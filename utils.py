"""
utils.py — time (IST), logging, alerts, kill switch, holiday calendar, daily state.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
LOGGER_NAME = "straddle"


def now_ist() -> datetime:
    return datetime.now(IST)


def at_today(t: dtime, ref: datetime | None = None) -> datetime:
    ref = ref or now_ist()
    return datetime.combine(ref.date(), t, tzinfo=IST)


class _ISTFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, IST)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S") + f".{int(record.msecs):03d} IST"


def setup_logging(log_dir: Path, day: date, mode: str) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    fmt = _ISTFormatter("%(asctime)s | %(levelname)-8s | %(message)s")
    file_handler = logging.FileHandler(log_dir / f"straddle_{mode.lower()}_{day.isoformat()}.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.addHandler(console)
    return logger


def alert(logger: logging.Logger, message: str) -> None:
    logger.critical("*** ALERT *** %s", message)
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except OSError:
        pass


def kill_switch_active(kill_file: Path, env_name: str) -> bool:
    return kill_file.exists() or os.environ.get(env_name, "0").strip() == "1"


def load_holidays(path: Path) -> set[date] | None:
    if not path.exists():
        return None
    days: set[date] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            days.add(date.fromisoformat(line))
    return days


def is_trading_day(day: date, holidays: set[date]) -> bool:
    return day.weekday() < 5 and day not in holidays


class DailyState:
    def __init__(self, state_dir: Path, day: date, mode: str) -> None:
        state_dir.mkdir(parents=True, exist_ok=True)
        self._path = state_dir / f"state_{mode.lower()}_{day.isoformat()}.json"
        self._data: dict[str, Any] = {}
        if self._path.exists():
            self._data = json.loads(self._path.read_text(encoding="utf-8"))

    @property
    def trade_attempted(self) -> bool:
        return bool(self._data.get("trade_attempted", False))

    def update(self, **fields: Any) -> None:
        self._data.update(fields)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, default=str), encoding="utf-8")
        tmp.replace(self._path)