# rugcheck.py
# Fetches one Rugcheck report and keeps only the fields the desk reads.
# The judge never sees the raw report: a fat payload costs tokens and costs accuracy.
import requests
import settings

RC = "https://api.rugcheck.xyz/v1/tokens"


def report(mint: str) -> dict:
    r = requests.get(f"{RC}/{mint}/report", timeout=20)
    r.raise_for_status()
    d = r.json()
    holders = d.get("topHolders") or []
    risks = d.get("risks") or []
    token = d.get("token") or {}
    return {
        "mint": mint,
        "symbol": (d.get("tokenMeta") or {}).get("symbol"),
        "risk_score": d.get("score_normalised"),
        "danger": [x.get("name") for x in risks if x.get("level") == "danger"],
        "warn": [x.get("name") for x in risks if x.get("level") == "warn"],
        "lp_locked_pct": d.get("lpLockedPct"),
        "top10_pct": round(sum(h.get("pct", 0) for h in holders[:10]), 2) if holders else None,
        "insider_nets": len(d.get("insiderNetworks") or []),
        "mint_authority": token.get("mintAuthority"),
        "freeze_authority": token.get("freezeAuthority"),
    }


def fact_kill(t: dict, cfg: dict | None = None) -> str | None:
    """Pass 4. Plain comparisons. First failure wins, the reason gets logged."""
    cfg = cfg or settings.load()["token"]
    if t["risk_score"] is None:                            return "no_report"
    if t["mint_authority"] or t["freeze_authority"]:       return "authority_open"
    if t["danger"]:                                        return "danger:" + t["danger"][0]
    if t["risk_score"] > cfg["max_risk_score"]:            return "risk_score"
    if t["top10_pct"] is not None and t["top10_pct"] > cfg["max_top10_pct"]:
        return "top10"
    if t["lp_locked_pct"] is not None and t["lp_locked_pct"] < cfg["min_lp_locked_pct"]:
        return "lp_unlocked"
    if t["insider_nets"] > cfg["max_insider_nets"]:        return "insiders"
    return None
