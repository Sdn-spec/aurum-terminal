"""A local CSV cache in front of the Yahoo client.

Fetching 20 years of daily bars for a whole watchlist on every terminal
launch would be slow and needlessly hammers Yahoo's edge. This caches each
(symbol, interval, range) combination to its own CSV under data/cache/ and
only re-fetches once the cached file is older than `max_age_hours`.
"""

import csv
import time
from pathlib import Path
from typing import List

from . import yahoo

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"


def _cache_path(symbol: str, interval: str, range_: str) -> Path:
    safe_symbol = symbol.replace("=", "_").replace("^", "idx_").replace(".", "_")
    return CACHE_DIR / f"{safe_symbol}_{interval}_{range_}.csv"


def _read_cache(path: Path) -> List[yahoo.HistoryBar]:
    bars = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            bars.append(
                yahoo.HistoryBar(
                    timestamp=int(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    return bars


def _write_cache(path: Path, bars: List[yahoo.HistoryBar]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for bar in bars:
            writer.writerow([bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume])


def get_history(symbol: str, range_: str = "10y", interval: str = "1d", max_age_hours: float = 12.0) -> List[yahoo.HistoryBar]:
    path = _cache_path(symbol, interval, range_)
    if path.exists():
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        if age_hours <= max_age_hours:
            return _read_cache(path)

    bars = yahoo.get_history(symbol, range_=range_, interval=interval)
    if bars:
        _write_cache(path, bars)
    elif path.exists():
        return _read_cache(path)  # fetch came back empty; fall back to stale cache rather than nothing
    return bars
