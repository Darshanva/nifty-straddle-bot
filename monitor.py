"""
monitor.py — live prices via KiteTicker and the target / stop-loss / time-exit loop.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable

from kiteconnect import KiteTicker

from config import StrategyConfig
from instruments import OptionContract
from utils import at_today, now_ist

EXIT_TARGET = "TARGET"
EXIT_STOP_LOSS = "STOP_LOSS"
EXIT_TIME = "TIME_EXIT"
EXIT_KILL = "KILL_SWITCH"


class PriceFeed:
    def __init__(
        self,
        api_key: str,
        access_token: str,
        logger: logging.Logger,
        rest_ltp: Callable[[], dict[int, float]],
        stale_after: float,
    ) -> None:
        self._log = logger
        self._rest_ltp = rest_ltp
        self._stale_after = stale_after
        self._prices: dict[int, tuple[float, float]] = {}
        self._lock = threading.Lock()
        self._tokens: list[int] = []
        self._last_fallback_log = 0.0
        self._kws = KiteTicker(api_key, access_token)
        self._kws.on_ticks = self._on_ticks
        self._kws.on_connect = self._on_connect
        self._kws.on_close = lambda ws, code, reason: self._log.warning("Ticker closed: %s %s", code, reason)
        self._kws.on_error = lambda ws, code, reason: self._log.error("Ticker error: %s %s", code, reason)
        self._kws.on_reconnect = lambda ws, attempts: self._log.warning("Ticker reconnecting (attempt %s)", attempts)

    def _on_connect(self, ws: Any, response: Any) -> None:
        ws.subscribe(self._tokens)
        ws.set_mode(ws.MODE_LTP, self._tokens)
        self._log.info("Ticker connected; subscribed %s", self._tokens)

    def _on_ticks(self, ws: Any, ticks: list[dict[str, Any]]) -> None:
        now = time.monotonic()
        with self._lock:
            for t in ticks:
                if "last_price" in t:
                    self._prices[int(t["instrument_token"])] = (float(t["last_price"]), now)

    def start(self, tokens: list[int]) -> None:
        self._tokens = list(tokens)
        self._kws.connect(threaded=True)

    def stop(self) -> None:
        try:
            self._kws.close()
        except Exception as exc:
            self._log.warning("Ticker close error: %s", exc)

    def prices(self, tokens: list[int]) -> dict[int, float] | None:
        now = time.monotonic()
        with self._lock:
            snap = {t: self._prices.get(t) for t in tokens}
        if all(v is not None and now - v[1] <= self._stale_after for v in snap.values()):
            return {t: v[0] for t, v in snap.items() if v is not None}
        if now - self._last_fallback_log > 10:
            self._log.warning("Websocket ticks stale/missing — using REST LTP fallback")
            self._last_fallback_log = now
        try:
            rest = self._rest_ltp()
            return {t: rest[t] for t in tokens}
        except Exception as exc:
            self._log.error("REST LTP fallback failed: %s", exc)
            return None


def check_exit(combined: float, entry_combined: float, target_pct: float, sl_pct: float) -> str | None:
    if combined <= entry_combined * (1 - target_pct):
        return EXIT_TARGET
    if combined >= entry_combined * (1 + sl_pct):
        return EXIT_STOP_LOSS
    return None


def monitor_position(
    feed: PriceFeed,
    ce: OptionContract,
    pe: OptionContract,
    entry_combined: float,
    cfg: StrategyConfig,
    logger: logging.Logger,
    kill_check: Callable[[], bool],
    clock_ist: Callable[[], datetime] = now_ist,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, float | None]:
    exit_at = at_today(cfg.exit_time, clock_ist())
    target_level = entry_combined * (1 - cfg.target_pct)
    sl_level = entry_combined * (1 + cfg.sl_pct)
    logger.info("MONITOR entry_combined=%.2f target<=%.2f stop>=%.2f time_exit=%s",
                entry_combined, target_level, sl_level, cfg.exit_time)
    last_log = 0.0
    combined: float | None = None
    tokens = [ce.instrument_token, pe.instrument_token]
    while True:
        if clock_ist() >= exit_at:
            logger.info("SIGNAL %s combined=%s", EXIT_TIME, combined)
            return EXIT_TIME, combined
        if kill_check():
            logger.warning("SIGNAL %s", EXIT_KILL)
            return EXIT_KILL, combined
        px = feed.prices(tokens)
        if px is not None:
            combined = px[ce.instrument_token] + px[pe.instrument_token]
            reason = check_exit(combined, entry_combined, cfg.target_pct, cfg.sl_pct)
            if reason:
                logger.info("SIGNAL %s CE=%.2f PE=%.2f combined=%.2f", reason,
                            px[ce.instrument_token], px[pe.instrument_token], combined)
                return reason, combined
            if time.monotonic() - last_log >= cfg.monitor_log_every_seconds:
                pnl_pts = entry_combined - combined
                logger.info("MTM CE=%.2f PE=%.2f combined=%.2f (%+.2f pts vs entry)",
                            px[ce.instrument_token], px[pe.instrument_token], combined, pnl_pts)
                last_log = time.monotonic()
        sleep(cfg.monitor_poll_interval_seconds)