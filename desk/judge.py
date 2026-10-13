# judge.py
# The only file that holds the key and the only place the desk touches Claude.
#
# ONE CALL PER TOKEN. Never one call per question. You pay for the payload every time.
# JSON OR NOTHING. The response is constrained to a schema; anything else raises -> SKIP upstream.
# ARITHMETIC STAYS IN CODE. Top-10 share, win rate, pnl split: computed before the call,
#   passed in as fields. The judge never adds numbers up.
# NULL STAYS NULL. A field rugcheck did not return goes in as null, never as a guess.
import json
import anthropic
import settings


class JudgeError(RuntimeError):
    """Any reason the judge gave no usable verdict. check.py turns it into SKIP."""


def ask(system: str, payload: dict, schema: dict, s: dict | None = None) -> dict:
    """One call, JSON in, schema-valid JSON out."""
    s = s or settings.load()
    key = settings.api_key(s)
    if not key:
        raise JudgeError("no Anthropic API key. Set it in Settings or export ANTHROPIC_API_KEY")

    client = anthropic.Anthropic(api_key=key, max_retries=1, timeout=120)
    try:
        r = client.beta.messages.create(
            model=s["model"],
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": json.dumps(payload, sort_keys=True)}],
            output_config={"effort": s.get("effort", "medium"),
                           "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        raise JudgeError("401: bad Anthropic API key")
    except anthropic.NotFoundError:
        raise JudgeError(f"404: model {s['model']!r} not found. Fix the model in Settings")
    except anthropic.RateLimitError:
        raise JudgeError("429: rate limited. Wait a minute, run again. Never loop it")
    except anthropic.APIStatusError as e:
        raise JudgeError(f"{e.status_code} from Anthropic: {e.message}")
    except anthropic.APIConnectionError as e:
        raise JudgeError(f"network: {e}")

    if r.stop_reason == "refusal":
        why = getattr(getattr(r, "stop_details", None), "explanation", None) or "declined"
        raise JudgeError(f"judge refused: {why}")
    if r.stop_reason != "end_turn":
        raise JudgeError(f"judge stopped early: {r.stop_reason}")

    text = next((b.text for b in r.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        raise JudgeError(f"no json in answer: {text[:200]}")
