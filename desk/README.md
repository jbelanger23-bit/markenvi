# desk

A copy-trade filter. Code fetches, Grok judges, copyfomo executes. Nobody does the other one's job.

```
0. BOARD     copyfomo /leaderboard, 30d             -> top traders on fomo
1. CODE CUT  trades, win rate, one-coin luck        -> a short list        (pick_traders.py, no network)
2. RANK      pick_traders.py -> Grok                -> up to 3 leaders     (leaders.json)
3. WATCH     copyfomo /alerts on every leader       -> each buy pings you
4. FACT CUT  rugcheck.py, code only, no model       -> open mint, danger flags, fat top 10 die here
5. JUDGE     check.py -> Grok, COPY or SKIP         -> one verdict, one reason, one risk
6. FILL      copyfomo, flat ticket, mirror exit     -> you place it by hand
```

The free passes run first. Only what survives pass 4 costs a Grok call. Only what Grok calls COPY costs money.

## Files

| file | role |
|---|---|
| `config.py` | every number on the desk. Retune here and nowhere else |
| `rugcheck.py` | fetch one Rugcheck report, trim it, apply the fact cut (pass 4) |
| `grok.py` | the only file that holds the xAI key and calls it. One call, JSON in, JSON out |
| `prompts.py` | TRADER_PROMPT (pass 2) and TOKEN_PROMPT (pass 5) |
| `pick_traders.py` | weekly: `traders.json` -> code cut -> Grok rank -> `leaders.json` |
| `check.py` | per buy: `python check.py <mint> <handle>` -> verdict, appended to `checks.csv` |

## Setup

```
pip install -r requirements.txt
export XAI_API_KEY="xai-..."        # mac / linux
setx XAI_API_KEY "xai-..."          # windows, then open a new terminal
```

Prove the key before anything else:

```
curl https://api.x.ai/v1/chat/completions \
  -H "Authorization: Bearer $XAI_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"grok-4","temperature":0,"messages":[{"role":"user","content":"reply with OK"}]}'
```

Rugcheck reads need no key. Only Solana mints have a report; anything else is a SKIP.

## Weekly: pick the leaders

Copy `traders.example.json` to `traders.json` and fill it from copyfomo `/leaderboard` plus each fomo profile. Then:

```
python pick_traders.py
```

Turn on copyfomo `/alerts` for every handle in `leaders.json`. Watching, not copying.

## Per buy: check the token

```
python check.py <mint> <handle>
```

Every run appends one line to `checks.csv` with its reason. COPY means you place the flat ticket in copyfomo; the stop loss and take profit live in copyfomo `/settings`, not here.

## Rules the code enforces

- A failed call is a SKIP: Rugcheck down, Grok down, bad JSON, missing key, handle not in `leaders.json`.
- COPY below `MIN_CONFIDENCE` is a SKIP. Any verdict that is not literally `COPY` is a SKIP.
- Temperature 0. Don't re-run until it says COPY; if you disagree, change a number in `config.py`.
- Grok never sees the key, the balance, the raw Rugcheck payload or your history. Arithmetic stays in code.
- `leaders.json`, `checks.csv` and `traders.json` are ignored by git on purpose.

## When it breaks

```
401 from xAI             -> bad key. Re-export XAI_API_KEY, open a new terminal.
400 / 404 from xAI       -> model name changed. Fix MODEL in config.py, nothing else.
429 from xAI             -> rate limit. Wait a minute, run again. Never loop it.
Grok returns a paragraph -> ValueError, logged, SKIP. Never parse prose into a trade.
rugcheck 404 / empty     -> token too fresh to index. SKIP. Fresh is not a pass.
rugcheck timeout         -> SKIP this one. A missing report is missing, not clean.
handle not in leaders    -> SKIP. Your alerts drifted, leaders.json didn't.
non-solana token         -> SKIP. Rugcheck here is Solana only. No report, no trade.
```

## Tests

```
python -m unittest discover -s tests -v
```

No network: Rugcheck and xAI are mocked. The tests pin the funnel rules above.

Every number in `config.py` is one person's tuning on one month of $50 tickets. Run `check.py` for a week and only read the log before putting a dollar behind a verdict. Most people who copy trade lose money. NFA, DYOR.
