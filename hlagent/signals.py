"""Entry signals: order-book imbalance, stop runs, liquidation runs.

Each detector returns a Signal or None. Signals carry a stop and target so the
risk engine can size them; they never size themselves.
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass, field

from .config import StrategyConfig
from .data import Snapshot
from .regime import Regime


@dataclass
class Signal:
    setup: str                  # "imbalance" | "stop_run" | "liq_run"
    symbol: str
    side: str                   # "long" | "short"
    entry: float
    stop: float
    target: float
    strength: float             # 0..1
    reason: str
    features: dict = field(default_factory=dict)

    @property
    def stop_distance_pct(self) -> float:
        return abs(self.entry - self.stop) / self.entry * 100

    @property
    def r_multiple(self) -> float:
        return abs(self.target - self.entry) / max(abs(self.entry - self.stop), 1e-12)


def book_imbalance(snap: Snapshot, levels: int) -> float:
    b, a = snap.depth_usd(levels)
    return (b - a) / (b + a) if (b + a) else 0.0


class ImbalanceDetector:
    """Persistent top-of-book imbalance in the direction of the regime bias.

    Requires the imbalance to hold for `confirm_ticks` consecutive snapshots so a
    single spoofed level does not trigger it.
    """

    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg
        self._streak: dict[str, tuple[int, int]] = {}   # symbol -> (sign, count)

    def detect(self, snap: Snapshot, regime: Regime) -> Signal | None:
        imb = book_imbalance(snap, self.cfg.imbalance_levels)
        sign = 1 if imb > self.cfg.imbalance_threshold else -1 if imb < -self.cfg.imbalance_threshold else 0
        psign, cnt = self._streak.get(snap.symbol, (0, 0))
        cnt = cnt + 1 if sign and sign == psign else (1 if sign else 0)
        self._streak[snap.symbol] = (sign, cnt)
        if not sign or cnt < self.cfg.imbalance_confirm_ticks:
            return None
        # only trade with the regime bias, never against a trend
        if regime.state.startswith("TREND") and (regime.bias > 0) != (sign > 0):
            return None
        if regime.state in ("VOLATILE",):
            return None
        atr = regime.atr_pct / 100 * snap.mid
        if sign > 0:
            entry = snap.asks[0][0]
            stop = entry - 1.2 * atr
            target = entry + self.cfg.target_r_multiple * (entry - stop)
            side = "long"
        else:
            entry = snap.bids[0][0]
            stop = entry + 1.2 * atr
            target = entry - self.cfg.target_r_multiple * (stop - entry)
            side = "short"
        return Signal("imbalance", snap.symbol, side, entry, stop, target,
                      strength=min(1.0, abs(imb) / 0.6),
                      reason=f"book imbalance {imb:+.2f} for {cnt} ticks in {regime.state}",
                      features={"imbalance": imb, "ticks": cnt, "regime": regime.state})


class StopRunDetector:
    """Sweep of a prior swing high/low that is reclaimed within N bars.

    Classic stop hunt: price pokes beyond the level (liquidity grab), fails to
    hold, closes back inside. Trade the reversal back toward the range.
    """

    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg

    def detect(self, snap: Snapshot, regime: Regime) -> Signal | None:
        bars = snap.bars[:-1]
        n = self.cfg.sweep_lookback_bars
        if len(bars) < n + self.cfg.sweep_max_bars_to_reclaim + 2:
            return None
        ref = bars[-(n + self.cfg.sweep_max_bars_to_reclaim):-self.cfg.sweep_max_bars_to_reclaim]
        hi, lo = max(b.h for b in ref), min(b.l for b in ref)
        recent = bars[-self.cfg.sweep_max_bars_to_reclaim:]
        last = recent[-1]
        pen = self.cfg.sweep_min_penetration_pct / 100

        # sweep below low then close back above -> long
        if any(b.l < lo * (1 - pen) for b in recent) and last.c > lo:
            wick_low = min(b.l for b in recent)
            entry, stop = last.c, wick_low * (1 - 0.0005)
            target = entry + self.cfg.target_r_multiple * (entry - stop)
            if regime.state == "TREND_DOWN" and regime.confidence > 0.7:
                return None  # don't catch knives in a strong downtrend
            return Signal("stop_run", snap.symbol, "long", entry, stop, target,
                          strength=0.6 + min(0.4, (lo - wick_low) / lo / pen * 0.1),
                          reason=f"swept low {lo:.2f} to {wick_low:.2f}, reclaimed",
                          features={"level": lo, "wick": wick_low, "regime": regime.state})
        # sweep above high then close back below -> short
        if any(b.h > hi * (1 + pen) for b in recent) and last.c < hi:
            wick_high = max(b.h for b in recent)
            entry, stop = last.c, wick_high * (1 + 0.0005)
            target = entry - self.cfg.target_r_multiple * (stop - entry)
            if regime.state == "TREND_UP" and regime.confidence > 0.7:
                return None
            return Signal("stop_run", snap.symbol, "short", entry, stop, target,
                          strength=0.6 + min(0.4, (wick_high - hi) / hi / pen * 0.1),
                          reason=f"swept high {hi:.2f} to {wick_high:.2f}, rejected",
                          features={"level": hi, "wick": wick_high, "regime": regime.state})
        return None


class LiquidationRunDetector:
    """Cascade exhaustion: volume spike + OI drop + large bar, then fade it.

    Only fires once the flush bar has closed and the next bar shows absorption
    (closes back inside the flush bar's range by > 30%).
    """

    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg

    def detect(self, snap: Snapshot, regime: Regime) -> Signal | None:
        bars = snap.bars[:-1]
        if len(bars) < 25:
            return None
        flush, confirm = bars[-2], bars[-1]
        med = st.median(b.v for b in bars[-22:-2]) or 1e-9
        oi_drop = regime.oi_delta_pct < -self.cfg.liq_oi_drop_pct or snap.recent_liq_notional > 0
        rng = flush.h - flush.l
        if flush.v < self.cfg.liq_volume_spike_mult * med or not oi_drop or rng <= 0:
            return None
        if flush.c < flush.o:  # long liquidations -> look for a long
            retrace = (confirm.c - flush.l) / rng
            if retrace < 0.3:
                return None
            entry, stop = confirm.c, flush.l * (1 - 0.001)
            target = entry + self.cfg.target_r_multiple * (entry - stop)
            return Signal("liq_run", snap.symbol, "long", entry, stop, target,
                          strength=min(1.0, flush.v / med / 6),
                          reason=f"long flush vol x{flush.v/med:.1f}, retrace {retrace:.0%}",
                          features={"vol_mult": flush.v / med, "retrace": retrace, "regime": regime.state})
        else:  # short squeeze -> look for a short
            retrace = (flush.h - confirm.c) / rng
            if retrace < 0.3:
                return None
            entry, stop = confirm.c, flush.h * (1 + 0.001)
            target = entry - self.cfg.target_r_multiple * (stop - entry)
            return Signal("liq_run", snap.symbol, "short", entry, stop, target,
                          strength=min(1.0, flush.v / med / 6),
                          reason=f"short squeeze vol x{flush.v/med:.1f}, retrace {retrace:.0%}",
                          features={"vol_mult": flush.v / med, "retrace": retrace, "regime": regime.state})
