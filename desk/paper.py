# paper.py
# Paper trading. A COPY opens a simulated position at the live price; the guide's copyfomo
# guards (max positions, no re-buy, first buy wins) gate the fill, and its exit rules (stop
# loss, take half at +100%) run on every mark. Mirror exits need you: "leader sold" closes.
# Nothing here touches a wallet. All state lives in data/paper.json.
import json, os, time, uuid
import settings
from config import DATA_DIR
from prices import price_usd

PAPER_FILE = os.path.join(DATA_DIR, "paper.json")
EMPTY = {"cash": None, "positions": []}


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def _ts(s: str) -> float:
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M"))


def load(path: str = PAPER_FILE, s: dict | None = None) -> dict:
    s = s or settings.load()
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = json.loads(json.dumps(EMPTY))
    if d.get("cash") is None:
        d["cash"] = float(s["paper"]["start_balance"])
    return d


def save(d: dict, path: str = PAPER_FILE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(d, f, indent=2)


def reset(path: str = PAPER_FILE, s: dict | None = None) -> dict:
    d = json.loads(json.dumps(EMPTY))
    save(d, path)
    return load(path, s)


def open_pos(d: dict) -> list[dict]:
    return [p for p in d["positions"] if p["status"] == "open"]


def guard(d: dict, mint: str, s: dict, at: float | None = None) -> str | None:
    """copyfomo's own guards, in code. First failure wins."""
    cfg, at = s["paper"], at or time.time()
    if any(p["mint"] == mint for p in open_pos(d)):
        return "already holding (first buy wins)"
    if len(open_pos(d)) >= int(cfg["max_positions"]):
        return f"max positions ({cfg['max_positions']})"
    window = float(cfg["no_rebuy_hours"]) * 3600
    for p in d["positions"]:
        if p["mint"] == mint and p["status"] == "closed" and at - _ts(p["closed"]) < window:
            return f"sold within {cfg['no_rebuy_hours']}h, no re-buy"
    if d["cash"] < float(s["ticket_usd"]):
        return f"cash {d['cash']:.2f} below ticket"
    return None


def try_open(mint: str, symbol: str | None, leader: str, s: dict | None = None,
             path: str = PAPER_FILE, price: float | None = None) -> dict:
    """Called on every COPY when paper mode is on. Returns {"filled": bool, "reason"|"position"}."""
    s = s or settings.load()
    d = load(path, s)
    if (why := guard(d, mint, s)):
        return {"filled": False, "reason": why}
    if price is None:
        try:
            price = price_usd(mint)
        except Exception as e:
            return {"filled": False, "reason": f"price failed: {e}"}
    if not price or price <= 0:
        return {"filled": False, "reason": "no price on DexScreener"}
    usd = float(s["ticket_usd"])
    p = {"id": uuid.uuid4().hex[:8], "mint": mint, "symbol": symbol, "leader": leader,
         "opened": now(), "entry": price, "qty": usd / price, "cost": usd,
         "status": "open", "tp_taken": False, "realized": 0.0, "last": price, "marked": now()}
    d["cash"] -= usd
    d["positions"].append(p)
    save(d, path)
    return {"filled": True, "position": p}


def _sell(d: dict, p: dict, qty: float, price: float, reason: str):
    proceeds = qty * price
    d["cash"] += proceeds
    p["realized"] += proceeds - qty * p["entry"]
    p["qty"] -= qty
    if p["qty"] <= 1e-12:
        p["qty"], p["status"], p["closed"], p["exit"], p["exit_reason"] = 0.0, "closed", now(), price, reason
    p["last"], p["marked"] = price, now()


def mark(s: dict | None = None, path: str = PAPER_FILE, prices: dict | None = None) -> dict:
    """Fetch prices for open positions, apply stop loss and take-profit, return the book."""
    s = s or settings.load()
    cfg, d = s["paper"], load(path, s)
    events = []
    for p in open_pos(d):
        try:
            price = prices[p["mint"]] if prices is not None else price_usd(p["mint"])
        except Exception as e:
            p["price_error"] = str(e); continue
        if not price:
            p["price_error"] = "no price"; continue
        p.pop("price_error", None)
        p["last"], p["marked"] = price, now()
        chg = price / p["entry"] - 1
        if chg <= -float(cfg["stop_loss_pct"]) / 100:
            _sell(d, p, p["qty"], price, f"stop loss {chg * 100:+.0f}%")
            events.append(f"{p['symbol']}: stop loss at {chg * 100:+.0f}%")
        elif not p["tp_taken"] and chg >= float(cfg["take_profit_pct"]) / 100:
            _sell(d, p, p["qty"] * float(cfg["take_profit_sell_frac"]), price, "take profit")
            p["tp_taken"] = True
            events.append(f"{p['symbol']}: took {float(cfg['take_profit_sell_frac']) * 100:.0f}% at {chg * 100:+.0f}%")
    save(d, path)
    return {**book(d, s), "events": events}


def close(pos_id: str, reason: str = "leader sold", s: dict | None = None, path: str = PAPER_FILE,
          price: float | None = None) -> dict:
    s = s or settings.load()
    d = load(path, s)
    p = next((p for p in open_pos(d) if p["id"] == pos_id), None)
    if not p:
        raise ValueError("no open position with that id")
    if price is None:
        try:
            price = price_usd(p["mint"])
        except Exception as e:
            raise ValueError(f"price failed: {e}")
    if not price:
        raise ValueError("no price on DexScreener; try again")
    _sell(d, p, p["qty"], price, reason)
    save(d, path)
    return book(d, s)


def book(d: dict, s: dict) -> dict:
    opens = open_pos(d)
    for p in opens:
        p["value"] = p["qty"] * p["last"]
        p["unrealized"] = p["value"] - p["qty"] * p["entry"]
        p["change_pct"] = (p["last"] / p["entry"] - 1) * 100
    closed = [p for p in d["positions"] if p["status"] == "closed"]
    equity = d["cash"] + sum(p["value"] for p in opens)
    start = float(s["paper"]["start_balance"])
    return {"cash": d["cash"], "equity": equity, "start": start, "pnl": equity - start,
            "realized": sum(p["realized"] for p in d["positions"]),
            "open": opens, "closed": sorted(closed, key=lambda p: p["closed"], reverse=True),
            "wins": sum(1 for p in closed if p["realized"] > 0), "losses": sum(1 for p in closed if p["realized"] <= 0)}
