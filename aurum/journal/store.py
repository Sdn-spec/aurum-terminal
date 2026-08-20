"""Persists the paper-trading journal to data/journal.json (gitignored, same
pattern as alerts.json/watchlist.json/state.json). This is the "Bullion
Ledger" journal — logged trades against a starting paper-trading balance,
with the setup/notes/confidence fields that make a win-rate-by-setup
breakdown possible — given permanent server-side storage here instead of
the browser localStorage it used as a standalone page, so the log survives
across devices and browsers like the rest of this app's data.
"""

import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

JOURNAL_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "journal.json"
DEFAULT_STARTING_EQUITY = 3000.0


@dataclass
class Trade:
    id: str
    ts: str
    instrument: str
    direction: str  # "long" | "short"
    entry: Optional[float]
    exit: Optional[float]
    size: Optional[float]
    pnl: float
    risk: Optional[float]
    ema: Optional[float]
    trend: Optional[str]  # "up" | "down" | "flat" | None
    setup: str
    notes: str
    confidence: Optional[int]


class TradeNotFoundError(ValueError):
    pass


def _load() -> dict:
    if JOURNAL_PATH.exists():
        try:
            data = json.loads(JOURNAL_PATH.read_text())
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"starting_equity": DEFAULT_STARTING_EQUITY, "trades": []}


def _save(data: dict) -> None:
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    JOURNAL_PATH.write_text(json.dumps(data, indent=2))


def load_journal() -> dict:
    data = _load()
    return {
        "starting_equity": data.get("starting_equity", DEFAULT_STARTING_EQUITY),
        "trades": [Trade(**row) for row in data.get("trades", [])],
    }


def add_trade(
    instrument: str = "",
    direction: str = "",
    pnl=None,
    ts: str = "",
    entry=None,
    exit=None,
    size=None,
    risk=None,
    ema=None,
    trend=None,
    setup: Optional[str] = None,
    notes: str = "",
    confidence=None,
    **_ignored,
) -> Trade:
    instrument = instrument.strip()
    if not instrument:
        raise ValueError("instrument can't be empty")
    if direction not in ("long", "short"):
        raise ValueError('direction must be "long" or "short"')
    if pnl is None:
        raise ValueError("pnl is required")
    if not ts:
        raise ValueError("ts is required")
    data = _load()
    trade = Trade(
        id=uuid.uuid4().hex[:10],
        ts=ts,
        instrument=instrument,
        direction=direction,
        entry=entry,
        exit=exit,
        size=size,
        pnl=float(pnl),
        risk=risk,
        ema=ema,
        trend=trend,
        setup=setup or "none",
        notes=notes or "",
        confidence=confidence,
    )
    data.setdefault("trades", []).append(asdict(trade))
    _save(data)
    return trade


def remove_trade(trade_id: str) -> None:
    data = _load()
    trades = data.get("trades", [])
    remaining = [row for row in trades if row.get("id") != trade_id]
    if len(remaining) == len(trades):
        raise TradeNotFoundError(f"trade {trade_id} not found")
    data["trades"] = remaining
    _save(data)
