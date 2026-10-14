# prices.py
# One price, from DexScreener's public API. No key. Solana only, like Rugcheck.
# The most liquid pair wins; a token with no pair has no price, and no price means no paper fill.
import requests

DS = "https://api.dexscreener.com/latest/dex/tokens"


def price_usd(mint: str) -> float | None:
    r = requests.get(f"{DS}/{mint}", timeout=15)
    r.raise_for_status()
    pairs = [p for p in (r.json().get("pairs") or []) if p.get("chainId") == "solana" and p.get("priceUsd")]
    if not pairs:
        return None
    best = max(pairs, key=lambda p: ((p.get("liquidity") or {}).get("usd") or 0))
    try:
        return float(best["priceUsd"])
    except (TypeError, ValueError):
        return None
