"""A local cache in front of aurum.datafeed.provider — the thing that
actually keeps this usable.

Three problems showed up running the website live: firing the whole
watchlist's quotes back-to-back tripped Yahoo's burst limit even with
per-call retry; opening a second browser tab (or reloading mid-load)
doubled every request because nothing coordinated concurrent callers; and
Yahoo's rate limit sometimes just doesn't clear for a long stretch, with
no single-provider fix for that. All three are addressed here: cache each
(name, interval, range) to disk, hold a per-key lock so concurrent callers
for the *same* key wait for one fetch instead of each firing their own, and
fall back to whatever's on disk (however stale) rather than surfacing a 502
when perfectly usable data is sitting right there. The `name` this module's
functions take is a friendly name (e.g. "GOLD"), not a ticker — provider.py
does the per-backend symbol resolution.
"""

import csv
import json
import threading
import time
from pathlib import Path
from typing import List

from . import provider, yahoo

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"

_locks_guard = threading.Lock()
_locks: dict = {}


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


# ---- historical bars --------------------------------------------------------


def _history_path(symbol: str, interval: str, range_: str) -> Path:
    safe_symbol = symbol.replace("=", "_").replace("^", "idx_").replace(".", "_")
    return CACHE_DIR / f"{safe_symbol}_{interval}_{range_}.csv"


# kept for backwards compatibility with existing tests/callers
_cache_path = _history_path


def _read_history_cache(path: Path) -> List[yahoo.HistoryBar]:
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


_read_cache = _read_history_cache  # backwards compatibility


def _write_history_cache(path: Path, bars: List[yahoo.HistoryBar]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for bar in bars:
            writer.writerow([bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume])


_write_cache = _write_history_cache  # backwards compatibility


def get_history(symbol: str, range_: str = "10y", interval: str = "1d", max_age_hours: float = 12.0) -> List[yahoo.HistoryBar]:
    path = _history_path(symbol, interval, range_)
    with _lock_for(str(path)):
        if path.exists():
            age_hours = (time.time() - path.stat().st_mtime) / 3600
            if age_hours <= max_age_hours:
                return _read_history_cache(path)

        try:
            bars = provider.get_history(symbol, range_=range_, interval=interval)
        except yahoo.DataFeedError:
            if path.exists():
                return _read_history_cache(path)  # stale data beats a 502
            raise

        if bars:
            _write_history_cache(path, bars)
        elif path.exists():
            return _read_history_cache(path)  # fetch came back empty; fall back to stale cache
        return bars


# ---- quotes -----------------------------------------------------------------


def _quote_path(symbol: str) -> Path:
    safe_symbol = symbol.replace("=", "_").replace("^", "idx_").replace(".", "_")
    return CACHE_DIR / f"quote_{safe_symbol}.json"


def _read_quote_cache(path: Path) -> yahoo.Quote:
    data = json.loads(path.read_text())
    return yahoo.Quote(**data)


def _write_quote_cache(path: Path, quote: yahoo.Quote) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(quote.__dict__))


def get_quote(symbol: str, max_age_seconds: float = 45.0) -> yahoo.Quote:
    """Short-TTL quote cache. Quotes from this endpoint are already
    delayed ~15-20 minutes, so serving a 45-second-old copy costs nothing
    real and turns "7 symbols x every click" into "7 symbols x every 45s.\""""
    path = _quote_path(symbol)
    with _lock_for(str(path)):
        if path.exists():
            age = time.time() - path.stat().st_mtime
            if age <= max_age_seconds:
                return _read_quote_cache(path)

        try:
            quote = provider.get_quote(symbol)
        except yahoo.DataFeedError:
            if path.exists():
                return _read_quote_cache(path)
            raise

        _write_quote_cache(path, quote)
        return quote
