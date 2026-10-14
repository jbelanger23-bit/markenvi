# desk

A copy-trade filter you run on your own computer. Code fetches, Claude judges, you execute in copyfomo. Nobody does the other one's job, and nothing in this app moves money.

```
0. BOARD     copyfomo /leaderboard, 30d             -> top traders on fomo
1. CODE CUT  trades, win rate, one-coin luck        -> a short list        (no network)
2. RANK      Claude ranks the survivors             -> up to 3 leaders
3. WATCH     copyfomo /alerts on every leader       -> each buy pings you
4. FACT CUT  Rugcheck facts, code only, no model    -> open mint, danger flags, fat top 10 die here
5. JUDGE     Claude: COPY or SKIP + confidence      -> one verdict, one reason, one risk
6. FILL      you, in copyfomo, flat ticket          -> position live, TP / SL armed there
```

The free passes run first. Only what survives pass 4 costs a Claude call. Only what Claude calls COPY costs money, and only if you place it.

## Start it (Windows)

1. Install Python 3.10 or newer from python.org. Tick **Add python.exe to PATH** in the installer.
2. Double-click `start.bat`. The first run creates a virtual environment and installs two packages. Then your browser opens at `http://127.0.0.1:8787/`.
3. Open **Settings**, paste your Anthropic API key (create one at console.anthropic.com), save.

Close the black window to stop the app. Mac or Linux: `./start.sh`.

The server listens on 127.0.0.1 only. Nothing is reachable from outside your machine.

## Use it

**Leaders tab, once a week.** Paste a JSON list of traders from copyfomo `/leaderboard` with the stats from each fomo profile (handle, pnl_30d_usd, pnl_7d_usd, closed_trades, win_rate, top_coin_share, avg_hold_minutes, followers). Run the pick. Code cuts first, Claude ranks what is left, the result is saved as your leaders. Turn on copyfomo `/alerts` for each one. Watching, not copying.

**Check a buy, every alert.** Paste the mint, pick the leader, run the funnel. You get COPY or SKIP, the reason, the main risk, where in the funnel it stopped, and the trimmed Rugcheck facts. A COPY means you place the flat ticket in copyfomo yourself. Stop loss and take profit live in copyfomo `/settings`.

**Paper, for the test week.** Paper mode is on by default. Every COPY opens a simulated position for the flat ticket at the token's live price from DexScreener (free, no key, Solana only). copyfomo's guards run in code before the fill: max positions, no re-buy within 24h of a sell, first buy wins. The exit rules run on every refresh of the Paper tab: stop loss at -50% closes the position, +100% sells half once and the rest rides. They apply at that moment's price and do not fire while the app is closed. Mirror exits need you: when a leader sells, tap **leader sold**. The tab shows equity, cash, realized and unrealized P&L, open and closed positions, and refreshes every minute while open. Reset the book from the same tab. All of it is editable in Settings.

**Log.** Every check with its reason, plus the week's counts. Under ten skips a week means the filter is too loose.

**Settings.** Every threshold, the model, the judge's effort, the confidence floor, and your key. Stored in `desk/data/settings.json`, which git ignores.

## Files

| file | role |
|---|---|
| `server.py` | the app: a local HTTP server, no framework, serves `ui/index.html` and the JSON endpoints |
| `ui/index.html` | the dashboard, one page, no build step |
| `config.py` | the default numbers. Settings in the UI override them, this file is never edited at runtime |
| `settings.py` | merges `data/settings.json` over the defaults, read at use time |
| `judge.py` | the only file that holds the key and calls Claude. One call, JSON schema in, JSON out |
| `prompts.py` | TRADER DESK and TOKEN DESK prompts with the schema each must answer in |
| `rugcheck.py` | fetch one Rugcheck report, trim it, apply the fact cut |
| `pick_traders.py` | code cut then Claude rank, writes `data/leaders.json` |
| `check.py` | the funnel for one buy, appends to `data/checks.csv`, opens a paper position on COPY |
| `paper.py` | paper book in `data/paper.json`: guards, fills, stop loss, take profit, mirror close |
| `prices.py` | one price from DexScreener, most liquid Solana pair wins |

The CLI still works from the desk folder: `python check.py <mint> <handle>` and `python pick_traders.py traders.json`.

## Rules the code enforces

- A failed call is a SKIP: Rugcheck down, Claude down, refused, bad JSON, missing key, handle not in the leaders.
- COPY below the confidence floor is a SKIP. Any verdict that is not literally `COPY` is a SKIP.
- The judge's answer is constrained to a JSON schema by the API. No prose is ever parsed into a trade.
- Claude never sees the key, the raw Rugcheck payload, your balance or your history. Arithmetic stays in code.
- If you disagree with a verdict, change a number in Settings. Don't re-run until it says COPY.
- `data/` is ignored by git on purpose: it holds your key, your leaders and your log.

## When it breaks

```
no Anthropic key        -> red banner in the header. Settings tab, paste the key.
401 from Anthropic      -> bad key. Paste it again.
404 from Anthropic      -> model name changed. Fix the model in Settings, nothing else.
429 from Anthropic      -> rate limit. Wait a minute, run again. Never loop it.
judge refused           -> SKIP, logged. The request tripped a safety classifier.
rugcheck 404 / empty    -> token too fresh to index. SKIP. Fresh is not a pass.
rugcheck timeout        -> SKIP this one. A missing report is missing, not clean.
handle not in leaders   -> SKIP. Your alerts drifted, your leaders didn't.
non-solana token        -> SKIP. Rugcheck here is Solana only. No report, no trade.
no paper fill: no price -> DexScreener has no pair yet. The COPY is logged, no position.
"?" in the last column  -> price fetch failed on this refresh. Hover for the error; try again.
port 8787 in use        -> change PORT in config.py.
```

## Tests

```
python -m unittest discover -s tests -v
```

No network: Rugcheck and the Anthropic API are mocked. The server tests spin up the real server on a random port.

Every number in Settings is one person's tuning on one month of $50 tickets. Run checks for a week and only read the log before putting a dollar behind a verdict. Most people who copy trade lose money. NFA, DYOR.
