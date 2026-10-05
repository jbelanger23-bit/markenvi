# Account-protection rules (hlagent)

Hard rules live in `hlagent/config.py:RiskConfig` and are enforced in
`hlagent/risk.py`. The learner cannot touch them. Defaults assume a personal
account under $500 on Hyperliquid perps.

## Per trade
| Rule | Default | Why |
|---|---|---|
| Risk per trade | 0.75% of equity | ~$3 on $400. 10 straight losses = -7.5%, survivable. |
| Max leverage | 3x notional/equity | HL lets you go 50x; you don't need it, and liquidation price must stay far beyond your stop. |
| Stop required | yes, exchange-held in live mode | A stop in the bot's memory dies with the bot. |
| Stop distance | 0.15% – 1.5% | Tighter is noise on HL's book; wider makes size too small to matter. |
| Target | ≥ 1.0R, default 1.5R | Learner may tune within [1.0, 2.5]. |
| Time stop | 12 bars (1h on 5m) | A trade that hasn't worked is a worse trade. |
| Breakeven | stop → entry after +0.8R | Removes most "winner turned loser" outcomes. |

## Per day / week / account
| Rule | Default |
|---|---|
| Max daily loss | 2.5% → no new trades until 00:00 UTC |
| Max weekly loss | 5% → no new trades until Monday |
| Max drawdown from high-water mark | 10% → **permanent halt**, needs manual reset of `state/risk.json` |
| Max trades/day | 6 |
| 3 consecutive losses | 2h cool-down |
| Max open positions | 1 |
| Weekend | size × 0.5 (thin books, 2–5% fake moves) |
| Macro event ±30 min | no new trades (add timestamps to `events_utc` in `state/risk.json`) |
| `HALT` file in cwd | kills all new entries instantly, no restart needed |

## Market-condition gates (per signal)
- Spread > 4 bps → skip.
- Top-5 depth either side < $50k → skip.
- |8h funding| > 0.05% → no trades in the crowded direction.
- Never trade imbalance *against* a TREND regime; never catch a knife (stop-run long) in a strong TREND_DOWN.
- VOLATILE regime → imbalance setups disabled (book is lying).

## Going live (in this order)
1. `HL_MODE=paper python -m hlagent run` for ≥ 100 closed trades. Review `python -m hlagent report`.
   Require: expectancy > +0.1R on at least one (setup, regime) arm with n ≥ 30 and max drawdown < 5%.
2. `HL_MODE=testnet` with an API wallet (never the main wallet key) for ≥ 2 weeks.
3. Live: create `ARM_LIVE`, set `HL_ACCOUNT_ADDRESS` + `HL_AGENT_PRIVATE_KEY` (API/agent wallet with
   trade-only permission, generated in HL "API" page or via Quantower's agent authorization), keep
   `HL_START_EQUITY` honest. Fund the HL account only with what the bot is allowed to lose.
4. Keep Quantower open on the same account: it shows the exchange-held stops and lets you flatten by hand.

## What the learner *is* allowed to do
- Decide whether to take a signal, per (setup, regime) arm, via Thompson sampling with a 10% explore rate.
- Tune `imbalance_threshold`, `sweep_min_penetration_pct`, `liq_volume_spike_mult`,
  `target_r_multiple`, `time_stop_bars` within the bounds in `config.TUNABLE`, every 20 closed trades,
  scored on the trade journal only, and only when the improvement is > 0.02R.
