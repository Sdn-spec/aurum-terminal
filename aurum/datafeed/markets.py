"""The global-index board behind the Markets dashboard.

Two things make this module different from the rest of the datafeed layer,
and both come from the same constraint: this board shows ~25 instruments and
wants to refresh every few seconds, where the watchlist shows 7 and refreshes
every 15.

  1. It fetches in ONE batched request, not one per symbol. Yahoo's
     v7/finance/quote endpoint takes a comma-separated symbol list and needs
     a cookie+crumb session (an unauthenticated call gets HTTP 429 straight
     away -- verified live). One request per cycle for the whole board is the
     only thing that makes a 5-10s refresh sustainable; 25 separate chart
     calls at that cadence gets the IP rate-limited within a minute, which is
     exactly what happened while this was being built.

  2. The cache lives here rather than in each browser. Every open tab reads
     the same server-side snapshot, so ten tabs still cost one upstream call
     per refresh window instead of ten. Callers get served the last good
     snapshot (with its real age attached) whenever a refresh fails, so a
     transient rate-limit shows a slightly stale board instead of an error.

Prices from Yahoo are delayed ~15 minutes for most indices; "live" here
means continuously refreshed, not real-time market data.
"""

import http.cookiejar
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import yahoo
from .yahoo import USER_AGENT, DataFeedError, RateLimitedError

QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
COOKIE_SEED_URLS = ("https://fc.yahoo.com", "https://finance.yahoo.com")


@dataclass(frozen=True)
class Index:
    code: str  # short board code, e.g. "SPX"
    label: str  # human name, e.g. "S&P 500 INDEX"
    ticker: str  # Yahoo ticker, e.g. "^GSPC"
    region: str  # "Americas" | "EMEA" | "APAC" | "Commodities & FX"


# Ordered per region; this order is what the board renders in.
INDICES: List[Index] = [
    # ---- Americas ----
    Index("INDU", "DOW JONES INDUS. AVG", "^DJI", "Americas"),
    Index("SPX", "S&P 500 INDEX", "^GSPC", "Americas"),
    Index("CCMP", "NASDAQ COMPOSITE", "^IXIC", "Americas"),
    Index("NDX", "NASDAQ 100", "^NDX", "Americas"),
    Index("RTY", "RUSSELL 2000", "^RUT", "Americas"),
    Index("SPTSX", "S&P/TSX COMPOSITE INDEX", "^GSPTSE", "Americas"),
    Index("IBOV", "IBOVESPA", "^BVSP", "Americas"),
    Index("MEXBOL", "S&P/BMV IPC", "^MXX", "Americas"),
    # ---- EMEA ----
    Index("SX5E", "EURO STOXX 50", "^STOXX50E", "EMEA"),
    Index("UKX", "FTSE 100 INDEX", "^FTSE", "EMEA"),
    Index("DAX", "DAX INDEX", "^GDAXI", "EMEA"),
    Index("CAC", "CAC 40 INDEX", "^FCHI", "EMEA"),
    Index("IBEX", "IBEX 35 INDEX", "^IBEX", "EMEA"),
    Index("AEX", "AEX INDEX", "^AEX", "EMEA"),
    Index("SMI", "SWISS MARKET INDEX", "^SSMI", "EMEA"),
    # ---- APAC ----
    Index("NKY", "NIKKEI 225", "^N225", "APAC"),
    Index("HSI", "HANG SENG INDEX", "^HSI", "APAC"),
    Index("SHSZ300", "CSI 300 INDEX", "000300.SS", "APAC"),
    Index("AS51", "S&P/ASX 200", "^AXJO", "APAC"),
    Index("KOSPI", "KOSPI INDEX", "^KS11", "APAC"),
    Index("SENSEX", "S&P BSE SENSEX", "^BSESN", "APAC"),
    Index("NIFTY", "NIFTY 50", "^NSEI", "APAC"),
    Index("TWSE", "TAIWAN TAIEX", "^TWII", "APAC"),
    # ---- Commodities & FX (this desk trades gold, so they belong on the board) ----
    Index("GOLD", "GOLD FUTURES", "GC=F", "Commodities & FX"),
    Index("SILVER", "SILVER FUTURES", "SI=F", "Commodities & FX"),
    Index("WTI", "CRUDE OIL WTI", "CL=F", "Commodities & FX"),
    Index("BTC", "BITCOIN / USD", "BTC-USD", "Commodities & FX"),
    Index("DXY", "US DOLLAR INDEX", "DX-Y.NYB", "Commodities & FX"),
    Index("US10Y", "US 10-YEAR YIELD", "^TNX", "Commodities & FX"),
]

REGIONS = ["Americas", "EMEA", "APAC", "Commodities & FX"]
BY_TICKER: Dict[str, Index] = {i.ticker: i for i in INDICES}


@dataclass
class MarketRow:
    code: str
    label: str
    ticker: str
    region: str
    price: Optional[float] = None
    previous_close: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    currency: str = ""
    market_time: int = 0
    market_state: str = ""
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    # filled in from the (separately cached, much slower-moving) history board
    change_1m_pct: Optional[float] = None
    change_1y_pct: Optional[float] = None
    spark: List[float] = field(default_factory=list)


class _YahooSession:
    """Holds the cookie+crumb pair the batch quote endpoint requires.

    The crumb is tied to the cookie jar and both go stale, so this refreshes
    the pair on demand and again whenever a request comes back 401/403/429 --
    an expired crumb is indistinguishable from being rate-limited from the
    status code alone, so the cheap fix (re-handshake once) is tried before
    giving up on the cycle.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._opener = None
        self._crumb: Optional[str] = None
        self._established_at = 0.0

    def _handshake(self) -> None:
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        opener.addheaders = [("User-Agent", USER_AGENT)]
        for url in COOKIE_SEED_URLS:
            try:
                with opener.open(url, timeout=10) as r:
                    r.read(1024)
            except Exception:
                # fc.yahoo.com answers 404 but still sets the A3 cookie, which
                # is all that matters here; finance.yahoo.com is the fallback.
                pass
            if any(c.name == "A3" for c in jar):
                break
        crumb = None
        try:
            with opener.open(CRUMB_URL, timeout=10) as r:
                candidate = r.read().decode(errors="replace").strip()
            # A real crumb is a short opaque token; an error page is not.
            if candidate and len(candidate) < 32 and "<" not in candidate:
                crumb = candidate
        except Exception:
            crumb = None
        self._opener, self._crumb, self._established_at = opener, crumb, time.time()

    def fetch_quotes(self, tickers: List[str]) -> List[dict]:
        # Shares the global Yahoo breaker with the chart client: the throttle
        # is per-IP across every Yahoo endpoint, so the batch path must respect
        # the same cooldown rather than knocking on its own.
        yahoo._check_breaker()
        with self._lock:
            if self._opener is None or self._crumb is None or time.time() - self._established_at > 1800:
                self._handshake()
            opener, crumb = self._opener, self._crumb

        if not crumb:
            raise DataFeedError("Could not establish a Yahoo session (no crumb)")

        params = {"symbols": ",".join(tickers), "crumb": crumb}
        url = f"{QUOTE_URL}?{urllib.parse.urlencode(params)}"
        try:
            with opener.open(url, timeout=15) as r:
                payload = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                yahoo._note_rate_limited()
                if yahoo.rate_limit_status()["open"]:
                    raise RateLimitedError(
                        "Yahoo rate-limited the batch quote; pausing Yahoo requests so the limit can clear"
                    ) from e
            if e.code in (401, 403, 429):
                with self._lock:
                    self._handshake()
                    opener, crumb = self._opener, self._crumb
                if not crumb:
                    raise DataFeedError(f"Yahoo returned HTTP {e.code} and the session could not be renewed") from e
                params["crumb"] = crumb
                url = f"{QUOTE_URL}?{urllib.parse.urlencode(params)}"
                try:
                    with opener.open(url, timeout=15) as r:
                        payload = json.loads(r.read())
                except Exception as e2:
                    raise DataFeedError(f"Yahoo batch quote failed after session renewal: {e2}") from e2
            else:
                raise DataFeedError(f"Yahoo returned HTTP {e.code} for the batch quote") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise DataFeedError(f"Could not reach Yahoo for the batch quote: {getattr(e, 'reason', e)}") from e
        except json.JSONDecodeError as e:
            raise DataFeedError("Yahoo returned unparseable data for the batch quote") from e

        yahoo._note_request_succeeded()  # a good response clears the breaker's 429 streak
        return payload.get("quoteResponse", {}).get("result") or []


_session = _YahooSession()


def compute_history_stats(bars) -> dict:
    """1-month / 1-year percentage moves and a 30-point sparkline, derived
    from daily bars. Daily closes only change once a day, so the caller
    caches these for hours -- they are not part of the fast refresh path."""
    closes = [b.close for b in bars if b.close]
    if not closes:
        return {"change_1m_pct": None, "change_1y_pct": None, "spark": []}
    last = closes[-1]

    def pct_from(n_bars_back: int) -> Optional[float]:
        if len(closes) <= n_bars_back:
            return None
        base = closes[-1 - n_bars_back]
        return (last - base) / base * 100 if base else None

    return {
        "change_1m_pct": pct_from(21),  # ~21 trading days in a month
        "change_1y_pct": pct_from(251),  # ~251 trading days in a year
        "spark": closes[-30:],
    }


def _row_from_quote(idx: Index, q: dict) -> MarketRow:
    price = q.get("regularMarketPrice")
    prev = q.get("regularMarketPreviousClose")
    change = q.get("regularMarketChange")
    change_pct = q.get("regularMarketChangePercent")
    if change is None and price is not None and prev:
        change = price - prev
    if change_pct is None and price is not None and prev:
        change_pct = (price - prev) / prev * 100
    return MarketRow(
        code=idx.code,
        label=idx.label,
        ticker=idx.ticker,
        region=idx.region,
        price=price,
        previous_close=prev,
        change=change,
        change_pct=change_pct,
        currency=q.get("currency") or "",
        market_time=q.get("regularMarketTime") or 0,
        market_state=q.get("marketState") or "",
        day_high=q.get("regularMarketDayHigh"),
        day_low=q.get("regularMarketDayLow"),
    )


def fetch_board_via_quote_cache(get_quote, deadline_seconds: Optional[float] = None) -> List[MarketRow]:
    """Fallback path for when the batched endpoint is unavailable (no crumb,
    or Yahoo is rate-limiting the v7 API specifically).

    `get_quote` is aurum.datafeed.cache.get_quote, which serves from a
    short-TTL disk cache -- so this walks the board without generating a
    burst of fresh upstream requests on every refresh, and any instrument
    that can't be fetched just comes back priceless instead of failing the
    whole board.

    `deadline_seconds` bounds the whole walk. It matters more than it looks:
    when Yahoo is rate-limiting, every symbol costs several seconds of
    retry-with-backoff inside the client before it gives up, so an unbounded
    walk over the board takes minutes. Past the deadline the remaining
    instruments are returned priceless rather than waited on.
    """
    started = time.time()
    rows = []
    got_any = False
    for idx in INDICES:
        if deadline_seconds is not None and time.time() - started > deadline_seconds:
            rows.append(MarketRow(code=idx.code, label=idx.label, ticker=idx.ticker, region=idx.region))
            continue
        try:
            q = get_quote(idx.ticker)
            got_any = True
            rows.append(
                MarketRow(
                    code=idx.code, label=idx.label, ticker=idx.ticker, region=idx.region,
                    price=q.price or None,
                    previous_close=q.previous_close or None,
                    change=(q.price - q.previous_close) if (q.price and q.previous_close) else None,
                    change_pct=((q.price - q.previous_close) / q.previous_close * 100) if (q.price and q.previous_close) else None,
                    currency=q.currency, market_time=q.market_time,
                    day_high=q.day_high or None, day_low=q.day_low or None,
                )
            )
        except DataFeedError:
            rows.append(MarketRow(code=idx.code, label=idx.label, ticker=idx.ticker, region=idx.region))
    if not got_any:
        raise DataFeedError("Could not fetch any instrument on the board")
    return rows


def fetch_board() -> List[MarketRow]:
    """One batched upstream call for the whole board. Raises DataFeedError
    if the call fails; callers decide whether to serve a stale snapshot."""
    raw = _session.fetch_quotes([i.ticker for i in INDICES])
    by_symbol = {q.get("symbol"): q for q in raw}
    rows = []
    for idx in INDICES:
        q = by_symbol.get(idx.ticker) or {}
        price = q.get("regularMarketPrice")
        prev = q.get("regularMarketPreviousClose")
        change = q.get("regularMarketChange")
        change_pct = q.get("regularMarketChangePercent")
        if change is None and price is not None and prev:
            change = price - prev
        if change_pct is None and price is not None and prev:
            change_pct = (price - prev) / prev * 100
        rows.append(
            MarketRow(
                code=idx.code,
                label=idx.label,
                ticker=idx.ticker,
                region=idx.region,
                price=price,
                previous_close=prev,
                change=change,
                change_pct=change_pct,
                currency=q.get("currency") or "",
                market_time=q.get("regularMarketTime") or 0,
                market_state=q.get("marketState") or "",
                day_high=q.get("regularMarketDayHigh"),
                day_low=q.get("regularMarketDayLow"),
            )
        )
    return rows
