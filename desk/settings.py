# settings.py
# Defaults come from config.py; the app's Settings tab stores overrides in data/settings.json.
# Read at use time, never at import, so an edit in the UI applies to the next check.
import json, os
import config

DEFAULTS = {
    "model": config.MODEL,
    "effort": config.EFFORT,
    "ticket_usd": config.TICKET_USD,
    "min_confidence": config.MIN_CONFIDENCE,
    "trader": dict(config.TRADER),
    "token": dict(config.TOKEN),
    "anthropic_api_key": "",     # empty -> ANTHROPIC_API_KEY from the environment
    "paper": dict(config.PAPER),
}


def load(path: str = config.SETTINGS_FILE) -> dict:
    s = json.loads(json.dumps(DEFAULTS))
    try:
        with open(path) as f:
            over = json.load(f)
    except (OSError, ValueError):
        return s
    for k, v in over.items():
        if k in ("trader", "token", "paper") and isinstance(v, dict):
            s[k].update({kk: vv for kk, vv in v.items() if kk in s[k]})
        elif k in s:
            s[k] = v
    return s


def save(over: dict, path: str = config.SETTINGS_FILE) -> dict:
    """Merge `over` into the stored overrides and return the effective settings."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path) as f:
            cur = json.load(f)
    except (OSError, ValueError):
        cur = {}
    for k, v in over.items():
        if k not in DEFAULTS:
            continue
        if k in ("trader", "token", "paper") and isinstance(v, dict):
            cur.setdefault(k, {}).update({kk: _coerce(vv) for kk, vv in v.items() if kk in DEFAULTS[k]})
        elif k in ("ticket_usd", "min_confidence"):
            cur[k] = _num(v)
        else:
            cur[k] = v
    with open(path, "w") as f:
        json.dump(cur, f, indent=2)
    return load(path)


def _coerce(v):
    if isinstance(v, bool) or v in ("true", "false", "on", "off"):
        return v if isinstance(v, bool) else v in ("true", "on")
    return _num(v)


def _num(v):
    if isinstance(v, (int, float)):
        return v
    f = float(v)
    return int(f) if f.is_integer() else f


def api_key(s: dict | None = None) -> str:
    s = s or load()
    return (s.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def public(s: dict | None = None) -> dict:
    """Settings as the UI sees them: the key is reduced to whether one is set."""
    s = dict(s or load())
    s["key_set"] = bool(api_key(s))
    s["key_source"] = "settings" if s.get("anthropic_api_key") else ("env" if os.environ.get("ANTHROPIC_API_KEY") else "")
    s.pop("anthropic_api_key")
    return s
