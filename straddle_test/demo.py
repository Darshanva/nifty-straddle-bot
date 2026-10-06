from datetime import datetime
from straddle import LongStraddle, StraddleConfig, State
import itertools

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

broker = FakeBroker({"CE": 100.0, "PE": 100.0})
config = StraddleConfig("CE", "PE", lot_size=65, basis="combined")
strategy = LongStraddle(config, broker)

T = datetime(2026, 10, 6, 10, 0)
strategy.enter(T)
print("Entry Combined:", strategy.entry_combined)

# Stop Loss scenario
event = strategy.on_tick(98.0, 99.5, T)
print("\n--- Stop Loss Case ---")
print("Event:", event.reason if event else None)
print("PnL:", event.pnl_rupees if event else None)
print("State:", strategy.state)