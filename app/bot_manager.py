from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import json
from strategies.long_straddle import LongStraddle, StraddleConfig, State

IST = ZoneInfo("Asia/Kolkata")
STATE_FILE = Path(__file__).resolve().parent.parent / "state" / "long_straddle_state.json"


class BotManager:
    def __init__(self):
        self.strategy = LongStraddle(StraddleConfig())
        self.running = False
        self.ce_symbol = None
        self.pe_symbol = None
        self.ce_token = None
        self.pe_token = None
        self.entry_time = None
        self.mode = "PAPER"
        self._load_state()

    def _save_state(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "running": self.running,
            "mode": self.mode,
            "ce_symbol": self.ce_symbol,
            "pe_symbol": self.pe_symbol,
            "ce_token": self.ce_token,
            "pe_token": self.pe_token,
            "status": self.strategy.status(),
            "entry_time": str(self.entry_time) if self.entry_time else None,
        }
        STATE_FILE.write_text(json.dumps(data, indent=2, default=str))

    def _load_state(self):
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                self.running = data.get("running", False)
                self.ce_symbol = data.get("ce_symbol")
                self.pe_symbol = data.get("pe_symbol")
                self.ce_token = data.get("ce_token")
                self.pe_token = data.get("pe_token")
            except:
                pass

    def start_paper(self, ce_symbol, pe_symbol, ce_token, pe_token, ce_price, pe_price):
        self.mode = "PAPER"
        self.ce_symbol = ce_symbol
        self.pe_symbol = pe_symbol
        self.ce_token = ce_token
        self.pe_token = pe_token
        self.strategy = LongStraddle(StraddleConfig(
            ce_symbol=ce_symbol,
            pe_symbol=pe_symbol,
            lot_size=65,
            basis="combined",
            target_pct=0.01,
            stop_pct=0.0125
        ))
        now = datetime.now(IST)
        self.strategy.enter(ce_price, pe_price, now)
        self.entry_time = now
        self.running = True
        self._save_state()
        return True

    def stop(self):
        self.running = False
        if self.strategy.state == State.OPEN:
            self.strategy.state = State.CLOSED
        self._save_state()

    def on_prices(self, ce_ltp: float, pe_ltp: float):
        if not self.running or self.strategy.state != State.OPEN:
            return None
        event = self.strategy.on_tick(ce_ltp, pe_ltp, datetime.now(IST))
        self._save_state()
        return event

    def get_status(self):
        return {
            "running": self.running,
            "mode": self.mode,
            "ce_symbol": self.ce_symbol,
            "pe_symbol": self.pe_symbol,
            **self.strategy.status()
        }


bot_manager = BotManager()