"""
charges.py — ESTIMATED transaction charges
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from config import ChargesConfig


@dataclass(frozen=True)
class Execution:
    side: str
    price: float
    qty: int


@dataclass(frozen=True)
class ChargesBreakdown:
    brokerage: float
    stt: float
    exchange_txn: float
    sebi_fee: float
    gst: float
    stamp_duty: float

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.exchange_txn + self.sebi_fee + self.gst + self.stamp_duty

    def as_dict(self) -> dict[str, float]:
        d = {k: round(v, 2) for k, v in asdict(self).items()}
        d["total"] = round(self.total, 2)
        return d


def estimate_charges(executions: list[Execution], rates: ChargesConfig) -> ChargesBreakdown:
    buy_turnover = sum(e.price * e.qty for e in executions if e.side == "BUY")
    sell_turnover = sum(e.price * e.qty for e in executions if e.side == "SELL")
    turnover = buy_turnover + sell_turnover
    brokerage = rates.brokerage_per_order * sum(1 for e in executions if e.qty > 0)
    stt = sell_turnover * rates.stt_sell_pct
    exchange_txn = turnover * rates.exchange_txn_pct
    sebi_fee = turnover * rates.sebi_fee_per_crore / 1e7
    gst = (brokerage + exchange_txn + sebi_fee) * rates.gst_pct
    stamp = buy_turnover * rates.stamp_duty_buy_pct
    return ChargesBreakdown(brokerage, stt, exchange_txn, sebi_fee, gst, stamp)