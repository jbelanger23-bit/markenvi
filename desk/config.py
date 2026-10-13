# config.py
# Every number on the desk lives here. When you retune, edit this file and nothing else.
MODEL = "grok-4"            # check console.x.ai -> Models if the API rejects it
TICKET_USD = 50             # flat. never size up because the last three won

TRADER = {                  # pass 1, arithmetic, no network
    "min_closed_trades": 20,    # one lucky trade is not a track record
    "min_win_rate":      0.55,
    "max_top_coin_share": 0.50, # one coin can't be more than half their pnl
    "min_pnl_7d_usd":    0,     # red last week = not this week
}

TOKEN = {                   # pass 4, facts from rugcheck, no model
    "max_risk_score":    50,    # score_normalised, 0-100, higher = riskier
    "max_top10_pct":     30,
    "min_lp_locked_pct": 90,
    "max_insider_nets":  0,
}

MIN_CONFIDENCE = 0.65       # Grok says COPY below this -> treated as SKIP
LEADERS_FILE = "leaders.json"
LOG_FILE = "checks.csv"
