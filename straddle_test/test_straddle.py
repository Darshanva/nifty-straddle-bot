from datetime import datetime
import itertools
import pytest
from straddle import LongStraddle, StraddleConfig, State


class FakeBroker:
    def __init__(self, prices):
        self.prices = prices
        self.ids = itertools.count()
        self.orders = {}

    def place_market_order(self, symbol, side, qty, product):
        oid = str(next(self.ids))
        self.orders[oid] = (symbol, side)
        return oid

    def get_avg_fill_price(self, oid):
        return self.prices[self.orders[oid][0]]


T = datetime(2026, 10, 6, 10, 0)


def make(basis="combined"):
    b = FakeBroker({"CE": 100.0, "PE": 100.0})
    s = LongStraddle(StraddleConfig("CE", "PE", lot_size=65, basis=basis), b)
    s.enter(T)
    return s, b


def test_combined_target_hit():
    s, b = make()
    b.prices.update(CE=103.0, PE=99.0)            # +3 -1 = +2 pts on 200 premium = +1%
    ev = s.on_tick(103.0, 99.0, T)
    assert ev.reason == "TARGET" and s.state is State.CLOSED
    assert ev.pnl_rupees == pytest.approx(2 * 65)


def test_combined_stop_hit():
    s, b = make()
    b.prices.update(CE=98.0, PE=99.5)             # -2.5 pts = -1.25%
    assert s.on_tick(98.0, 99.5, T).reason == "STOPLOSS"


def test_no_exit_inside_band():
    s, _ = make()
    assert s.on_tick(100.5, 100.2, T) is None and s.state is State.OPEN


def test_time_square_off():
    s, _ = make()
    assert s.on_tick(100, 100, T.replace(hour=15, minute=16)).reason == "TIME_SQUARE_OFF"


def test_per_leg_independent():
    s, b = make("per_leg")
    b.prices.update(CE=101.5)
    s.on_tick(101.5, 100.0, T)
    assert not s.ce.open and s.pe.open and s.state is State.OPEN