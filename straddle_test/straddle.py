from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto


class State(Enum):
    OPEN = auto()
    CLOSED = auto()


@dataclass
class StraddleConfig:
    ce_symbol: str
    pe_symbol: str
    lot_size: int
    basis: str = "combined"          # "combined" or "per_leg"
    target_pct: float = 0.01
    stop_pct: float = 0.0125
    square_off_time: tuple = (15, 15)


@dataclass
class ExitEvent:
    reason: str
    pnl_rupees: float = 0.0


class Leg:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.open = True
        self.entry_price = 0.0


class LongStraddle:
    def __init__(self, config: StraddleConfig, broker):
        self.config = config
        self.broker = broker
        self.state = State.OPEN
        self.ce = Leg(config.ce_symbol)
        self.pe = Leg(config.pe_symbol)
        self.entry_combined = 0.0

    def enter(self, now: datetime):
        oid1 = self.broker.place_market_order(self.ce.symbol, "BUY", self.config.lot_size, "MIS")
        oid2 = self.broker.place_market_order(self.pe.symbol, "BUY", self.config.lot_size, "MIS")
        self.ce.entry_price = self.broker.get_avg_fill_price(oid1)
        self.pe.entry_price = self.broker.get_avg_fill_price(oid2)
        self.entry_combined = self.ce.entry_price + self.pe.entry_price
        self.state = State.OPEN

    def on_tick(self, ce_ltp: float, pe_ltp: float, now: datetime):
        if self.state is State.CLOSED:
            return None

        # Time square-off
        if (now.hour, now.minute) >= self.config.square_off_time:
            return self._close_all("TIME_SQUARE_OFF", ce_ltp, pe_ltp)

        if self.config.basis == "combined":
            combined = ce_ltp + pe_ltp
            change_pct = (combined - self.entry_combined) / self.entry_combined

            if change_pct >= self.config.target_pct:
                return self._close_all("TARGET", ce_ltp, pe_ltp)
            if change_pct <= -self.config.stop_pct:
                return self._close_all("STOPLOSS", ce_ltp, pe_ltp)

        elif self.config.basis == "per_leg":
            if self.ce.open and ce_ltp >= self.ce.entry_price * (1 + self.config.target_pct):
                self.ce.open = False
            if self.pe.open and pe_ltp >= self.pe.entry_price * (1 + self.config.target_pct):
                self.pe.open = False
            if not self.ce.open and not self.pe.open:
                self.state = State.CLOSED
                return ExitEvent("BOTH_LEGS_CLOSED")

        return None

    def _close_all(self, reason: str, ce_ltp: float, pe_ltp: float):
        self.ce.open = False
        self.pe.open = False
        self.state = State.CLOSED
        pnl = ((ce_ltp - self.ce.entry_price) + (pe_ltp - self.pe.entry_price)) * self.config.lot_size
        return ExitEvent(reason=reason, pnl_rupees=pnl)