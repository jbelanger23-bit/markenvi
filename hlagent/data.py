"""Market data: a normalized snapshot plus two providers.

HyperliquidData pulls from the public /info endpoint via the official SDK.
ReplayData reads snapshots previously recorded to JSONL so the agent can be
backtested / unit-tested offline with identical code paths.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict, field
from typing import Iterator, Protocol


@dataclass
class Bar:
    t: int          # open time, ms
    o: float
    h: float
    l: float
    c: float
    v: float        # base volume


@dataclass
class Snapshot:
    symbol: str
    ts: int                                  # ms
    mid: float
    bids: list[tuple[float, float]]          # (px, sz) best first
    asks: list[tuple[float, float]]
    bars: list[Bar]                          # oldest -> newest, newest may be partial
    funding_8h: float                        # current funding rate per 8h, fraction
    open_interest: float                     # base units
    oi_prev: float | None = None             # OI one bar ago (for delta)
    recent_liq_notional: float = 0.0         # liquidation notional in last bar, if known
    extra: dict = field(default_factory=dict)

    @property
    def spread_bps(self) -> float:
        if not self.bids or not self.asks:
            return float("inf")
        return (self.asks[0][0] - self.bids[0][0]) / self.mid * 1e4

    def depth_usd(self, levels: int = 5) -> tuple[float, float]:
        b = sum(px * sz for px, sz in self.bids[:levels])
        a = sum(px * sz for px, sz in self.asks[:levels])
        return b, a

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json(cls, s: str) -> "Snapshot":
        d = json.loads(s)
        d["bars"] = [Bar(**b) for b in d["bars"]]
        d["bids"] = [tuple(x) for x in d["bids"]]
        d["asks"] = [tuple(x) for x in d["asks"]]
        return cls(**d)


class DataProvider(Protocol):
    def snapshot(self, symbol: str) -> Snapshot: ...


INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}


class HyperliquidData:
    """Live data from Hyperliquid's public info endpoint (no key needed)."""

    def __init__(self, interval: str = "5m", lookback_bars: int = 120, testnet: bool = False):
        from hyperliquid.info import Info
        from hyperliquid.utils import constants

        url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL
        self.info = Info(url, skip_ws=True)
        self.interval = interval
        self.lookback = lookback_bars
        self._last_oi: dict[str, float] = {}

    def snapshot(self, symbol: str) -> Snapshot:
        now = int(time.time() * 1000)
        start = now - INTERVAL_MS[self.interval] * self.lookback
        candles = self.info.candles_snapshot(symbol, self.interval, start, now)
        bars = [Bar(t=int(c["t"]), o=float(c["o"]), h=float(c["h"]), l=float(c["l"]),
                    c=float(c["c"]), v=float(c["v"])) for c in candles]

        book = self.info.l2_snapshot(symbol)
        bids = [(float(x["px"]), float(x["sz"])) for x in book["levels"][0]]
        asks = [(float(x["px"]), float(x["sz"])) for x in book["levels"][1]]

        meta, ctxs = self.info.meta_and_asset_ctxs()
        idx = next(i for i, a in enumerate(meta["universe"]) if a["name"] == symbol)
        ctx = ctxs[idx]
        oi = float(ctx["openInterest"])
        funding = float(ctx["funding"])  # HL reports hourly funding; normalise to 8h
        mid = float(ctx.get("midPx") or (bids[0][0] + asks[0][0]) / 2)

        snap = Snapshot(symbol=symbol, ts=now, mid=mid, bids=bids, asks=asks, bars=bars,
                        funding_8h=funding * 8, open_interest=oi,
                        oi_prev=self._last_oi.get(symbol))
        self._last_oi[symbol] = oi
        return snap


class ReplayData:
    """Replays JSONL snapshots recorded by `hlagent record`."""

    def __init__(self, path: str):
        self.path = path

    def __iter__(self) -> Iterator[Snapshot]:
        with open(self.path) as f:
            for line in f:
                if line.strip():
                    yield Snapshot.from_json(line)


def record(provider: DataProvider, symbols: tuple[str, ...], path: str, every_s: int = 60, n: int | None = None):
    """Append live snapshots to a JSONL file for later replay/backtesting."""
    i = 0
    with open(path, "a") as f:
        while n is None or i < n:
            for s in symbols:
                f.write(provider.snapshot(s).to_json() + "\n")
            f.flush()
            i += 1
            time.sleep(every_s)
