"""
order_manager.py — entry and exit execution.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from broker import BUY, SELL, TRANSIENT_ERRORS, Broker, OrderStatus
from config import StrategyConfig
from exceptions import LegRiskError, OrderError
from instruments import OptionContract
from utils import alert


@dataclass
class LegWork:
    contract: OptionContract
    side: str
    target_qty: int
    orders: dict[str, OrderStatus] = field(default_factory=dict)
    active_order_id: str | None = None
    attempts: int = 0
    last_reprice_at: float = 0.0
    last_error: str = ""

    @property
    def filled_qty(self) -> int:
        return sum(s.filled_qty for s in self.orders.values())

    @property
    def remaining(self) -> int:
        return self.target_qty - self.filled_qty

    @property
    def avg_price(self) -> float:
        filled = self.filled_qty
        if filled == 0:
            return 0.0
        return sum(s.filled_qty * s.avg_price for s in self.orders.values()) / filled

    @property
    def executed_orders(self) -> list[OrderStatus]:
        return [s for s in self.orders.values() if s.filled_qty > 0]


class OrderManager:
    def __init__(
        self,
        broker: Broker,
        cfg: StrategyConfig,
        logger: logging.Logger,
        trade_date: date,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._broker = broker
        self._cfg = cfg
        self._log = logger
        self._date = trade_date
        self._clock = clock
        self._sleep = sleep
        self.entry_legs: list[LegWork] = []

    def marketable_price(self, ltp: float, side: str, tick: float) -> float:
        buffer = max(ltp * self._cfg.limit_buffer_pct, self._cfg.limit_buffer_min_points)
        if side == SELL:
            steps = math.floor(round((ltp - buffer) / tick, 6))
            price = max(steps * tick, tick)
        else:
            steps = math.ceil(round((ltp + buffer) / tick, 6))
            price = steps * tick
        return round(price, 2)

    def _tag(self, leg: LegWork, phase: str) -> str:
        return f"{self._cfg.order_tag_prefix}{self._date:%m%d}{leg.contract.option_type}{phase}{leg.attempts + 1}"[:20]

    def _place(self, leg: LegWork, phase: str) -> None:
        ltp = self._broker.ltp([leg.contract])[leg.contract.tradingsymbol]
        price = self.marketable_price(ltp, leg.side, leg.contract.tick_size)
        tag = self._tag(leg, phase)
        leg.attempts += 1
        oid = self._broker.place_limit(leg.contract, leg.side, leg.remaining, price, tag)
        leg.active_order_id = oid
        leg.last_reprice_at = self._clock()
        self._log.info("ORDER %s %s qty=%d limit=%.2f (ltp %.2f) tag=%s id=%s",
                       leg.side, leg.contract.tradingsymbol, leg.remaining, price, ltp, tag, oid)

    def _refresh(self, leg: LegWork) -> None:
        oid = leg.active_order_id
        if oid is None:
            return
        st = self._broker.order_status(oid)
        prev = leg.orders.get(oid)
        leg.orders[oid] = st
        if prev is None or prev.filled_qty != st.filled_qty or prev.status != st.status:
            self._log.info("FILL-STATUS %s %s id=%s status=%s filled=%d/%d avg=%.2f %s",
                           leg.side, leg.contract.tradingsymbol, oid, st.status,
                           st.filled_qty, st.quantity, st.avg_price, st.message)
        if not st.is_open:
            leg.active_order_id = None
            if st.is_failed:
                leg.last_error = f"{st.status}: {st.message}"

    def _reprice(self, leg: LegWork, interval: float) -> None:
        oid = leg.active_order_id
        if oid is None or self._clock() - leg.last_reprice_at < interval:
            return
        ltp = self._broker.ltp([leg.contract])[leg.contract.tradingsymbol]
        new_price = self.marketable_price(ltp, leg.side, leg.contract.tick_size)
        leg.last_reprice_at = self._clock()
        current = leg.orders.get(oid)
        if current is not None and abs(current.price - new_price) < 1e-9:
            return
        try:
            self._broker.modify_price(oid, new_price)
            self._log.info("REPRICE %s %s id=%s -> %.2f (ltp %.2f)", leg.side, leg.contract.tradingsymbol, oid, new_price, ltp)
        except OrderError as exc:
            self._log.warning("Reprice skipped for %s: %s", oid, exc)

    def _cancel_and_settle(self, legs: list[LegWork], settle_seconds: float = 5.0) -> None:
        for leg in legs:
            if leg.active_order_id:
                try:
                    self._broker.cancel(leg.active_order_id)
                    self._log.info("CANCEL requested id=%s", leg.active_order_id)
                except OrderError as exc:
                    self._log.warning("Cancel failed (may have filled): %s", exc)
        deadline = self._clock() + settle_seconds
        while any(leg.active_order_id for leg in legs) and self._clock() < deadline:
            for leg in legs:
                try:
                    self._refresh(leg)
                except TRANSIENT_ERRORS as exc:
                    self._log.warning("Refresh error while settling: %s", exc)
            self._sleep(self._cfg.order_poll_interval_seconds)
        for leg in legs:
            if leg.active_order_id:
                alert(self._log, f"Order {leg.active_order_id} still not terminal after cancel — CHECK KITE MANUALLY")

    def enter_straddle(self, ce: OptionContract, pe: OptionContract, qty: int) -> list[LegWork]:
        legs = [LegWork(ce, SELL, qty), LegWork(pe, SELL, qty)]
        self.entry_legs = legs
        for leg in legs:
            try:
                self._place(leg, "E")
            except (OrderError, *TRANSIENT_ERRORS) as exc:
                leg.last_error = str(exc)
                self._log.error("Entry order failed for %s: %s", leg.contract.tradingsymbol, exc)

        deadline = self._clock() + self._cfg.leg_fill_timeout_seconds
        while self._clock() < deadline:
            for leg in legs:
                try:
                    self._refresh(leg)
                except TRANSIENT_ERRORS as exc:
                    self._log.warning("Status check failed: %s", exc)
            if all(leg.remaining == 0 for leg in legs):
                self._log.info("ENTRY COMPLETE: %s", ", ".join(
                    f"{l.contract.tradingsymbol} SELL {l.filled_qty} @ {l.avg_price:.2f}" for l in legs))
                return legs
            if any(leg.active_order_id is None and leg.remaining > 0 for leg in legs):
                break
            for leg in legs:
                self._reprice(leg, self._cfg.entry_reprice_interval_seconds)
            self._sleep(self._cfg.order_poll_interval_seconds)

        reasons = "; ".join(f"{l.contract.tradingsymbol}: filled {l.filled_qty}/{l.target_qty} {l.last_error}" for l in legs)
        alert(self._log, f"ENTRY FAILED — squaring off any filled quantity. {reasons}")
        self._cancel_and_settle(legs)
        to_close = [(l.contract, l.filled_qty) for l in legs if l.filled_qty > 0]
        exit_legs = self.exit_legs(to_close, "LEG_RISK") if to_close else []
        raise LegRiskError(f"Entry aborted: {reasons}", legs, exit_legs)

    def exit_legs(self, to_close: list[tuple[OptionContract, int]], reason: str) -> list[LegWork]:
        legs = [LegWork(c, BUY, q) for c, q in to_close if q > 0]
        self._log.info("EXIT START reason=%s legs=%s", reason, [(l.contract.tradingsymbol, l.target_qty) for l in legs])
        start = self._clock()
        last_alert = -1e9
        while True:
            had_error = False
            for leg in legs:
                if leg.remaining <= 0 and leg.active_order_id is None:
                    continue
                try:
                    self._refresh(leg)
                    if leg.active_order_id is None and leg.remaining > 0:
                        self._place(leg, "X")
                    else:
                        self._reprice(leg, self._cfg.exit_reprice_interval_seconds)
                except Exception as exc:
                    had_error = True
                    leg.last_error = str(exc)
                    self._log.error("Exit error on %s: %s", leg.contract.tradingsymbol, exc)

            if all(leg.remaining <= 0 and leg.active_order_id is None for leg in legs):
                self._log.info("EXIT COMPLETE: %s", ", ".join(
                    f"{l.contract.tradingsymbol} BUY {l.filled_qty} @ {l.avg_price:.2f}" for l in legs))
                return legs

            struggling = any(l.attempts > self._cfg.exit_attempts_before_alert for l in legs) or self._clock() - start > 30
            if struggling and self._clock() - last_alert > 10:
                alert(self._log, "EXIT NOT COMPLETE — still retrying. Open: " + ", ".join(
                    f"{l.contract.tradingsymbol} rem={l.remaining} err={l.last_error}" for l in legs if l.remaining > 0))
                last_alert = self._clock()
            self._sleep(1.0 if had_error else self._cfg.order_poll_interval_seconds)

    def flatten_from_positions(self, contracts: list[OptionContract], reason: str) -> list[LegWork]:
        self._cancel_and_settle([l for l in self.entry_legs if l.active_order_id])
        positions = self._broker.net_positions()
        to_close = [(c, -positions.get(c.tradingsymbol, 0)) for c in contracts if positions.get(c.tradingsymbol, 0) < 0]
        if not to_close:
            self._log.info("Emergency flatten: no open short positions found")
            return []
        return self.exit_legs(to_close, reason)