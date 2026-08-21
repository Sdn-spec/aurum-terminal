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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import List

from .yahoo import DataFeedError, HistoryBar, Quote

BASE_URL = "https://api.twelvedata.com"

_INTERVAL_MAP = {"1d": "1day", "1wk": "1week", "1mo": "1month", "1h": "1h", "15m": "15min", "5m": "5min", "1m": "1min"}

# ---- daily-quota guard ------------------------------------------------------
# Twelve Data's free tier is 800 credits a day, and this client is reached as
# a *fallback* -- every Yahoo failure sends a call here. So a Yahoo outage
# doesn't degrade to Twelve Data, it stampedes it: found this key at 11,001
# credits used against a limit of 800, i.e. ~13x the daily allowance burned
# entirely on calls that could only ever return "out of credits".
#
# Once the API says the day's quota is gone, believe it and stop calling until
# the quota actually resets (UTC midnight, per Twelve Data's docs).
_quota_lock = threading.Lock()
_quota = {"exhausted_until": 0.0}

# Symbols this plan is not entitled to (SPX, NASDAQ, SILVER, OIL on the free
# tier -- see the module docstring). A refusal still costs a credit, so asking
# again every poll spends the day's allowance on answers that cannot change
# until the plan does. Remember them and stop asking.
_unsupported: set = set()


class QuotaExhaustedError(DataFeedError):
    """Raised while Twelve Data's daily credit allowance is known to be spent."""


class SymbolNotOnPlanError(DataFeedError):
    """Raised for a symbol this API plan does not include. Distinct from a
    transient failure: retrying cannot help until the plan is upgraded."""


def _looks_like_plan_restriction(message: str) -> bool:
    lowered = message.lower()
    return "plan" in lowered and ("available" in lowered or "upgrade" in lowered)


def _seconds_until_utc_midnight() -> float:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (tomorrow - now).total_seconds()


def quota_status() -> dict:
    with _quota_lock:
        remaining = max(0.0, _quota["exhausted_until"] - time.time())
    return {"exhausted": remaining > 0, "seconds_until_reset": remaining}


def reset_quota_guard() -> None:
    with _quota_lock:
        _quota["exhausted_until"] = 0.0


def _note_quota_exhausted() -> None:
    with _quota_lock:
        _quota["exhausted_until"] = time.time() + _seconds_until_utc_midnight()


def _check_quota() -> None:
    with _quota_lock:
        remaining = _quota["exhausted_until"] - time.time()
    if remaining > 0:
        raise QuotaExhaustedError(
            f"Twelve Data's daily credit limit is spent; not calling again for "
            f"{remaining / 3600:.1f}h (resets at UTC midnight)"
        )


def reset_plan_memo() -> None:
    _unsupported.clear()


def _get(path: str, params: dict) -> dict:
    _check_quota()
    symbol = params.get("symbol")
    if symbol in _unsupported:
        raise SymbolNotOnPlanError(
            f"Twelve Data's current plan does not include {symbol}; not spending a credit to be told again"
        )
    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}/{path}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "aurum-terminal/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            # 429 here is the daily credit allowance, not a per-second burst.
            _note_quota_exhausted()
            raise QuotaExhaustedError(
                f"Twelve Data daily credit limit reached; pausing calls until the quota resets"
            ) from e
        raise DataFeedError(f"Twelve Data returned HTTP {e.code} for {params.get('symbol')}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        # a mid-read timeout comes back as a bare TimeoutError, not wrapped in
        # URLError -- see aurum.datafeed.yahoo for where this was actually caught live
        reason = getattr(e, "reason", e)
        raise DataFeedError(f"Could not reach Twelve Data for {params.get('symbol')}: {reason}") from e
    except json.JSONDecodeError as e:
        raise DataFeedError(f"Twelve Data returned unparseable data for {params.get('symbol')}") from e

    if isinstance(payload, dict) and payload.get("status") == "error":
        message = str(payload.get("message") or "")
        # The same exhaustion can arrive as a 200 with an error body rather
        # than an HTTP 429, so catch it here too.
        if payload.get("code") == 429 or "run out of API credits" in message:
            _note_quota_exhausted()
            raise QuotaExhaustedError(f"Twelve Data daily credit limit reached: {message}")
        if _looks_like_plan_restriction(message):
            if symbol:
                _unsupported.add(symbol)
            raise SymbolNotOnPlanError(f"Twelve Data plan does not include {symbol}: {message}")
        raise DataFeedError(f"Twelve Data error for {params.get('symbol')}: {message}")
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
        previous_close=float(data.get("previous_close", 0) or 0),
        open=float(data.get("open", 0) or 0),
        volume=float(data.get("volume", 0) or 0),
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
