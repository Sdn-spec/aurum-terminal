"""News and an earnings calendar via Finnhub — free tier, 60 calls/minute,
no application review. Only meaningful for real stock tickers (AAPL,
MSFT, ...); the commodity/forex/index names in the default watchlist
(GOLD, SILVER, BTC, SPX, ...) aren't Finnhub-recognized symbols, so these
calls just come back empty for them — not an error, nothing to catch
specially at the call site.

Needs a free key from https://finnhub.io/register, set as
FINNHUB_API_KEY or {"finnhub_api_key": "..."} in data/config.json — same
file and resolution order as the Twelve Data and FRED keys.

Verified live against a real free-tier key (2026-08-20): calendar/earnings,
the general news endpoint, and company-news (get_company_news()) all return
real data for AAPL. An earlier check the same day saw company-news come back
empty for the same symbol and key — re-verified later and it was real data,
so that was a transient gap on Finnhub's side (a delay indexing a fresh key,
maybe), not an actual free-tier restriction. Worth knowing if it's ever seen
empty again: it may just need a retry rather than being a hard limitation.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional

from . import provider
from .yahoo import DataFeedError

BASE_URL = "https://finnhub.io/api/v1"


@dataclass
class NewsItem:
    headline: str
    source: str
    url: str
    published: int  # unix seconds


@dataclass
class EarningsEvent:
    date: str
    eps_estimate: Optional[float]
    eps_actual: Optional[float]


@dataclass
class Fundamentals:
    pe_ttm: Optional[float]
    market_cap_millions: Optional[float]
    eps_ttm: Optional[float]
    dividend_yield_pct: Optional[float]
    net_profit_margin_pct: Optional[float]
    return_on_equity_pct: Optional[float]
    beta: Optional[float]


def resolve_api_key() -> Optional[str]:
    key = os.environ.get("FINNHUB_API_KEY")
    if key:
        return key
    if provider.CONFIG_PATH.exists():
        try:
            return json.loads(provider.CONFIG_PATH.read_text()).get("finnhub_api_key")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _get(path: str, params: dict, api_key: str):
    query = urllib.parse.urlencode({**params, "token": api_key})
    url = f"{BASE_URL}/{path}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "aurum-terminal/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        raise DataFeedError(f"Finnhub returned HTTP {e.code} for {path}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        # a mid-read timeout comes back as a bare TimeoutError, not wrapped in
        # URLError -- see aurum.datafeed.yahoo for where this was actually caught live
        reason = getattr(e, "reason", e)
        raise DataFeedError(f"Could not reach Finnhub for {path}: {reason}") from e
    except json.JSONDecodeError as e:
        raise DataFeedError(f"Finnhub returned unparseable data for {path}") from e


def get_company_news(symbol: str, api_key: str, days: int = 7, limit: int = 8) -> List[NewsItem]:
    today = date.today()
    payload = _get(
        "company-news",
        {"symbol": symbol, "from": str(today - timedelta(days=days)), "to": str(today)},
        api_key,
    )
    if not isinstance(payload, list):
        return []
    items = [
        NewsItem(headline=row["headline"], source=row.get("source", ""), url=row.get("url", ""), published=row.get("datetime", 0))
        for row in payload
        if row.get("headline")
    ]
    items.sort(key=lambda n: n.published, reverse=True)
    return items[:limit]


def get_next_earnings(symbol: str, api_key: str, days_ahead: int = 120) -> Optional[EarningsEvent]:
    today = date.today()
    payload = _get(
        "calendar/earnings",
        {"symbol": symbol, "from": str(today), "to": str(today + timedelta(days=days_ahead))},
        api_key,
    )
    rows = payload.get("earningsCalendar") if isinstance(payload, dict) else None
    if not rows:
        return None
    row = sorted(rows, key=lambda r: r.get("date", ""))[0]
    return EarningsEvent(date=row.get("date", ""), eps_estimate=row.get("epsEstimate"), eps_actual=row.get("epsActual"))


def get_fundamentals(symbol: str, api_key: str) -> Optional[Fundamentals]:
    """Field names verified against a real response for AAPL (2026-08-20):
    stock/metric?symbol=AAPL&metric=all. marketCapitalization comes back in
    millions of USD, left as-is here — the caller formats it for display."""
    payload = _get("stock/metric", {"symbol": symbol, "metric": "all"}, api_key)
    metric = payload.get("metric") if isinstance(payload, dict) else None
    if not metric or not any(metric.get(k) is not None for k in ("peTTM", "marketCapitalization", "epsTTM")):
        return None
    return Fundamentals(
        pe_ttm=metric.get("peTTM"),
        market_cap_millions=metric.get("marketCapitalization"),
        eps_ttm=metric.get("epsTTM"),
        dividend_yield_pct=metric.get("dividendYieldIndicatedAnnual"),
        net_profit_margin_pct=metric.get("netProfitMarginTTM"),
        return_on_equity_pct=metric.get("roeTTM"),
        beta=metric.get("beta"),
    )
