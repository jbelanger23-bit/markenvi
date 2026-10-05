"""Synthetic market generator for offline tests: produces Snapshot sequences with
controllable regimes (trend / range / flush) and an order book."""
from __future__ import annotations

import random

from hlagent.data import Bar, Snapshot

BAR_MS = 300_000


def make_bars(n: int, start_px: float = 60_000, drift: float = 0.0, vol: float = 0.002,
              seed: int = 1, t0: int = 1_700_000_000_000) -> list[Bar]:
    rng = random.Random(seed)
    bars, px = [], start_px
    for i in range(n):
        o = px
        c = o * (1 + drift + rng.gauss(0, vol))
        h = max(o, c) * (1 + abs(rng.gauss(0, vol / 2)))
        l = min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
        bars.append(Bar(t0 + i * BAR_MS, o, h, l, c, 50 + rng.random() * 20))
        px = c
    return bars


def book(mid: float, bid_mult: float = 1.0, ask_mult: float = 1.0, levels: int = 10, tick: float = 1.0):
    bids = [(mid - tick * (i + 1), 2.0 * bid_mult) for i in range(levels)]
    asks = [(mid + tick * (i + 1), 2.0 * ask_mult) for i in range(levels)]
    return bids, asks


def snap(bars: list[Bar], symbol="BTC", bid_mult=1.0, ask_mult=1.0, funding=0.0001,
         oi=10_000.0, oi_prev=None, liq=0.0) -> Snapshot:
    mid = bars[-1].c
    b, a = book(mid, bid_mult, ask_mult)
    return Snapshot(symbol, bars[-1].t + BAR_MS, mid, b, a, bars, funding, oi, oi_prev, liq)
