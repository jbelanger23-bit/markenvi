# pick_traders.py
# Runs once a week, from the Leaders tab or the CLI. Input is the top of copyfomo's
# /leaderboard with the stats from each fomo profile. Output is leaders.json.
import json, os, sys
import settings
from config import LEADERS_FILE
from prompts import TRADER_PROMPT, TRADER_SCHEMA
from judge import ask

FIELDS = ["handle", "pnl_30d_usd", "pnl_7d_usd", "closed_trades", "win_rate",
          "top_coin_share", "avg_hold_minutes", "followers"]


def code_cut(t: dict, cfg: dict | None = None) -> str | None:
    """Pass 1. Arithmetic on numbers you already have. No network."""
    cfg = cfg or settings.load()["trader"]
    if t["closed_trades"] < cfg["min_closed_trades"]:    return "too_few_trades"
    if t["win_rate"] < cfg["min_win_rate"]:              return "win_rate"
    if t["top_coin_share"] > cfg["max_top_coin_share"]:  return "one_coin_luck"
    if t["pnl_7d_usd"] < cfg["min_pnl_7d_usd"]:          return "red_week"
    return None


def pick(traders: list[dict], s: dict | None = None) -> dict:
    """Pass 1 then pass 2. Returns {"leaders": [...stats + rank/why/risk], "cut": [...], "rejected": [...]}."""
    s = s or settings.load()
    passed, cut = [], []
    for t in traders:
        reason = code_cut(t, s["trader"])
        if reason:
            cut.append({"handle": t["handle"], "reason": reason})
        else:
            passed.append(t)

    if not passed:
        return {"leaders": [], "cut": cut, "rejected": [],
                "note": "nobody passed the code cut. that is an answer too."}

    out = ask(TRADER_PROMPT, {"traders": passed}, TRADER_SCHEMA, s)       # pass 2
    by_handle = {t["handle"]: t for t in passed}
    leaders = [{**by_handle[l["handle"]], **l} for l in out.get("leaders", [])
               if l.get("handle") in by_handle]              # never trust a handle the judge made up
    leaders.sort(key=lambda l: l["rank"])
    return {"leaders": leaders, "cut": cut, "rejected": out.get("rejected", [])}


def save_leaders(leaders: list[dict], path: str = LEADERS_FILE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(leaders, f, indent=2)


def load_leaders(path: str = LEADERS_FILE) -> list[dict]:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "traders.json"
    with open(src) as f:
        traders = json.load(f)
    out = pick(traders)
    for c in out["cut"]:
        print(f"{c['handle']:<20} cut: {c['reason']}")
    if out.get("note"):
        raise SystemExit(out["note"])
    save_leaders(out["leaders"])
    for l in out["leaders"]:
        print(f"#{l['rank']} {l['handle']}: {l['why']} | risk: {l['risk']}")
