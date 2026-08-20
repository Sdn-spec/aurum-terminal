"""A minimal client for Yahoo Finance's public chart API.

This is the same data TradingView-style charts and yfinance ultimately pull
from, hit directly over HTTP with the stdlib — no API key, no signup, no
extra dependency. It needs a browser User-Agent header or Yahoo's edge
rejects the request; that's the one non-obvious requirement here.

Two things to know about what you get back:
  - Quotes are typically delayed ~15-20 minutes for most symbols (real-time
    is a paid tier on every provider, including Yahoo) — fine for a personal
    terminal, not fine for latency-sensitive execution.
  - History depth depends on the interval: daily/weekly/monthly bars go back
    decades ("range=max"); intraday bars are capped much shorter by Yahoo
    itself (roughly: 1m -> 7d, 5m/15m -> 60d, 1h -> 2y), no matter what range
    you ask for.
"""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class DataFeedError(RuntimeError):
    """Raised when Yahoo can't be reached or returns something unusable."""


@dataclass(frozen=True)
class HistoryBar:
    timestamp: int  # unix seconds (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    day_high: float
    day_low: float
    fifty_two_week_high: float
    fifty_two_week_low: float
    currency: str
    exchange: str
    market_time: int  # unix seconds of the last update
    previous_close: float = 0.0  # prior session's close -- day change is computed from this
    open: float = 0.0
    volume: float = 0.0


def _fetch(symbol: str, params: dict, retries: int = 4) -> dict:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE_URL.format(symbol=symbol)}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    last_error: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read())
            break
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code == 429 and attempt < retries - 1:
                # Yahoo's edge rate-limits per IP; back off and try again rather
                # than failing outright — quote data is delayed anyway, so
                # waiting a couple seconds costs nothing real.
                retry_after = e.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else (2 ** attempt)
                time.sleep(min(delay, 20))
                continue
            raise DataFeedError(f"Yahoo returned HTTP {e.code} for {symbol}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            # A timeout that happens mid-response-read (not at connect time)
            # comes back as a bare TimeoutError, not wrapped in URLError — a
            # real gap caught live: FRED hit exactly this and, since it wasn't
            # caught, crashed the whole Analyze report instead of just skipping
            # macro data. Retried the same as a 429, since it's just as
            # transient; only raises once retries are exhausted.
            last_error = e
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 20))
                continue
            reason = getattr(e, "reason", e)
            raise DataFeedError(f"Could not reach Yahoo Finance for {symbol}: {reason}") from e
        except json.JSONDecodeError as e:
            raise DataFeedError(f"Yahoo returned unparseable data for {symbol}") from e
    else:
        raise DataFeedError(f"Yahoo kept rate-limiting requests for {symbol} after {retries} attempts") from last_error

    chart = payload.get("chart", {})
    if chart.get("error"):
        raise DataFeedError(f"Yahoo error for {symbol}: {chart['error']}")
    results = chart.get("result")
    if not results:
        raise DataFeedError(f"No data returned for {symbol} — check the ticker is valid")
    return results[0]


def get_history(symbol: str, range_: str = "1y", interval: str = "1d") -> List[HistoryBar]:
    """Fetch OHLCV bars. `range_` e.g. "1mo","1y","5y","10y","max".
    `interval` e.g. "1m","15m","1h","1d","1wk","1mo"."""
    result = _fetch(symbol, {"range": range_, "interval": interval})
    timestamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]

    bars = []
    for i, ts in enumerate(timestamps):
        o, h, l, c, v = (quote[field][i] for field in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c):
            continue  # Yahoo pads illiquid/holiday bars with nulls; skip them
        bars.append(HistoryBar(ts, o, h, l, c, v or 0.0))
    return bars


def get_quote(symbol: str) -> Quote:
    """A current snapshot, derived from the same chart endpoint's metadata
    (no separate quote endpoint needed, and one less thing Yahoo can block)."""
    result = _fetch(symbol, {"range": "1d", "interval": "1d"})
    meta = result["meta"]
    return Quote(
        symbol=symbol,
        price=meta.get("regularMarketPrice", 0.0),
        day_high=meta.get("regularMarketDayHigh", 0.0),
        day_low=meta.get("regularMarketDayLow", 0.0),
        fifty_two_week_high=meta.get("fiftyTwoWeekHigh", 0.0),
        fifty_two_week_low=meta.get("fiftyTwoWeekLow", 0.0),
        currency=meta.get("currency", ""),
        exchange=meta.get("fullExchangeName", meta.get("exchangeName", "")),
        market_time=meta.get("regularMarketTime", 0),
        previous_close=meta.get("chartPreviousClose") or meta.get("previousClose") or 0.0,
        open=meta.get("regularMarketOpen", 0.0) or 0.0,
        volume=meta.get("regularMarketVolume", 0.0) or 0.0,
    )
