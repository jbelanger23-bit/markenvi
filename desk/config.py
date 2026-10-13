# config.py
# Every number on the desk starts here. The Settings tab in the app writes overrides to
# data/settings.json; nothing in this file is edited at runtime.
import os

MODEL = "claude-opus-5-5"   # the only judge on the desk
EFFORT = "medium"           # low | medium | high. thinking depth of the judge
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

MIN_CONFIDENCE = 0.65       # Claude says COPY below this -> treated as SKIP

# Local state. Everything under DATA_DIR is ignored by git.
DATA_DIR = os.environ.get("DESK_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LEADERS_FILE = os.path.join(DATA_DIR, "leaders.json")
LOG_FILE = os.path.join(DATA_DIR, "checks.csv")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

HOST, PORT = "127.0.0.1", 8787
