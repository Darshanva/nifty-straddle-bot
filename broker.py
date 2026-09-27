"""
broker.py — one interface, two implementations:
  LiveBroker  : real orders through Kite Connect.
  PaperBroker : simulated fills using LIVE market prices.
"""
from __future__ import annotations

import itertools
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

from kiteconnect import exceptions as kex
from requests.exceptions import RequestException
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from exceptions import OrderError
from instruments import OptionContract

BUY = "BUY"
SELL = "SELL"
STATUS_COMPLETE = "COMPLETE"
FAILED_STATUSES = frozenset({"REJECTED", "CANCELLED"})

TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    kex.NetworkException, kex.DataException, kex.GeneralException, RequestException,
)

read_retry = retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=0.3, max=3),
    retry=retry_if_exception_type(TRANSIENT_ERRORS),
)


@dataclass(frozen=True)
class OrderStatus:
    order_id: str
    status: str
    quantity: int
    filled_qty: int
    pending_qty: int
    avg_price: float
    price: float
    message: str = ""

    @property
    def is_complete(self) -> bool:
        return self.status == STATUS_COMPLETE

    @property
    def is_failed(self) -> bool:
        return self.status in FAILED_STATUSES

    @property
    def is_open(self) -> bool:
        return not (self.is_complete or self.is_failed)


class Broker(Protocol):
    def place_limit(self, contract: OptionContract, side: str, qty: int, price: float, tag: str) -> str: ...
    def order_status(self, order_id: str) -> OrderStatus: ...
    def modify_price(self, order_id: str, price: float) -> None: ...
    def cancel(self, order_id: str) -> None: ...
    def ltp(self, contracts: list[OptionContract]) -> dict[str, float]: ...
    def net_positions(self) -> dict[str, int]: ...


class _KiteQuotes:
    def __init__(self, kite: Any, logger: logging.Logger) -> None:
        self._kite = kite
        self._log = logger

    @read_retry
    def ltp(self, contracts: list[OptionContract]) -> dict[str, float]:
        quotes = self._kite.ltp([c.exchange_symbol for c in contracts])
        return {c.tradingsymbol: float(quotes[c.exchange_symbol]["last_price"]) for c in contracts}


class LiveBroker(_KiteQuotes):
    def __init__(self, kite: Any, logger: logging.Logger, algo_id: str | None = None) -> None:
        super().__init__(kite, logger)
        self._algo_id = algo_id

    def place_limit(self, contract: OptionContract, side: str, qty: int, price: float, tag: str) -> str:
        params: dict[str, Any] = dict(
            variety=self._kite.VARIETY_REGULAR,
            exchange=contract.exchange,
            tradingsymbol=contract.tradingsymbol,
            transaction_type=side,
            quantity=qty,
            product=self._kite.PRODUCT_MIS,
            order_type=self._kite.ORDER_TYPE_LIMIT,
            price=price,
            validity=self._kite.VALIDITY_DAY,
            tag=tag,
        )
        if self._algo_id:
            params["algo_id"] = self._algo_id
        try:
            return str(self._kite.place_order(**params))
        except (kex.TokenException, kex.PermissionException, kex.InputException, kex.OrderException) as exc:
            raise OrderError(f"{side} {contract.tradingsymbol} rejected by API: {exc}") from exc
        except TRANSIENT_ERRORS as exc:
            self._log.warning("Ambiguous place_order failure (%s); reconciling by tag %s", exc, tag)
            found = self._find_order_by_tag(tag)
            if found:
                self._log.info("Order with tag %s exists: %s", tag, found)
                return found
            raise OrderError(f"{side} {contract.tradingsymbol} not placed: {exc}") from exc

    @read_retry
    def _orders(self) -> list[dict[str, Any]]:
        return list(self._kite.orders())

    def _find_order_by_tag(self, tag: str) -> str | None:
        time.sleep(1.0)
        try:
            for o in self._orders():
                tags = o.get("tags") or []
                if o.get("tag") == tag or tag in tags:
                    return str(o["order_id"])
        except TRANSIENT_ERRORS as exc:
            self._log.error("Could not reconcile orders by tag: %s", exc)
        return None

    @read_retry
    def order_status(self, order_id: str) -> OrderStatus:
        last = self._kite.order_history(order_id)[-1]
        return OrderStatus(
            order_id=order_id,
            status=str(last["status"]),
            quantity=int(last.get("quantity") or 0),
            filled_qty=int(last.get("filled_quantity") or 0),
            pending_qty=int(last.get("pending_quantity") or 0),
            avg_price=float(last.get("average_price") or 0.0),
            price=float(last.get("price") or 0.0),
            message=str(last.get("status_message") or ""),
        )

    def modify_price(self, order_id: str, price: float) -> None:
        try:
            self._kite.modify_order(variety=self._kite.VARIETY_REGULAR, order_id=order_id,
                                    price=price, order_type=self._kite.ORDER_TYPE_LIMIT)
        except (kex.KiteException, RequestException) as exc:
            raise OrderError(f"modify {order_id} failed: {exc}") from exc

    def cancel(self, order_id: str) -> None:
        try:
            self._kite.cancel_order(variety=self._kite.VARIETY_REGULAR, order_id=order_id)
        except (kex.KiteException, RequestException) as exc:
            raise OrderError(f"cancel {order_id} failed: {exc}") from exc

    @read_retry
    def net_positions(self) -> dict[str, int]:
        net = self._kite.positions().get("net", [])
        return {p["tradingsymbol"]: int(p["quantity"]) for p in net if p.get("exchange") == "NFO"}


@dataclass
class _PaperOrder:
    order_id: str
    contract: OptionContract
    side: str
    qty: int
    price: float
    status: str = "OPEN"
    filled_qty: int = 0
    avg_price: float = 0.0


class PaperBroker(_KiteQuotes):
    def __init__(self, kite: Any, logger: logging.Logger) -> None:
        super().__init__(kite, logger)
        self._orders: dict[str, _PaperOrder] = {}
        self._positions: dict[str, int] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def _try_fill(self, o: _PaperOrder) -> None:
        if o.status != "OPEN":
            return
        ltp = self.ltp([o.contract])[o.contract.tradingsymbol]
        marketable = (o.side == SELL and o.price <= ltp) or (o.side == BUY and o.price >= ltp)
        if marketable:
            o.status, o.filled_qty, o.avg_price = STATUS_COMPLETE, o.qty, o.price
            delta = o.qty if o.side == BUY else -o.qty
            sym = o.contract.tradingsymbol
            self._positions[sym] = self._positions.get(sym, 0) + delta

    def place_limit(self, contract: OptionContract, side: str, qty: int, price: float, tag: str) -> str:
        with self._lock:
            oid = f"PAPER-{next(self._ids)}"
            order = _PaperOrder(oid, contract, side, qty, price)
            self._orders[oid] = order
            self._try_fill(order)
            self._log.info("[PAPER] %s %s x%d @ %.2f tag=%s -> %s", side, contract.tradingsymbol, qty, price, tag, order.status)
            return oid

    def order_status(self, order_id: str) -> OrderStatus:
        with self._lock:
            o = self._orders[order_id]
            self._try_fill(o)
            return OrderStatus(o.order_id, o.status, o.qty, o.filled_qty, o.qty - o.filled_qty, o.avg_price, o.price)

    def modify_price(self, order_id: str, price: float) -> None:
        with self._lock:
            o = self._orders[order_id]
            if o.status == "OPEN":
                o.price = price
                self._try_fill(o)

    def cancel(self, order_id: str) -> None:
        with self._lock:
            o = self._orders[order_id]
            if o.status == "OPEN":
                o.status = "CANCELLED"

    def net_positions(self) -> dict[str, int]:
        with self._lock:
            return dict(self._positions)