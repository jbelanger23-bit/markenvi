"""Crypto market-state classifier (the MARKENVI model adapted to perps).

States: TREND_UP, TREND_DOWN, RANGE, VOLATILE, LIQUIDATION.
Each snapshot gets a state, a confidence 0..1 and a directional bias -1..1.
A transition matrix is kept on disk so P(next state | current) can be reported
and used as a feature by the learner.
"""
from __future__ import annotations

import json
import os
import statistics as st
from dataclasses import dataclass, asdict

from .data import Snapshot

STATES = ("TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE", "LIQUIDATION")


@dataclass
class Regime:
    state: str
    confidence: float
    bias: float                 # -1 short .. +1 long
    adx: float
    atr_pct: float
    vol_rank: float             # realized vol percentile vs lookback
    oi_delta_pct: float
    funding_8h: float
    notes: list[str]

    def to_dict(self):
        return asdict(self)


def _ema(xs, n):
    k = 2 / (n + 1)
    e = xs[0]
    out = []
    for x in xs:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def _atr(bars, n=14):
    trs = []
    for i in range(1, len(bars)):
        b, p = bars[i], bars[i - 1]
        trs.append(max(b.h - b.l, abs(b.h - p.c), abs(b.l - p.c)))
    return _ema(trs, n)[-1] if trs else 0.0


def _adx(bars, n=14):
    if len(bars) < n * 2 + 1:
        return 0.0
    plus, minus, trs = [], [], []
    for i in range(1, len(bars)):
        b, p = bars[i], bars[i - 1]
        up, dn = b.h - p.h, p.l - b.l
        plus.append(up if up > dn and up > 0 else 0.0)
        minus.append(dn if dn > up and dn > 0 else 0.0)
        trs.append(max(b.h - b.l, abs(b.h - p.c), abs(b.l - p.c)))
    atr = _ema(trs, n)
    pdi = [100 * p / a if a else 0 for p, a in zip(_ema(plus, n), atr)]
    mdi = [100 * m / a if a else 0 for m, a in zip(_ema(minus, n), atr)]
    dx = [100 * abs(p - m) / (p + m) if (p + m) else 0 for p, m in zip(pdi, mdi)]
    return _ema(dx, n)[-1]


def classify(snap: Snapshot, lookback_vol: int = 96) -> Regime:
    bars = snap.bars[:-1] if len(snap.bars) > 2 else snap.bars  # drop partial bar
    notes: list[str] = []
    closes = [b.c for b in bars]
    if len(closes) < 30:
        return Regime("RANGE", 0.2, 0.0, 0, 0, 0.5, 0, snap.funding_8h, ["insufficient bars"])

    atr = _atr(bars)
    atr_pct = atr / closes[-1] * 100
    adx = _adx(bars)

    # realized vol rank: 12-bar stdev of returns vs rolling history
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    win = 12
    rv = [st.pstdev(rets[i - win:i]) for i in range(win, len(rets) + 1)]
    rv_hist = rv[-lookback_vol:]
    vol_rank = sum(1 for x in rv_hist if x <= rv[-1]) / len(rv_hist)

    ema20, ema50 = _ema(closes, 20)[-1], _ema(closes, 50)[-1]
    # structure: higher highs / higher lows over last 3 swings (simple 8-bar pivots)
    hh = all(bars[-i].h >= bars[-i - 8].h for i in (1, 9)) if len(bars) > 18 else False
    hl = all(bars[-i].l >= bars[-i - 8].l for i in (1, 9)) if len(bars) > 18 else False
    lh = all(bars[-i].h <= bars[-i - 8].h for i in (1, 9)) if len(bars) > 18 else False
    ll = all(bars[-i].l <= bars[-i - 8].l for i in (1, 9)) if len(bars) > 18 else False

    oi_delta = 0.0
    if snap.oi_prev:
        oi_delta = (snap.open_interest - snap.oi_prev) / snap.oi_prev * 100

    vols = [b.v for b in bars]
    med_vol = st.median(vols[-20:]) or 1e-9
    last = bars[-1]
    last_range_pct = (last.h - last.l) / last.c * 100

    # --- LIQUIDATION: volume spike + OI drop + big bar ---
    if (last.v > 3 * med_vol and oi_delta < -0.8 and last_range_pct > 2 * atr_pct) or snap.recent_liq_notional > 0:
        direction = 1.0 if last.c < last.o else -1.0  # fade the flush after it exhausts
        notes.append(f"vol x{last.v/med_vol:.1f}, OI {oi_delta:+.2f}%, bar {last_range_pct:.2f}%")
        return Regime("LIQUIDATION", 0.7, direction * 0.4, adx, atr_pct, vol_rank, oi_delta, snap.funding_8h, notes)

    # --- VOLATILE: vol in top decile but no structure ---
    if vol_rank > 0.9 and adx < 20:
        notes.append("vol rank > 0.9 without trend")
        return Regime("VOLATILE", 0.6, 0.0, adx, atr_pct, vol_rank, oi_delta, snap.funding_8h, notes)

    # --- TREND ---
    if adx >= 25 or (ema20 > ema50 and hh and hl) or (ema20 < ema50 and lh and ll):
        up = ema20 > ema50
        conf = min(1.0, 0.5 + adx / 100 + (0.2 if (hh and hl) or (lh and ll) else 0))
        bias = 0.7 if up else -0.7
        # crowded funding against the trend reduces confidence (squeeze risk)
        if up and snap.funding_8h > 0.03:
            conf -= 0.15; notes.append("crowded longs (funding)")
        if not up and snap.funding_8h < -0.03:
            conf -= 0.15; notes.append("crowded shorts (funding)")
        # OI rising with price = leveraged trend (fragile); OI falling with price up = spot-driven
        if up and oi_delta > 0.5:
            notes.append("leveraged rally")
        if up and oi_delta < -0.5:
            notes.append("spot-driven rally"); conf += 0.05
        return Regime("TREND_UP" if up else "TREND_DOWN", max(0.3, conf), bias, adx, atr_pct, vol_rank, oi_delta, snap.funding_8h, notes)

    # --- RANGE ---
    conf = 0.5 + (0.2 if adx < 18 else 0) + (0.2 if vol_rank < 0.4 else 0)
    notes.append("inside structure, low ADX")
    return Regime("RANGE", min(conf, 0.9), 0.0, adx, atr_pct, vol_rank, oi_delta, snap.funding_8h, notes)


class TransitionMatrix:
    """Markov transition counts between regime states, persisted as JSON."""

    def __init__(self, path: str):
        self.path = path
        self.counts = {a: {b: 1 for b in STATES} for a in STATES}  # Laplace prior
        self.last: dict[str, str] = {}
        if os.path.exists(path):
            with open(path) as f:
                d = json.load(f)
                self.counts, self.last = d["counts"], d["last"]

    def observe(self, symbol: str, state: str):
        prev = self.last.get(symbol)
        if prev:
            self.counts[prev][state] += 1
        self.last[symbol] = state
        with open(self.path, "w") as f:
            json.dump({"counts": self.counts, "last": self.last}, f)

    def next_probs(self, state: str) -> dict[str, float]:
        row = self.counts[state]
        tot = sum(row.values())
        return {k: v / tot for k, v in row.items()}
