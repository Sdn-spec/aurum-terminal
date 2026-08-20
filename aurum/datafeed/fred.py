"""Macro data via FRED (Federal Reserve Economic Data) — free, no
application review, generous rate limits. Only pulls a small curated set
of series that matter for a regime read: the effective fed funds rate,
headline CPI, unemployment, and the 10-year Treasury yield. Nothing here
is symbol-specific; this is shared macro context every Analyze report can
fold in, not something fetched per instrument.

Needs a free key from https://fredaccount.stlouisfed.org/apikeys, set as
FRED_API_KEY or {"fred_api_key": "..."} in data/config.json — the same
file and resolution order aurum.datafeed.provider already uses for the
Twelve Data key.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import List, Optional, Tuple

from . import provider
from .yahoo import DataFeedError

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# Series id + a display label kept alongside it, since fetching FRED's own
# metadata would be a second (rate-limited) call just for a name.
SERIES = {
    "fed_funds_rate": ("DFF", "Federal funds effective rate"),
    "cpi": ("CPIAUCSL", "CPI, all urban consumers (index)"),
    "unemployment": ("UNRATE", "Unemployment rate"),
    "treasury_10y": ("DGS10", "10-year Treasury yield"),
}


@dataclass
class MacroSeries:
    key: str
    series_id: str
    label: str
    latest_date: str
    latest_value: float
    previous_value: Optional[float]  # one observation back, for a simple up/down read


def resolve_api_key() -> Optional[str]:
    key = os.environ.get("FRED_API_KEY")
    if key:
        return key
    if provider.CONFIG_PATH.exists():
        try:
            return json.loads(provider.CONFIG_PATH.read_text()).get("fred_api_key")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def get_series(series_id: str, api_key: str, limit: int = 6) -> List[Tuple[str, float]]:
    """Most recent `limit` observations, oldest first. FRED marks a missing
    reading as the string "." (holidays, series lag) — those are dropped."""
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    }
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "aurum-terminal/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as e:
        raise DataFeedError(f"FRED returned HTTP {e.code} for {series_id}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        # a mid-read timeout (as opposed to connect-time) comes back from
        # urllib as a bare TimeoutError, not wrapped in URLError -- this is
        # exactly what crashed the whole Analyze report once, live, before
        # this was caught here (see aurum.datafeed.yahoo for the same fix
        # with a retry, and the regression test there for the real traceback)
        reason = getattr(e, "reason", e)
        raise DataFeedError(f"Could not reach FRED for {series_id}: {reason}") from e
    except json.JSONDecodeError as e:
        raise DataFeedError(f"FRED returned unparseable data for {series_id}") from e

    if isinstance(payload, dict) and "error_message" in payload:
        raise DataFeedError(f"FRED error for {series_id}: {payload['error_message']}")

    observations = [(o["date"], o["value"]) for o in payload.get("observations", []) if o.get("value") != "."]
    observations.reverse()  # FRED gives newest-first; flip to oldest-first
    return [(d, float(v)) for d, v in observations]


def get_macro_snapshot(api_key: str) -> List[MacroSeries]:
    """One FRED call per curated series — a small fixed set, not per-symbol.
    A series that fails or comes back empty is skipped rather than failing
    the whole snapshot, so one bad/renamed series id doesn't take out the
    other three."""
    results = []
    for key, (series_id, label) in SERIES.items():
        try:
            observations = get_series(series_id, api_key, limit=6)
        except DataFeedError:
            continue
        if not observations:
            continue
        latest_date, latest_value = observations[-1]
        previous_value = observations[-2][1] if len(observations) >= 2 else None
        results.append(
            MacroSeries(
                key=key,
                series_id=series_id,
                label=label,
                latest_date=latest_date,
                latest_value=latest_value,
                previous_value=previous_value,
            )
        )
    return results
