"""The decision loop: snapshot -> regime -> signals -> learner gate -> risk gate -> broker."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from .config import Settings
from .data import Snapshot
from .execution import PaperBroker, LiveBroker
from .learning import Learner
from .regime import classify, TransitionMatrix
from .risk import RiskEngine
from .signals import ImbalanceDetector, StopRunDetector, LiquidationRunDetector

log = logging.getLogger("hlagent")


class Agent:
    def __init__(self, settings: Settings, broker=None, seed: int | None = None):
        self.s = settings
        os.makedirs(settings.state_dir, exist_ok=True)
        self.risk = RiskEngine(settings.risk, os.path.join(settings.state_dir, "risk.json"), settings.starting_equity)
        self.learner = Learner(settings.state_dir, settings.strategy, seed=seed)
        self.tm = TransitionMatrix(os.path.join(settings.state_dir, "transitions.json"))
        cfg = self.learner.cfg
        self.detectors = [ImbalanceDetector(cfg), StopRunDetector(cfg), LiquidationRunDetector(cfg)]
        if broker is not None:
            self.broker = broker
        elif settings.mode in ("live", "testnet"):
            self.broker = LiveBroker(settings.account_address, settings.agent_private_key, settings.mode == "testnet")
        else:
            self.broker = PaperBroker()
        self._regime_at_open: dict[str, str] = {}

    def step(self, snap: Snapshot, now: datetime | None = None) -> dict:
        now = now or datetime.fromtimestamp(snap.ts / 1000, tz=timezone.utc)
        regime = classify(snap)
        self.tm.observe(snap.symbol, regime.state)
        out = {"symbol": snap.symbol, "regime": regime.to_dict(), "next": self.tm.next_probs(regime.state), "actions": []}

        # manage open positions first
        cfg = self.learner.cfg
        for fill in self.broker.mark(snap, cfg.time_stop_bars, cfg.breakeven_at_r):
            self.risk.record_result(fill.pnl_usd, now)
            self.learner.record(fill, self._regime_at_open.pop(fill.position.id, regime.state),
                                {"equity_after": round(self.risk.state.equity, 2)})
            out["actions"].append(f"CLOSE {fill.position.side} {snap.symbol} @ {fill.exit_px:.2f} "
                                  f"{fill.exit_reason} pnl {fill.pnl_usd:+.2f}")
            log.info(out["actions"][-1])

        # look for a new entry
        for det in self.detectors:
            det.cfg = cfg
            sig = det.detect(snap, regime)
            if not sig:
                continue
            take, p, why = self.learner.should_take(sig.setup, regime.state)
            if not take:
                out["actions"].append(f"SKIP {sig.setup} {sig.side}: learner {why}")
                continue
            dec = self.risk.check(sig, snap, len(self.broker.positions), now)
            if not dec.allowed:
                out["actions"].append(f"BLOCK {sig.setup} {sig.side}: {dec.reason}")
                continue
            pos = self.broker.open(sig, dec.size, snap)
            self._regime_at_open[pos.id] = regime.state
            out["actions"].append(f"OPEN {sig.side} {snap.symbol} {dec.size:.5f} @ {pos.entry:.2f} "
                                  f"stop {sig.stop:.2f} tgt {sig.target:.2f} [{sig.setup}] {sig.reason} | {dec.reason}")
            log.info(out["actions"][-1])
            break  # one entry per step
        return out
