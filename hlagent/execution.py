"""Brokers. PaperBroker simulates fills against live/replayed snapshots.
LiveBroker wraps the official SDK and is only constructed when HL_MODE=live or
testnet AND the ARM_LIVE file exists.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field, asdict

from .data import Snapshot
from .signals import Signal

TAKER_FEE = 0.00045   # HL base tier; paper mode applies it both ways
SLIPPAGE_BPS = 1.0


@dataclass
class Position:
    id: str
    symbol: str
    side: str
    size: float
    entry: float
    stop: float
    target: float
    setup: str
    opened_ts: int
    bars_held: int = 0
    breakeven_set: bool = False
    last_bar_t: int = 0
    initial_risk: float = 0.0   # |entry - original stop|, for R accounting

    def __post_init__(self):
        if not self.initial_risk:
            self.initial_risk = abs(self.entry - self.stop)
    meta: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


@dataclass
class Fill:
    position: Position
    exit_px: float
    pnl_usd: float
    fees_usd: float
    exit_reason: str


class PaperBroker:
    def __init__(self):
        self.positions: dict[str, Position] = {}

    def open(self, sig: Signal, size: float, snap: Snapshot) -> Position:
        slip = snap.mid * SLIPPAGE_BPS / 1e4
        px = (snap.asks[0][0] + slip) if sig.side == "long" else (snap.bids[0][0] - slip)
        pos = Position(uuid.uuid4().hex[:8], sig.symbol, sig.side, size, px, sig.stop, sig.target,
                       sig.setup, snap.ts, meta={"reason": sig.reason, **sig.features})
        self.positions[pos.id] = pos
        return pos

    def _close(self, pos: Position, px: float, reason: str) -> Fill:
        sign = 1 if pos.side == "long" else -1
        gross = sign * (px - pos.entry) * pos.size
        fees = (pos.entry + px) * pos.size * TAKER_FEE
        del self.positions[pos.id]
        return Fill(pos, px, gross - fees, fees, reason)

    def mark(self, snap: Snapshot, time_stop_bars: int, breakeven_at_r: float) -> list[Fill]:
        """Check stops/targets against the latest completed bar's high/low."""
        fills = []
        for pos in list(self.positions.values()):
            if pos.symbol != snap.symbol or len(snap.bars) < 3:
                continue
            bar = snap.bars[-2]                      # last completed bar
            interval = snap.bars[-1].t - bar.t
            if bar.t + interval <= pos.opened_ts:    # closed before we entered
                continue
            if bar.t <= pos.last_bar_t:              # already evaluated this bar
                continue
            pos.last_bar_t = bar.t
            pos.bars_held += 1
            if pos.side == "long":
                if bar.l <= pos.stop:
                    fills.append(self._close(pos, pos.stop, "stop")); continue
                if bar.h >= pos.target:
                    fills.append(self._close(pos, pos.target, "target")); continue
                if not pos.breakeven_set and bar.h >= pos.entry + breakeven_at_r * pos.initial_risk:
                    pos.stop, pos.breakeven_set = pos.entry, True
            else:
                if bar.h >= pos.stop:
                    fills.append(self._close(pos, pos.stop, "stop")); continue
                if bar.l <= pos.target:
                    fills.append(self._close(pos, pos.target, "target")); continue
                if not pos.breakeven_set and bar.l <= pos.entry - breakeven_at_r * pos.initial_risk:
                    pos.stop, pos.breakeven_set = pos.entry, True
            if pos.bars_held >= time_stop_bars:
                fills.append(self._close(pos, bar.c, "time"))
        return fills

    def flatten(self, snap: Snapshot, reason="flatten") -> list[Fill]:
        return [self._close(p, snap.mid, reason) for p in list(self.positions.values()) if p.symbol == snap.symbol]


class LiveBroker(PaperBroker):
    """Real orders via the official SDK. Entry is a market IOC; stop and target are
    placed immediately as reduce-only trigger orders so the exchange holds the
    stop even if this process dies.
    """

    def __init__(self, account_address: str, agent_private_key: str, testnet: bool):
        if not os.path.exists("ARM_LIVE"):
            raise RuntimeError("Refusing live trading: create a file named ARM_LIVE to arm.")
        import eth_account
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants
        super().__init__()
        url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL
        wallet = eth_account.Account.from_key(agent_private_key)
        self.ex = Exchange(wallet, url, account_address=account_address)

    def open(self, sig: Signal, size: float, snap: Snapshot) -> Position:
        is_buy = sig.side == "long"
        size = round(size, 4)
        res = self.ex.market_open(sig.symbol, is_buy, size, None, 0.01)
        statuses = res["response"]["data"]["statuses"]
        filled = next((s["filled"] for s in statuses if "filled" in s), None)
        if not filled:
            raise RuntimeError(f"entry not filled: {res}")
        px = float(filled["avgPx"])
        # exchange-held stop and target, reduce-only
        self.ex.order(sig.symbol, not is_buy, size, sig.stop,
                      {"trigger": {"triggerPx": sig.stop, "isMarket": True, "tpsl": "sl"}}, reduce_only=True)
        self.ex.order(sig.symbol, not is_buy, size, sig.target,
                      {"trigger": {"triggerPx": sig.target, "isMarket": True, "tpsl": "tp"}}, reduce_only=True)
        pos = Position(uuid.uuid4().hex[:8], sig.symbol, sig.side, size, px, sig.stop, sig.target,
                       sig.setup, snap.ts, meta={"reason": sig.reason, **sig.features})
        self.positions[pos.id] = pos
        return pos

    def _close(self, pos: Position, px: float, reason: str) -> Fill:
        # Stop/target are exchange-held; for time/flatten exits send a reduce-only market close.
        if reason in ("time", "flatten"):
            self.ex.market_close(pos.symbol)
        return super()._close(pos, px, reason)
