"""Picks a data source. Yahoo first — always, no key needed. Twelve Data
second, only if Yahoo fails *and* a free API key is configured (env var
TWELVEDATA_API_KEY, or {"twelvedata_api_key": "..."} in data/config.json).
No key configured means no fallback attempt — Yahoo's own error is what
gets raised, unchanged.

This is the layer aurum.datafeed.cache calls, so the rest of the codebase
(scanner, decision, fund, the web API) never needs to know how many
providers exist or which one actually answered.
"""

import json
import os
from pathlib import Path
from typing import List, Optional

from . import universe, yahoo
from .yahoo import DataFeedError, HistoryBar, Quote

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "config.json"


def _twelvedata_key() -> Optional[str]:
    key = os.environ.get("TWELVEDATA_API_KEY")
    if key:
        return key
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text()).get("twelvedata_api_key")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def get_quote(name: str) -> Quote:
    yahoo_ticker = universe.resolve(name)
    try:
        return yahoo.get_quote(yahoo_ticker)
    except DataFeedError as primary_error:
        key = _twelvedata_key()
        if not key:
            raise
        from . import twelvedata  # imported lazily: only needed on the fallback path

        try:
            return twelvedata.get_quote(universe.resolve_twelvedata(name), key)
        except DataFeedError:
            raise primary_error  # the original Yahoo error is the more familiar/actionable one


def get_history(name: str, range_: str = "10y", interval: str = "1d") -> List[HistoryBar]:
    yahoo_ticker = universe.resolve(name)
    try:
        return yahoo.get_history(yahoo_ticker, range_=range_, interval=interval)
    except DataFeedError as primary_error:
        key = _twelvedata_key()
        if not key:
            raise
        from . import twelvedata

        try:
            return twelvedata.get_history(universe.resolve_twelvedata(name), key, interval=interval)
        except DataFeedError:
            raise primary_error
