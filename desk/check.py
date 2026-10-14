# check.py
# Runs every time a leader buys, from the Check tab or the CLI:  python check.py <mint> <handle>
#
# NEVER COPY ON A FAILED CALL. Rugcheck down, judge down, bad JSON: the answer is SKIP.
# NEVER RE-RUN UNTIL IT SAYS COPY. If you disagree, you disagree with a number in Settings.
# LOG EVERY SKIP WITH ITS REASON. Under ten skips a week and your filter is too loose.
import csv, json, os, sys, time
import settings
from config import LEADERS_FILE, LOG_FILE
from prompts import TOKEN_PROMPT, TOKEN_SCHEMA
from rugcheck import report, fact_kill
from judge import ask
from pick_traders import load_leaders
import paper

FIELDS = ["time", "leader", "mint", "symbol", "verdict", "confidence", "reason", "stage"]


def log(row: dict, path: str = LOG_FILE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k) for k in FIELDS})


def read_log(path: str = LOG_FILE) -> list[dict]:
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def check(mint: str, handle: str, leaders_file: str = LEADERS_FILE, s: dict | None = None) -> dict:
    """The funnel for one buy. `stage` says where it stopped: leader | rugcheck | facts | judge | confidence | done."""
    s = s or settings.load()
    leaders = {l["handle"]: l for l in load_leaders(leaders_file)}
    if handle not in leaders:
        return {"verdict": "SKIP", "reason": "not a leader", "stage": "leader"}  # alerts drift, the file doesn't

    try:
        token = report(mint)                                     # pass 3
    except Exception as e:
        return {"verdict": "SKIP", "reason": f"rugcheck failed: {e}", "stage": "rugcheck"}

    if (k := fact_kill(token, s["token"])):                      # pass 4
        return {"verdict": "SKIP", "reason": k, "symbol": token["symbol"], "token": token, "stage": "facts"}

    leader = {k: v for k, v in leaders[handle].items() if k not in ("rank", "why", "risk")}
    try:
        ans = ask(TOKEN_PROMPT, {"leader": leader, "token": token}, TOKEN_SCHEMA, s)  # pass 5
    except Exception as e:
        return {"verdict": "SKIP", "reason": f"judge failed: {e}", "symbol": token["symbol"],
                "token": token, "stage": "judge"}               # no judge, no trade

    stage = "done"
    if ans.get("verdict") != "COPY":
        ans["verdict"] = "SKIP"                                  # anything not COPY is SKIP
    elif not isinstance(ans.get("confidence"), (int, float)) or ans["confidence"] < s["min_confidence"]:
        ans["verdict"], ans["why"], stage = "SKIP", f"low confidence {ans.get('confidence')}: {ans.get('why', '')}", "confidence"
    return {**ans, "symbol": token["symbol"], "reason": ans.get("why"), "token": token, "stage": stage}


def check_and_log(mint: str, handle: str, leaders_file: str = LEADERS_FILE,
                  log_file: str = LOG_FILE, s: dict | None = None, **paper_kw) -> dict:
    s = s or settings.load()
    v = check(mint, handle, leaders_file, s)
    v["time"] = time.strftime("%Y-%m-%d %H:%M")
    v["leader"], v["mint"] = handle, mint
    if v["verdict"] == "COPY" and s["paper"]["enabled"]:
        v["paper"] = paper.try_open(mint, v.get("symbol"), handle, s, **paper_kw)
    log(v, log_file)
    return v


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python check.py <mint> <handle>")
    v = check_and_log(sys.argv[1], sys.argv[2])
    v.pop("token", None)
    print(json.dumps(v, indent=2))
