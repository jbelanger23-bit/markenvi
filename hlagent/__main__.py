"""CLI.

  python -m hlagent run                 # paper-trade live Hyperliquid data (default)
  python -m hlagent record out.jsonl    # record snapshots for replay
  python -m hlagent replay out.jsonl    # run the agent over recorded snapshots
  python -m hlagent report              # learner arms + current params
  python -m hlagent regime              # one-shot regime read for each symbol
"""
from __future__ import annotations

import argparse
import json
import logging
import time

from .agent import Agent
from .config import Settings
from .data import HyperliquidData, ReplayData, record
from .regime import classify


def main():
    ap = argparse.ArgumentParser(prog="hlagent")
    ap.add_argument("cmd", choices=["run", "record", "replay", "report", "regime"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--every", type=int, default=60, help="seconds between snapshots")
    ap.add_argument("--n", type=int, default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    s = Settings()

    if a.cmd == "report":
        print(Agent(s).learner.report()); return
    if a.cmd == "replay":
        agent = Agent(s)
        for snap in ReplayData(a.path):
            r = agent.step(snap)
            for act in r["actions"]:
                print(act)
        print(agent.learner.report()); print(f"equity: {agent.risk.state.equity:.2f}"); return

    data = HyperliquidData(s.strategy.bar_interval, testnet=(s.mode == "testnet"))
    if a.cmd == "regime":
        for sym in s.strategy.symbols:
            print(sym, json.dumps(classify(data.snapshot(sym)).to_dict(), indent=1)); return
    if a.cmd == "record":
        record(data, s.strategy.symbols, a.path, a.every, a.n); return

    agent = Agent(s)
    print(f"mode={s.mode} equity={agent.risk.state.equity:.2f} symbols={s.strategy.symbols}")
    i = 0
    while a.n is None or i < a.n:
        for sym in s.strategy.symbols:
            try:
                r = agent.step(data.snapshot(sym))
                rg = r["regime"]
                print(f"{sym:4s} {rg['state']:11s} conf {rg['confidence']:.2f} bias {rg['bias']:+.1f} "
                      f"adx {rg['adx']:.0f} vol {rg['vol_rank']:.2f} fund {rg['funding_8h']*100:+.3f}%  "
                      + " ; ".join(r["actions"]))
            except Exception as e:  # keep the loop alive; the exchange holds stops in live mode
                logging.exception("step failed for %s: %s", sym, e)
        i += 1
        time.sleep(a.every)


if __name__ == "__main__":
    main()
