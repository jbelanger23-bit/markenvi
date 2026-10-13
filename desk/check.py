# check.py
# Runs every time a leader buys. One command, one verdict:  python check.py <mint> <handle>
#
# NEVER COPY ON A FAILED CALL. Rugcheck down, Grok down, bad JSON: the answer is SKIP.
# NEVER RE-RUN UNTIL IT SAYS COPY. Temperature 0 means the same answer. If you disagree,
#   you disagree with a number in config.py. Change the number, not the dice.
# LOG EVERY SKIP WITH ITS REASON. Under ten skips a week and your filter is too loose.
import csv, json, os, sys, time
from config import LEADERS_FILE, LOG_FILE, MIN_CONFIDENCE
from prompts import TOKEN_PROMPT
from rugcheck import report, fact_kill
from grok import ask

FIELDS = ["time", "leader", "mint", "symbol", "verdict", "confidence", "reason"]


def log(row: dict, path: str = LOG_FILE):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k) for k in FIELDS})


def check(mint: str, handle: str, leaders_file: str = LEADERS_FILE) -> dict:
    try:
        with open(leaders_file) as f:
            leaders = {l["handle"]: l for l in json.load(f)}
    except (OSError, ValueError) as e:
        return {"verdict": "SKIP", "reason": f"no leaders file: {e}"}
    if handle not in leaders:
        return {"verdict": "SKIP", "reason": "not a leader"}  # alerts drift, the file doesn't

    try:
        token = report(mint)                                     # pass 3
    except Exception as e:
        return {"verdict": "SKIP", "reason": f"rugcheck failed: {e}"}

    if (k := fact_kill(token)):                                  # pass 4
        return {"verdict": "SKIP", "reason": k, "symbol": token["symbol"]}

    try:
        ans = ask(TOKEN_PROMPT, {"leader": leaders[handle], "token": token})  # pass 5
    except Exception as e:
        return {"verdict": "SKIP", "reason": f"grok failed: {e}"}  # no judge, no trade

    if ans.get("verdict") != "COPY":
        ans["verdict"] = "SKIP"                                  # anything not COPY is SKIP
    elif not isinstance(ans.get("confidence"), (int, float)) or ans["confidence"] < MIN_CONFIDENCE:
        ans["verdict"], ans["why"] = "SKIP", f"low confidence {ans.get('confidence')}"
    return {**ans, "symbol": token["symbol"], "reason": ans.get("why")}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python check.py <mint> <handle>")
    mint, handle = sys.argv[1], sys.argv[2]
    v = check(mint, handle)
    log({"time": time.strftime("%Y-%m-%d %H:%M"), "leader": handle, "mint": mint,
         "symbol": v.get("symbol"), "verdict": v["verdict"],
         "confidence": v.get("confidence"), "reason": v.get("reason")})
    print(json.dumps(v, indent=2))
