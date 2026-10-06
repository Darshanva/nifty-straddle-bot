from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import Optional


class State(Enum):
    IDLE = auto()
    OPEN = auto()
    CLOSED = auto()


@dataclass
class StraddleConfig:
    ce_symbol: str = "CE"
    pe_symbol: str = "PE"
    lot_size: int = 65
    basis: str = "combined"
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
        self.open = False
        self.entry_price = 0.0


class LongStraddle:
    def __init__(self, config: StraddleConfig):
        self.config = config
        self.state = State.IDLE
        self.ce = Leg(config.ce_symbol)
        self.pe = Leg(config.pe_symbol)
        self.entry_combined = 0.0
        self.last_event: Optional[ExitEvent] = None
        self.entry_time: Optional[datetime] = None

    def enter(self, ce_price: float, pe_price: float, now: datetime):
        self.ce.entry_price = ce_price
        self.pe.entry_price = pe_price
        self.ce.open = True
        self.pe.open = True
        self.entry_combined = ce_price + pe_price
        self.state = State.OPEN
        self.entry_time = now
        self.last_event = None

    def on_tick(self, ce_ltp: float, pe_ltp: float, now: datetime) -> Optional[ExitEvent]:
        if self.state is not State.OPEN:
            return None

        if (now.hour, now.minute) >= self.config.square_off_time:
            return self._close_all("TIME_SQUARE_OFF", ce_ltp, pe_ltp)

        if self.config.basis == "combined":
            combined = ce_ltp + pe_ltp
            change_pct = (combined - self.entry_combined) / self.entry_combined
            if change_pct >= self.config.target_pct:
                return self._close_all("TARGET", ce_ltp, pe_ltp)
            if change_pct <= -self.config.stop_pct:
                return self._close_all("STOPLOSS", ce_ltp, pe_ltp)

        return None

    def _close_all(self, reason: str, ce_ltp: float, pe_ltp: float) -> ExitEvent:
        self.ce.open = False
        self.pe.open = False
        self.state = State.CLOSED
        pnl = ((ce_ltp - self.ce.entry_price) + (pe_ltp - self.pe.entry_price)) * self.config.lot_size
        event = ExitEvent(reason=reason, pnl_rupees=pnl)
        self.last_event = event
        return event

    def status(self) -> dict:
        return {
            "state": self.state.name,
            "entry_combined": self.entry_combined,
            "ce_entry": self.ce.entry_price,
            "pe_entry": self.pe.entry_price,
            "last_event": self.last_event.reason if self.last_event else None,
            "last_pnl": self.last_event.pnl_rupees if self.last_event else None,
        }