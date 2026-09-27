"""
config.py — every tunable parameter lives here. Nothing is hard-coded in logic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

from exceptions import ConfigError


@dataclass(frozen=True)
class ChargesConfig:
    brokerage_per_order: float = 20.0
    stt_sell_pct: float = 0.001
    exchange_txn_pct: float = 0.0003503
    sebi_fee_per_crore: float = 10.0
    gst_pct: float = 0.18
    stamp_duty_buy_pct: float = 0.00003


@dataclass(frozen=True)
class StrategyConfig:
    underlying_name: str = "NIFTY"
    spot_symbol: str = "NSE:NIFTY 50"
    options_exchange: str = "NFO"
    strike_step: int = 50
    trade_on_expiry_day: bool = False

    lots: int = 1
    max_order_qty: int = 1800

    entry_time: time = time(9, 20, 0)
    exit_time: time = time(9, 30, 0)
    entry_grace_seconds: int = 60

    target_pct: float = 0.02
    sl_pct: float = 0.03

    limit_buffer_pct: float = 0.01
    limit_buffer_min_points: float = 0.5
    leg_fill_timeout_seconds: int = 10
    entry_reprice_interval_seconds: float = 2.0
    exit_reprice_interval_seconds: float = 1.5
    order_poll_interval_seconds: float = 0.25
    exit_attempts_before_alert: int = 5
    order_tag_prefix: str = "STRDL"
    algo_id: str | None = field(default_factory=lambda: os.environ.get("KITE_ALGO_ID") or None)

    monitor_poll_interval_seconds: float = 0.2
    stale_tick_seconds: float = 3.0
    monitor_log_every_seconds: float = 5.0

    paper_trading: bool = True
    margin_safety_factor: float = 1.10
    kill_switch_file: str = "KILL_SWITCH"
    kill_switch_env: str = "STRADDLE_KILL_SWITCH"
    holidays_file: str = "holidays.txt"
    require_holidays_file: bool = True

    base_dir: Path = Path(__file__).resolve().parent
    log_dir_name: str = "logs"
    state_dir_name: str = "state"

    charges: ChargesConfig = field(default_factory=ChargesConfig)

    def __post_init__(self) -> None:
        if self.lots < 1:
            raise ConfigError("lots must be >= 1")
        if not (0 < self.target_pct < 1) or not (0 < self.sl_pct < 1):
            raise ConfigError("target_pct and sl_pct must be between 0 and 1")
        if self.exit_time <= self.entry_time:
            raise ConfigError("exit_time must be after entry_time")
        if not self.order_tag_prefix.isalnum() or len(self.order_tag_prefix) > 8:
            raise ConfigError("order_tag_prefix must be alphanumeric, max 8 chars")
        if self.strike_step <= 0:
            raise ConfigError("strike_step must be positive")

    @property
    def log_dir(self) -> Path:
        return self.base_dir / self.log_dir_name

    @property
    def state_dir(self) -> Path:
        return self.base_dir / self.state_dir_name

    @property
    def kill_switch_path(self) -> Path:
        return self.base_dir / self.kill_switch_file

    @property
    def holidays_path(self) -> Path:
        return self.base_dir / self.holidays_file


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y"}


def load_config() -> StrategyConfig:
    defaults = StrategyConfig()
    lots = int(os.environ.get("STRADDLE_LOTS", defaults.lots))
    paper = _env_bool("PAPER_TRADING", defaults.paper_trading)
    return StrategyConfig(lots=lots, paper_trading=paper)