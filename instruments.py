"""
instruments.py — resolve the ATM CE/PE contracts
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable

from exceptions import InstrumentError


@dataclass(frozen=True)
class OptionContract:
    tradingsymbol: str
    instrument_token: int
    exchange: str
    strike: float
    expiry: date
    option_type: str
    lot_size: int
    tick_size: float

    @property
    def exchange_symbol(self) -> str:
        return f"{self.exchange}:{self.tradingsymbol}"


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    raise InstrumentError(f"Unparseable expiry value: {value!r}")


def atm_strike(spot: float, step: int) -> int:
    if spot <= 0 or step <= 0:
        raise InstrumentError(f"Invalid spot {spot} or step {step}")
    return int(math.floor(spot / step + 0.5) * step)


def choose_expiry(expiries: Iterable[date], today: date, trade_on_expiry_day: bool) -> date:
    upcoming = sorted({e for e in expiries if e >= today})
    if not upcoming:
        raise InstrumentError("No upcoming expiries in instruments dump")
    if upcoming[0] == today and not trade_on_expiry_day:
        if len(upcoming) < 2:
            raise InstrumentError("Expiry day and no next expiry available")
        return upcoming[1]
    return upcoming[0]


def _to_contract(rec: dict[str, Any]) -> OptionContract:
    return OptionContract(
        tradingsymbol=str(rec["tradingsymbol"]),
        instrument_token=int(rec["instrument_token"]),
        exchange=str(rec.get("exchange") or "NFO"),
        strike=float(rec["strike"]),
        expiry=_to_date(rec["expiry"]),
        option_type=str(rec["instrument_type"]),
        lot_size=int(rec["lot_size"]),
        tick_size=float(rec.get("tick_size") or 0.05),
    )


def select_straddle(
    instruments: list[dict[str, Any]],
    underlying: str,
    spot: float,
    strike_step: int,
    today: date,
    trade_on_expiry_day: bool,
) -> tuple[OptionContract, OptionContract]:
    options = [
        r for r in instruments
        if r.get("name") == underlying
        and r.get("segment") == "NFO-OPT"
        and r.get("instrument_type") in ("CE", "PE")
    ]
    if not options:
        raise InstrumentError(f"No {underlying} options found in instruments dump")

    expiry = choose_expiry((_to_date(r["expiry"]) for r in options), today, trade_on_expiry_day)

    by_strike: dict[float, dict[str, dict[str, Any]]] = {}
    for r in options:
        if _to_date(r["expiry"]) == expiry:
            by_strike.setdefault(float(r["strike"]), {})[r["instrument_type"]] = r

    both_legs = [s for s, legs in by_strike.items() if "CE" in legs and "PE" in legs]
    if not both_legs:
        raise InstrumentError(f"No strike with both CE and PE for expiry {expiry}")

    target = atm_strike(spot, strike_step)
    strike = min(both_legs, key=lambda s: (abs(s - target), s))
    ce = _to_contract(by_strike[strike]["CE"])
    pe = _to_contract(by_strike[strike]["PE"])
    if ce.lot_size <= 0 or ce.lot_size != pe.lot_size:
        raise InstrumentError(f"Bad lot sizes: CE {ce.lot_size}, PE {pe.lot_size}")
    return ce, pe