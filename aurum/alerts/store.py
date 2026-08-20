"""Persists user-created price alerts and the last Analyze verdict seen per
symbol, to data/alerts.json (gitignored, same pattern as state.json,
watchlist.json, and config.json).

Two independent concerns share one file for simplicity:
- price_alerts: explicit "tell me when GOLD crosses $4500" rules the user
  sets; a real background poller would be needed to check these against
  live prices continuously, which this app doesn't have — instead the
  frontend checks them client-side during the watchlist's existing live
  quote poll (see app.js), and this module is just the persisted rules.
- last_verdicts: the Analyze verdict last seen per symbol, so the next time
  a report is built it can say "this changed since you last looked" without
  needing a background poller either — the comparison happens the next
  time someone actually opens that symbol.
"""

import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

ALERTS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "alerts.json"


@dataclass
class PriceAlert:
    id: str
    symbol: str
    condition: str  # "above" | "below"
    price: float


class AlertNotFoundError(ValueError):
    pass


def _load() -> dict:
    if ALERTS_PATH.exists():
        try:
            data = json.loads(ALERTS_PATH.read_text())
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"price_alerts": [], "last_verdicts": {}}


def _save(data: dict) -> None:
    ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ALERTS_PATH.write_text(json.dumps(data, indent=2))


def load_price_alerts() -> List[PriceAlert]:
    data = _load()
    return [PriceAlert(**row) for row in data.get("price_alerts", [])]


def add_price_alert(symbol: str, condition: str, price: float) -> PriceAlert:
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("symbol can't be empty")
    if condition not in ("above", "below"):
        raise ValueError('condition must be "above" or "below"')
    if price <= 0:
        raise ValueError("price must be positive")
    data = _load()
    alert = PriceAlert(id=uuid.uuid4().hex[:10], symbol=symbol, condition=condition, price=price)
    data.setdefault("price_alerts", []).append(asdict(alert))
    _save(data)
    return alert


def remove_price_alert(alert_id: str) -> None:
    data = _load()
    alerts = data.get("price_alerts", [])
    remaining = [row for row in alerts if row.get("id") != alert_id]
    if len(remaining) == len(alerts):
        raise AlertNotFoundError(f"alert {alert_id} not found")
    data["price_alerts"] = remaining
    _save(data)


def get_last_verdict(symbol: str) -> Optional[str]:
    data = _load()
    return data.get("last_verdicts", {}).get(symbol.strip().upper())


def set_last_verdict(symbol: str, verdict: str) -> None:
    data = _load()
    data.setdefault("last_verdicts", {})[symbol.strip().upper()] = verdict
    _save(data)
