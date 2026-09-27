"""
main.py — runs the NIFTY 09:20 short straddle once per trading day.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from typing import Any

from kiteconnect import KiteConnect
from kiteconnect import exceptions as kex
from requests.exceptions import RequestException

from broker import SELL, Broker, LiveBroker, PaperBroker, read_retry
from charges import Execution, estimate_charges
from config import StrategyConfig, load_config
from exceptions import ConfigError, InstrumentError, LegRiskError
from instruments import OptionContract, select_straddle
from monitor import PriceFeed, monitor_position
from order_manager import LegWork, OrderManager
from utils import (DailyState, alert, at_today, is_trading_day, kill_switch_active,
                   load_holidays, now_ist, setup_logging)


def _raise_interrupt(signum: int, frame: Any) -> None:
    raise KeyboardInterrupt(f"signal {signum}")


def _credentials() -> tuple[str, str]:
    api_key = os.environ.get("KITE_API_KEY", "").strip()
    token = os.environ.get("KITE_ACCESS_TOKEN", "").strip()
    if not api_key or not token:
        raise ConfigError("Set KITE_API_KEY and KITE_ACCESS_TOKEN (run login.py each morning).")
    return api_key, token


@read_retry
def _spot_ltp(kite: KiteConnect, symbol: str) -> float:
    return float(kite.ltp([symbol])[symbol]["last_price"])


@read_retry
def _instruments(kite: KiteConnect, exchange: str) -> list[dict[str, Any]]:
    return list(kite.instruments(exchange))


def _margin_ok(kite: KiteConnect, ce: OptionContract, pe: OptionContract, qty: int,
               cfg: StrategyConfig, log: logging.Logger) -> bool:
    try:
        orders = [dict(exchange=c.exchange, tradingsymbol=c.tradingsymbol, transaction_type=SELL,
                       variety="regular", product="MIS", order_type="MARKET",
                       quantity=qty, price=0, trigger_price=0) for c in (ce, pe)]
        basket = kite.basket_order_margins(orders, consider_positions=True, mode="compact")
        required = float(basket["final"]["total"])
        available = float(kite.margins(segment="equity")["net"])
    except Exception as exc:
        log.error("Margin check failed (%s) — skipping trade", exc)
        return False
    needed = required * cfg.margin_safety_factor
    log.info("MARGIN required=%.0f x%.2f = %.0f | available=%.0f", required, cfg.margin_safety_factor, needed, available)
    return available >= needed


def _wait_until_entry(cfg: StrategyConfig, log: logging.Logger) -> bool:
    entry_at = at_today(cfg.entry_time)
    now = now_ist()
    if (now - entry_at).total_seconds() > cfg.entry_grace_seconds:
        log.warning("Started at %s, past entry %s + %ss grace — skipping today", now.time(), cfg.entry_time, cfg.entry_grace_seconds)
        return False
    while (remaining := (entry_at - now_ist()).total_seconds()) > 0:
        if kill_switch_active(cfg.kill_switch_path, cfg.kill_switch_env):
            log.warning("Kill switch active while waiting — no trade today")
            return False
        time.sleep(min(remaining, 15.0))
    return True


def _pnl_summary(entry: list[LegWork], exits: list[LegWork], cfg: StrategyConfig) -> dict[str, Any]:
    execs = [Execution(l.side, s.avg_price, s.filled_qty) for l in entry + exits for s in l.executed_orders]
    sell_value = sum(e.price * e.qty for e in execs if e.side == "SELL")
    buy_value = sum(e.price * e.qty for e in execs if e.side == "BUY")
    charges = estimate_charges(execs, cfg.charges)
    gross = sell_value - buy_value
    return {
        "entry": [{"symbol": l.contract.tradingsymbol, "side": l.side, "qty": l.filled_qty, "avg": round(l.avg_price, 2)} for l in entry],
        "exit": [{"symbol": l.contract.tradingsymbol, "side": l.side, "qty": l.filled_qty, "avg": round(l.avg_price, 2)} for l in exits],
        "gross_pnl": round(gross, 2),
        "charges_estimate": charges.as_dict(),
        "net_pnl_estimate": round(gross - charges.total, 2),
    }


def run(cfg: StrategyConfig) -> int:
    today = now_ist().date()
    mode = "PAPER" if cfg.paper_trading else "LIVE"
    log = setup_logging(cfg.log_dir, today, mode)
    log.info("===== NIFTY straddle bot | %s | %s =====", mode, today)

    holidays = load_holidays(cfg.holidays_path)
    if holidays is None:
        if cfg.require_holidays_file:
            log.error("holidays.txt missing and require_holidays_file=True — not trading")
            return 1
        holidays = set()
    if not is_trading_day(today, holidays):
        log.info("Not a trading day — exiting")
        return 0
    if kill_switch_active(cfg.kill_switch_path, cfg.kill_switch_env):
        log.warning("Kill switch active — exiting")
        return 0
    state = DailyState(cfg.state_dir, today, mode)
    if state.trade_attempted:
        log.info("Already attempted a trade today (max 1/day) — exiting")
        return 0

    api_key, access_token = _credentials()
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    try:
        profile = kite.profile()
        log.info("Logged in as %s", profile.get("user_id"))
    except kex.TokenException:
        log.error("Access token invalid/expired — run login.py and update KITE_ACCESS_TOKEN")
        return 2

    if not _wait_until_entry(cfg, log):
        return 0

    try:
        spot = _spot_ltp(kite, cfg.spot_symbol)
        ce, pe = select_straddle(_instruments(kite, cfg.options_exchange), cfg.underlying_name,
                                 spot, cfg.strike_step, today, cfg.trade_on_expiry_day)
    except (InstrumentError, kex.KiteException, RequestException) as exc:
        log.error("Instrument selection failed: %s — no trade", exc)
        return 1
    qty = cfg.lots * ce.lot_size
    log.info("SPOT %.2f -> strike %s expiry %s | %s + %s | lots=%d lot_size=%d qty=%d",
             spot, ce.strike, ce.expiry, ce.tradingsymbol, pe.tradingsymbol, cfg.lots, ce.lot_size, qty)
    if qty > cfg.max_order_qty:
        log.error("qty %d exceeds max_order_qty %d (freeze limit) — reduce lots", qty, cfg.max_order_qty)
        return 1

    broker: Broker = PaperBroker(kite, log) if cfg.paper_trading else LiveBroker(kite, log, cfg.algo_id)
    positions = broker.net_positions()
    if any(positions.get(c.tradingsymbol, 0) != 0 for c in (ce, pe)):
        log.warning("Existing position in %s/%s — skipping", ce.tradingsymbol, pe.tradingsymbol)
        return 0
    if not _margin_ok(kite, ce, pe, qty, cfg, log):
        log.warning("Insufficient margin — skipping")
        return 0

    def rest_ltp() -> dict[int, float]:
        by_sym = broker.ltp([ce, pe])
        return {ce.instrument_token: by_sym[ce.tradingsymbol], pe.instrument_token: by_sym[pe.tradingsymbol]}

    feed = PriceFeed(api_key, access_token, log, rest_ltp, cfg.stale_tick_seconds)
    feed.start([ce.instrument_token, pe.instrument_token])

    state.update(trade_attempted=True, mode=mode, strike=ce.strike, expiry=ce.expiry,
                 ce=ce.tradingsymbol, pe=pe.tradingsymbol, qty=qty, status="ENTERING")

    om = OrderManager(broker, cfg, log, today)
    entry_legs: list[LegWork] = []
    exit_legs: list[LegWork] | None = None
    reason = "UNKNOWN"
    entry_combined: float | None = None
    try:
        try:
            entry_legs = om.enter_straddle(ce, pe, qty)
        except LegRiskError as exc:
            entry_legs, exit_legs, reason = exc.entry_legs, exc.exit_legs, "LEG_RISK_ABORT"
        else:
            entry_combined = sum(l.avg_price for l in entry_legs)
            state.update(status="IN_POSITION", entry_combined=entry_combined)
            reason, _ = monitor_position(feed, ce, pe, entry_combined, cfg, log,
                                         lambda: kill_switch_active(cfg.kill_switch_path, cfg.kill_switch_env))
            exit_legs = om.exit_legs([(l.contract, l.filled_qty) for l in entry_legs], reason)
    except KeyboardInterrupt:
        alert(log, "Interrupted — flattening positions. Do NOT interrupt again.")
        reason = "INTERRUPTED"
        exit_legs = om.flatten_from_positions([ce, pe], reason)
    except Exception as exc:
        log.exception("Unexpected error: %s", exc)
        alert(log, "Unexpected error — flattening positions")
        reason = "ERROR"
        exit_legs = om.flatten_from_positions([ce, pe], reason)
    finally:
        feed.stop()

    summary: dict[str, Any] = {
        "date": today.isoformat(), "mode": mode, "spot_at_entry": spot,
        "strike": ce.strike, "expiry": ce.expiry.isoformat(), "qty": qty,
        "entry_combined_premium": round(entry_combined, 2) if entry_combined else None,
        "exit_reason": reason,
        **_pnl_summary(entry_legs or om.entry_legs, exit_legs or [], cfg),
    }
    state.update(status="DONE", exit_reason=reason, gross_pnl=summary["gross_pnl"])
    out = cfg.log_dir / f"summary_{mode.lower()}_{today.isoformat()}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("SUMMARY %s", json.dumps(summary))
    log.info("Gross P&L Rs %.2f | est. charges Rs %.2f | est. net Rs %.2f",
             summary["gross_pnl"], summary["charges_estimate"]["total"], summary["net_pnl_estimate"])
    return 0


def main() -> None:
    signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        sys.exit(run(load_config()))
    except ConfigError as exc:
        logging.getLogger("straddle").error("Config error: %s", exc)
        print(f"Config error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()