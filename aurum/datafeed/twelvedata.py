"""A second data provider, for when Yahoo's rate limit won't budge.

Twelve Data (twelvedata.com) has a real free tier behind a free API key —
no credit card, sign up at https://twelvedata.com/pricing. It's picked over
other free options for two reasons: it's an official, documented API (not
scraping — Stooq's endpoint is protected by an actual JavaScript
proof-of-work challenge that no plain HTTP client can pass, key or not),
and its free tier's request volume is meaningfully more usable for an
interactive dashboard than Alpha Vantage's (25 requests/day at the time
this was written, which a 7-symbol watchlist burns through almost
immediately).

This module is never called unless a key is configured — see
`aurum.datafeed.provider` for how it's wired in as a fallback, not a
replacement.

Verified live against a real free-tier key (2026-08-20): GOLD (XAU/USD) and
BTC (BTC/USD) work directly, including 5000 real daily Gold bars back to
2008. SILVER, OIL, SPX, and NASDAQ are free-tier-restricted on Twelve Data
("available starting with the Grow or Venture plan") — see
`aurum.datafeed.universe.TWELVEDATA_ALIASES` for the full breakdown and the
DXY-to-UUP proxy substitution.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import List

from .yahoo import DataFeedError, HistoryBar, Quote

BASE_URL = "https://api.twelvedata.com"

_INTERVAL_MAP = {"1d": "1day", "1wk": "1week", "1mo": "1month", "1h": "1h", "15m": "15min", "5m": "5min", "1m": "1min"}


def _get(path: str, params: dict) -> dict:
    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}/{path}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "aurum-terminal/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as e:
        raise DataFeedError(f"Twelve Data returned HTTP {e.code} for {params.get('symbol')}") from e
    except urllib.error.URLError as e:
        raise DataFeedError(f"Could not reach Twelve Data for {params.get('symbol')}: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise DataFeedError(f"Twelve Data returned unparseable data for {params.get('symbol')}") from e

    if isinstance(payload, dict) and payload.get("status") == "error":
        raise DataFeedError(f"Twelve Data error for {params.get('symbol')}: {payload.get('message')}")
    return payload


def _parse_datetime(value: str) -> int:
    # daily bars come back as "2024-01-01"; intraday as "2024-01-01 14:30:00" —
    # treated as UTC for consistency with the rest of the system, which is an
    # approximation (Twelve Data's actual timestamps are exchange-local).
    fmt = "%Y-%m-%d %H:%M:%S" if " " in value else "%Y-%m-%d"
    dt = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def get_quote(symbol: str, api_key: str) -> Quote:
    data = _get("quote", {"symbol": symbol, "apikey": api_key})
    fifty_two_week = data.get("fifty_two_week") or {}
    return Quote(
        symbol=symbol,
        price=float(data.get("close", 0) or 0),
        day_high=float(data.get("high", 0) or 0),
        day_low=float(data.get("low", 0) or 0),
        fifty_two_week_high=float(fifty_two_week.get("high", 0) or 0),
        fifty_two_week_low=float(fifty_two_week.get("low", 0) or 0),
        currency=data.get("currency", ""),
        exchange=data.get("exchange", ""),
        market_time=int(data.get("timestamp", 0) or 0),
    )


def get_history(symbol: str, api_key: str, interval: str = "1d", outputsize: int = 5000) -> List[HistoryBar]:
    td_interval = _INTERVAL_MAP.get(interval, interval)
    data = _get(
        "time_series",
        {"symbol": symbol, "interval": td_interval, "outputsize": outputsize, "apikey": api_key},
    )
    values = data.get("values") or []
    bars = [
        HistoryBar(
            timestamp=_parse_datetime(row["datetime"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume", 0) or 0),
        )
        for row in values
    ]
    bars.sort(key=lambda b: b.timestamp)  # Twelve Data returns newest-first; the rest of aurum expects ascending
    return bars
