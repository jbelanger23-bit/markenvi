"""Risk engine: the only component allowed to size a trade, and the gate every
order passes through. It is deliberately boring and un-tunable.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta

from .config import RiskConfig
from .data import Snapshot
from .signals import Signal


@dataclass
class RiskState:
    equity: float
    high_water: float
    day: str = ""
    week: str = ""
    day_start_equity: float = 0.0
    week_start_equity: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    cooldown_until: str = ""           # ISO
    halted_reason: str = ""
    events_utc: list[str] = field(default_factory=list)   # ISO timestamps of macro events


@dataclass
class Decision:
    allowed: bool
    size: float = 0.0               # base units
    notional: float = 0.0
    risk_usd: float = 0.0
    reason: str = ""


class RiskEngine:
    def __init__(self, cfg: RiskConfig, state_path: str, starting_equity: float):
        self.cfg = cfg
        self.path = state_path
        if os.path.exists(state_path):
            with open(state_path) as f:
                self.state = RiskState(**json.load(f))
        else:
            self.state = RiskState(equity=starting_equity, high_water=starting_equity)

    # ----- persistence -----
    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(asdict(self.state), f, indent=1)

    def _roll_periods(self, now: datetime):
        s = self.state
        day = now.strftime("%Y-%m-%d")
        week = now.strftime("%G-W%V")
        if s.day != day:
            s.day, s.day_start_equity, s.trades_today = day, s.equity, 0
        if s.week != week:
            s.week, s.week_start_equity = week, s.equity
        self.save()

    # ----- account updates -----
    def record_result(self, pnl_usd: float, now: datetime | None = None):
        now = now or datetime.now(timezone.utc)
        self._roll_periods(now)
        s = self.state
        s.equity += pnl_usd
        s.high_water = max(s.high_water, s.equity)
        s.trades_today += 1
        if pnl_usd < 0:
            s.consecutive_losses += 1
            if s.consecutive_losses >= self.cfg.max_consecutive_losses:
                s.cooldown_until = (now + timedelta(minutes=self.cfg.cooldown_minutes_after_streak)).isoformat()
        else:
            s.consecutive_losses = 0
        self.save()

    # ----- the gate -----
    def check(self, sig: Signal, snap: Snapshot, open_positions: int, now: datetime | None = None) -> Decision:
        now = now or datetime.now(timezone.utc)
        self._roll_periods(now)
        s, c = self.state, self.cfg

        if os.path.exists(c.halt_file):
            return Decision(False, reason=f"halt file {c.halt_file} present")
        if s.halted_reason:
            return Decision(False, reason=f"halted: {s.halted_reason}")

        dd = (s.high_water - s.equity) / s.high_water * 100
        if dd >= c.max_drawdown_pct:
            s.halted_reason = f"max drawdown {dd:.1f}% hit"; self.save()
            return Decision(False, reason=s.halted_reason)
        day_loss = (s.day_start_equity - s.equity) / s.day_start_equity * 100
        if day_loss >= c.max_daily_loss_pct:
            return Decision(False, reason=f"daily loss {day_loss:.2f}% >= {c.max_daily_loss_pct}%")
        week_loss = (s.week_start_equity - s.equity) / s.week_start_equity * 100
        if week_loss >= c.max_weekly_loss_pct:
            return Decision(False, reason=f"weekly loss {week_loss:.2f}% >= {c.max_weekly_loss_pct}%")
        if s.trades_today >= c.max_trades_per_day:
            return Decision(False, reason="max trades per day reached")
        if s.cooldown_until and now < datetime.fromisoformat(s.cooldown_until):
            return Decision(False, reason=f"cooldown after {s.consecutive_losses} losses until {s.cooldown_until}")
        if open_positions >= c.max_open_positions:
            return Decision(False, reason="max open positions")

        # event blackout
        for ev in s.events_utc:
            t = datetime.fromisoformat(ev)
            if abs((now - t).total_seconds()) <= c.no_trade_minutes_around_events * 60:
                return Decision(False, reason=f"macro event blackout around {ev}")

        # market-condition gates
        if snap.spread_bps > c.max_spread_bps:
            return Decision(False, reason=f"spread {snap.spread_bps:.1f}bps too wide")
        b, a = snap.depth_usd(5)
        if min(b, a) < c.min_book_depth_usd:
            return Decision(False, reason=f"book too thin (${min(b,a):,.0f})")
        if sig.side == "long" and snap.funding_8h > c.funding_extreme_8h_pct / 100:
            return Decision(False, reason="funding extreme positive: longs crowded")
        if sig.side == "short" and snap.funding_8h < -c.funding_extreme_8h_pct / 100:
            return Decision(False, reason="funding extreme negative: shorts crowded")

        # stop sanity, measured from the price we would actually fill at, not the
        # signal's reference price (the book can have moved through the stop already)
        fill_px = snap.asks[0][0] if sig.side == "long" else snap.bids[0][0]
        if abs(fill_px - sig.entry) / sig.entry > 0.003:
            return Decision(False, reason=f"signal stale: ref {sig.entry:.2f} vs fill {fill_px:.2f}")
        if c.require_stop_on_entry and sig.stop == sig.entry:
            return Decision(False, reason="no stop")
        if (sig.side == "long") != (sig.stop < fill_px):
            return Decision(False, reason="price already through stop")
        sig.entry = fill_px
        sd = sig.stop_distance_pct
        if sd < c.min_stop_distance_pct:
            return Decision(False, reason=f"stop {sd:.2f}% too tight")
        if sd > c.max_stop_distance_pct:
            return Decision(False, reason=f"stop {sd:.2f}% too wide")

        # size: fixed-fractional on stop distance, capped by leverage
        risk_usd = s.equity * c.max_risk_per_trade_pct / 100
        if now.weekday() >= 5:
            risk_usd *= c.weekend_size_multiplier
        size = risk_usd / abs(sig.entry - sig.stop)
        notional = size * sig.entry
        max_notional = s.equity * c.max_leverage
        if notional > max_notional:
            size, notional = max_notional / sig.entry, max_notional
            risk_usd = size * abs(sig.entry - sig.stop)
        return Decision(True, size=size, notional=notional, risk_usd=risk_usd,
                        reason=f"risk ${risk_usd:.2f} ({risk_usd/s.equity*100:.2f}%), lev {notional/s.equity:.2f}x")
