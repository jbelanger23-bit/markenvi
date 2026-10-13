# pick_traders.py
# Runs once a week. Input is traders.json: the top of copyfomo's /leaderboard
# with the stats from each fomo profile. Output is leaders.json.
import json
from config import TRADER, LEADERS_FILE
from prompts import TRADER_PROMPT
from grok import ask


def code_cut(t: dict) -> str | None:
    """Pass 1. Arithmetic on numbers you already have. No network."""
    if t["closed_trades"] < TRADER["min_closed_trades"]:    return "too_few_trades"
    if t["win_rate"] < TRADER["min_win_rate"]:              return "win_rate"
    if t["top_coin_share"] > TRADER["max_top_coin_share"]:  return "one_coin_luck"
    if t["pnl_7d_usd"] < TRADER["min_pnl_7d_usd"]:          return "red_week"
    return None


def pick(traders: list[dict]) -> list[dict]:
    """Pass 1 then pass 2. Returns the leaders with their stats merged in."""
    passed = []
    for t in traders:
        reason = code_cut(t)
        print(f"{t['handle']:<20} {'pass' if not reason else 'cut: ' + reason}")
        if not reason:
            passed.append(t)

    if not passed:
        raise SystemExit("nobody passed the code cut. that is an answer too.")

    out = ask(TRADER_PROMPT, {"traders": passed})       # pass 2
    by_handle = {t["handle"]: t for t in passed}
    return [{**by_handle[l["handle"]], **l} for l in out.get("leaders", [])
            if l.get("handle") in by_handle]              # never trust a handle Grok made up


if __name__ == "__main__":
    with open("traders.json") as f:
        traders = json.load(f)
    leaders = pick(traders)
    with open(LEADERS_FILE, "w") as f:
        json.dump(leaders, f, indent=2)

    for l in leaders:
        print(f"#{l['rank']} {l['handle']}: {l['why']} | risk: {l['risk']}")
