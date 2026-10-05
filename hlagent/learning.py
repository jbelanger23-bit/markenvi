"""Self-teaching loop.

Two mechanisms, both bounded so they can never override the risk engine:

1. Setup gating (Thompson sampling). Each (setup, regime) arm keeps a Beta
   posterior over "trade was profitable". A signal is only taken if a sample
   from its arm beats a floor. Arms that keep losing get traded less and less;
   arms with a real edge get traded more. Cold arms get explored at a small
   fixed rate so nothing is written off on 3 trades.

2. Parameter tuning (bounded random-restart hill climb). Every N closed trades
   the learner re-scores the trade journal under candidate parameter sets
   (within config.TUNABLE bounds) and keeps the best by expectancy. Candidates
   are evaluated on the journal, never on live money.

Everything is written to state/journal.jsonl so a human (or Claude) can audit
every decision.
"""
from __future__ import annotations

import json
import os
import random
from dataclasses import asdict, dataclass, replace

from .config import TUNABLE, StrategyConfig
from .execution import Fill
from .regime import STATES


@dataclass
class ArmStats:
    alpha: float = 1.0
    beta: float = 1.0
    n: int = 0
    pnl: float = 0.0
    r_sum: float = 0.0

    @property
    def win_rate(self):
        return self.alpha / (self.alpha + self.beta)

    @property
    def expectancy_r(self):
        return self.r_sum / self.n if self.n else 0.0


class Learner:
    SETUPS = ("imbalance", "stop_run", "liq_run")
    EXPLORE_RATE = 0.10
    TAKE_FLOOR = 0.45          # sampled P(win) must beat this (with ~1.5R targets, 0.45 is +EV)
    RETUNE_EVERY = 20

    def __init__(self, state_dir: str, cfg: StrategyConfig, seed: int | None = None):
        self.dir = state_dir
        os.makedirs(state_dir, exist_ok=True)
        self.journal = os.path.join(state_dir, "journal.jsonl")
        self.arms_path = os.path.join(state_dir, "arms.json")
        self.params_path = os.path.join(state_dir, "params.json")
        self.rng = random.Random(seed)
        self.arms: dict[str, ArmStats] = {f"{s}|{r}": ArmStats() for s in self.SETUPS for r in STATES}
        if os.path.exists(self.arms_path):
            with open(self.arms_path) as f:
                self.arms.update({k: ArmStats(**v) for k, v in json.load(f).items()})
        self.cfg = cfg
        if os.path.exists(self.params_path):
            with open(self.params_path) as f:
                self.cfg = replace(cfg, **json.load(f))
        self._closed_since_tune = 0

    # ---- gating ----
    def should_take(self, setup: str, regime: str) -> tuple[bool, float, str]:
        arm = self.arms[f"{setup}|{regime}"]
        if self.rng.random() < self.EXPLORE_RATE:
            return True, arm.win_rate, "explore"
        sample = self.rng.betavariate(arm.alpha, arm.beta)
        ok = sample >= self.TAKE_FLOOR
        return ok, sample, f"thompson sample {sample:.2f} (n={arm.n}, wr={arm.win_rate:.2f}, exp={arm.expectancy_r:+.2f}R)"

    # ---- feedback ----
    def record(self, fill: Fill, regime: str, extra: dict | None = None):
        pos = fill.position
        key = f"{pos.setup}|{regime}"
        arm = self.arms[key]
        r = fill.pnl_usd / max(pos.initial_risk * pos.size, 1e-9)
        if fill.pnl_usd > 0:
            arm.alpha += 1
        else:
            arm.beta += 1
        arm.n += 1
        arm.pnl += fill.pnl_usd
        arm.r_sum += r
        with open(self.arms_path, "w") as f:
            json.dump({k: asdict(v) for k, v in self.arms.items()}, f, indent=1)
        row = {"ts": pos.opened_ts, "setup": pos.setup, "regime": regime, "symbol": pos.symbol,
               "side": pos.side, "entry": pos.entry, "stop": pos.stop, "target": pos.target,
               "exit": fill.exit_px, "exit_reason": fill.exit_reason, "pnl": round(fill.pnl_usd, 4),
               "r": round(r, 3), "bars_held": pos.bars_held, "features": pos.meta,
               "params": {k: getattr(self.cfg, k) for k in TUNABLE}, **(extra or {})}
        with open(self.journal, "a") as f:
            f.write(json.dumps(row) + "\n")
        self._closed_since_tune += 1
        if self._closed_since_tune >= self.RETUNE_EVERY:
            self._closed_since_tune = 0
            self.retune()

    # ---- parameter tuning ----
    def _load_journal(self) -> list[dict]:
        if not os.path.exists(self.journal):
            return []
        with open(self.journal) as f:
            return [json.loads(l) for l in f if l.strip()]

    @staticmethod
    def _score(rows: list[dict], params: dict) -> float:
        """Counterfactual expectancy: a trade 'would have been taken' under candidate
        params if its recorded features clear the candidate thresholds. Trades that
        would be filtered out contribute 0. Target multiple scales R linearly as a
        crude proxy (a target that was hit at 1.5R is assumed hit at <=1.5R, and
        assumed stopped-at-time beyond)."""
        if not rows:
            return 0.0
        total = 0.0
        for r in rows:
            f = r.get("features", {})
            if r["setup"] == "imbalance" and abs(f.get("imbalance", 1)) < params["imbalance_threshold"]:
                continue
            if r["setup"] == "liq_run" and f.get("vol_mult", 99) < params["liq_volume_spike_mult"]:
                continue
            rr = r["r"]
            if r["exit_reason"] == "target":
                rr = min(rr, params["target_r_multiple"])
            total += rr
        return total / len(rows)

    def retune(self, candidates: int = 40) -> dict:
        rows = self._load_journal()[-200:]
        if len(rows) < self.RETUNE_EVERY:
            return {}
        current = {k: getattr(self.cfg, k) for k in TUNABLE}
        best, best_s = current, self._score(rows, current)
        for _ in range(candidates):
            cand = dict(current)
            for k, (lo, hi) in TUNABLE.items():
                if self.rng.random() < 0.5:
                    step = (hi - lo) * 0.15
                    v = current[k] + self.rng.uniform(-step, step)
                    cand[k] = int(round(min(hi, max(lo, v)))) if isinstance(lo, int) else min(hi, max(lo, v))
            s = self._score(rows, cand)
            if s > best_s + 0.02:        # require a real improvement, avoid noise-chasing
                best, best_s = cand, s
        if best != current:
            self.cfg = replace(self.cfg, **best)
            with open(self.params_path, "w") as f:
                json.dump(best, f, indent=1)
        return {"params": best, "expectancy_r": best_s, "n": len(rows)}

    def report(self) -> str:
        lines = ["setup|regime            n   wr    expR   pnl"]
        for k, a in sorted(self.arms.items(), key=lambda kv: -kv[1].n):
            if a.n:
                lines.append(f"{k:22s} {a.n:3d}  {a.win_rate:.2f}  {a.expectancy_r:+.2f}  {a.pnl:+.2f}")
        lines.append(f"params: {json.dumps({k: getattr(self.cfg, k) for k in TUNABLE})}")
        return "\n".join(lines)
