# grok.py
# The only file that holds the key and the only place the desk touches xAI.
#
# ONE CALL PER TOKEN. Never one call per question. You pay for the payload every time.
# TEMPERATURE 0. Same token, same answer. A judge that changes its mind is not a judge.
# ARITHMETIC STAYS IN CODE. Top-10 share, win rate, pnl split: computed before the call,
#   passed in as fields. Grok never adds numbers up.
# NULL STAYS NULL. A field rugcheck did not return goes in as null, never as a guess.
import os, json, requests
from config import MODEL

URL = "https://api.x.ai/v1/chat/completions"


def _key() -> str:
    # Read at call time, not import time: a missing key surfaces as "grok failed"
    # (-> SKIP) in check.py instead of crashing before the log line is written.
    try:
        return os.environ["XAI_API_KEY"]
    except KeyError:
        raise RuntimeError("XAI_API_KEY is not set") from None


def ask(system: str, payload: dict) -> dict:
    """One call, JSON in, JSON out. Anything that isn't JSON is treated as SKIP upstream."""
    r = requests.post(URL, timeout=60,
                      headers={"Authorization": f"Bearer {_key()}"},
                      json={"model": MODEL, "temperature": 0,
                            "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": json.dumps(payload)}]})
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    start, end = text.find("{"), text.rfind("}")
    if start == -1:
        raise ValueError(f"no json in answer: {text[:200]}")
    return json.loads(text[start:end + 1])
