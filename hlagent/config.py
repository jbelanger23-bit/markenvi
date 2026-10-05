"""Account, risk and strategy configuration.

Defaults are tuned for a small personal account (< $500) on Hyperliquid perps.
Every hard limit here is enforced by hlagent.risk.RiskEngine; strategies and the
learning loop can only tune parameters inside the bounds declared in TUNABLE.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RiskConfig:
    # --- hard account limits (never tuned by the learner) ---
    max_risk_per_trade_pct: float = 0.75      # % of equity lost if the stop is hit
    max_daily_loss_pct: float = 2.5           # stop trading for the day past this
    max_weekly_loss_pct: float = 5.0          # stop trading for the week past this
    max_drawdown_pct: float = 10.0            # from equity high-water mark -> full halt
    max_leverage: float = 3.0                 # notional / equity, per position
    max_open_positions: int = 1
    max_trades_per_day: int = 6
    max_consecutive_losses: int = 3           # cool-down after this many in a row
    cooldown_minutes_after_streak: int = 120
    min_stop_distance_pct: float = 0.15       # stop too tight = noise fill on HL
    max_stop_distance_pct: float = 1.5        # stop too wide = size too small to matter
    require_stop_on_entry: bool = True
    halt_file: str = "HALT"                   # presence of this file stops all trading

    # --- market-condition gates ---
    max_spread_bps: float = 4.0               # skip if book is too wide
    min_book_depth_usd: float = 50_000.0      # top-5 levels each side
    funding_extreme_8h_pct: float = 0.05      # |funding| above this: no trades in its direction
    weekend_size_multiplier: float = 0.5
    no_trade_minutes_around_events: int = 30  # FOMC/CPI/NFP etc.


@dataclass(frozen=True)
class StrategyConfig:
    symbols: tuple[str, ...] = ("BTC", "ETH", "SOL")
    bar_interval: str = "5m"
    # order-book imbalance
    imbalance_levels: int = 5
    imbalance_threshold: float = 0.35         # (bid-ask)/(bid+ask) over top N levels
    imbalance_confirm_ticks: int = 3
    # stop-run / liquidation-run
    sweep_lookback_bars: int = 24
    sweep_min_penetration_pct: float = 0.05   # how far past the level price must poke
    sweep_max_bars_to_reclaim: int = 2
    liq_volume_spike_mult: float = 3.0        # bar volume vs 20-bar median
    liq_oi_drop_pct: float = 1.0              # OI drop in the bar (% of OI)
    # trade management
    target_r_multiple: float = 1.5
    time_stop_bars: int = 12
    breakeven_at_r: float = 0.8


# Parameters the learner may adjust, with hard bounds. Nothing in RiskConfig is here.
TUNABLE: dict[str, tuple[float, float]] = {
    "imbalance_threshold": (0.25, 0.6),
    "sweep_min_penetration_pct": (0.02, 0.15),
    "liq_volume_spike_mult": (2.0, 5.0),
    "target_r_multiple": (1.0, 2.5),
    "time_stop_bars": (6, 24),
}


@dataclass
class Settings:
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    mode: str = field(default_factory=lambda: os.getenv("HL_MODE", "paper"))  # paper|testnet|live
    state_dir: str = field(default_factory=lambda: os.getenv("HL_STATE_DIR", "state"))
    starting_equity: float = field(default_factory=lambda: float(os.getenv("HL_START_EQUITY", "400")))
    # live/testnet only; never commit these. Agent key = API wallet, not the main wallet.
    account_address: str | None = field(default_factory=lambda: os.getenv("HL_ACCOUNT_ADDRESS"))
    agent_private_key: str | None = field(default_factory=lambda: os.getenv("HL_AGENT_PRIVATE_KEY"))
