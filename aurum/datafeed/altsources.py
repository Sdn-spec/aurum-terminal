"""Live prices from somewhere other than Yahoo.

Yahoo covers the whole board in one call, which is why it's first — but it
rate-limits per IP, and when it does it refuses *everything* for hours. A
board that only knows one source is a board that shows dashes for an
afternoon. These are the sources that were verified working from this
machine while Yahoo was throttled:

  NSE India   — every Indian index in one keyless request, and it's the
                authoritative source rather than a mirror. Also carries
                30-day and 1-year moves, which are exactly the board's
                1M/1Y columns.
  Finnhub     — US coverage via liquid ETFs (SPY, QQQ, DIA, IWM, GLD...).
                Needs the key that's already configured for news.
  CoinGecko   — crypto, keyless.
  Frankfurter — FX reference rates, keyless.

On honesty: an ETF is not the index it tracks. SPY is roughly SPX/10, so
serving SPY's *level* under "S&P 500 INDEX" would be a lie. Rows filled
this way are marked `is_proxy` with the instrument actually used, and the
UI says so. The percentage move is representative; the level is not.
"""

import http.cookiejar
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional

from . import finnhub
from .yahoo import USER_AGENT, DataFeedError

NSE_HOME = "https://www.nseindia.com"
NSE_INDICES = "https://www.nseindia.com/api/allIndices"
COINGECKO = "https://api.coingecko.com/api/v3/simple/price"
FRANKFURTER = "https://api.frankfurter.app/latest"


# ---- per-source caching ----------------------------------------------------
# The board refreshes every 5s, but these sources have very different budgets:
# NSE is one request, Finnhub is one *per symbol* against a 60/min free tier,
# and FX reference rates only change daily. Refreshing all of them on every
# board tick meant ~200 Finnhub calls a minute and rows flickering to blank as
# it started refusing -- caught live. Each source now refreshes at a rate it
# can actually sustain, and the board reads whatever is currently cached.
_CACHE_TTL = {
    "nse": 10.0,
    "finnhub": 30.0,   # 18 symbols per sweep -> ~36 calls/min, inside the 60 budget
    "crypto": 20.0,
    "fx": 3600.0,      # reference rates, published once a day
}
_cache_lock = threading.Lock()
_cache: Dict[str, dict] = {}


def _cached(key: str, producer):
    """Serve `key` from cache, refreshing only when its own TTL has expired.
    A refresh failure keeps serving the previous value rather than blanking
    the rows that depend on it."""
    ttl = _CACHE_TTL.get(key, 30.0)
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry["at"] < ttl:
            return entry["value"]
    try:
        value = producer()
    except DataFeedError:
        with _cache_lock:
            entry = _cache.get(key)
        if entry:
            return entry["value"]
        raise
    with _cache_lock:
        _cache[key] = {"value": value, "at": time.time()}
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _get_json(url: str, headers: Optional[dict] = None, timeout: float = 12.0):
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise DataFeedError(f"{urllib.parse.urlparse(url).netloc} returned HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise DataFeedError(f"Could not reach {urllib.parse.urlparse(url).netloc}: {getattr(e, 'reason', e)}") from e
    except json.JSONDecodeError as e:
        raise DataFeedError(f"{urllib.parse.urlparse(url).netloc} returned unparseable data") from e


# ---- NSE India -------------------------------------------------------------
# NSE hands out a session cookie on the homepage and rejects API calls without
# one, so the opener is kept alive between refreshes rather than re-handshaking
# every time.
_nse_lock = threading.Lock()
_nse = {"opener": None, "at": 0.0}
_NSE_SESSION_TTL = 900.0


def _nse_opener():
    with _nse_lock:
        if _nse["opener"] is not None and time.time() - _nse["at"] < _NSE_SESSION_TTL:
            return _nse["opener"]
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        # These three headers, exactly. Adding the ones a browser would also
        # send (Connection: keep-alive, Upgrade-Insecure-Requests) gets a 403
        # from the homepage -- found by embellishing a working request and
        # breaking it. Don't "improve" this without re-testing.
        opener.addheaders = [
            ("User-Agent", USER_AGENT),
            ("Accept", "application/json"),
            ("Accept-Language", "en-US,en;q=0.9"),
        ]
        try:
            opener.open(NSE_HOME, timeout=12).read(256)
            # NSE sets its cookies on the homepage and rejects an API call
            # arriving in the same instant; the pause is load-bearing.
            time.sleep(1.0)
        except Exception as e:
            raise DataFeedError(f"Could not open an NSE session: {e}")
        _nse["opener"], _nse["at"] = opener, time.time()
        return opener


def reset_nse_session() -> None:
    with _nse_lock:
        _nse["opener"], _nse["at"] = None, 0.0


def fetch_nse_indices() -> Dict[str, dict]:
    """Every NSE index in one request, keyed by NSE's own index name."""
    return _cached("nse", _fetch_nse_indices_uncached)


def _fetch_nse_indices_uncached() -> Dict[str, dict]:
    opener = _nse_opener()
    try:
        with opener.open(NSE_INDICES, timeout=12) as r:
            payload = json.loads(r.read())
    except Exception as e:
        reset_nse_session()  # a stale cookie looks like a hard failure; retry fresh next time
        raise DataFeedError(f"NSE India request failed: {e}")

    out = {}
    for row in payload.get("data", []):
        name = (row.get("index") or "").strip().upper()
        if not name:
            continue
        out[name] = {
            "price": _num(row.get("last")),
            "previous_close": _num(row.get("previousClose")),
            "change": _num(row.get("variation")),
            "change_pct": _num(row.get("percentChange")),
            "day_high": _num(row.get("high")),
            "day_low": _num(row.get("low")),
            # NSE publishes these directly, so the board's 1M/1Y columns cost
            # nothing extra here (Yahoo needs a year of daily bars per symbol)
            "change_1m_pct": _num(row.get("perChange30d")),
            "change_1y_pct": _num(row.get("perChange365d")),
            "currency": "INR",
        }
    if not out:
        raise DataFeedError("NSE India returned no indices")
    return out


def _num(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


# ---- CoinGecko -------------------------------------------------------------

def fetch_crypto(ids=("bitcoin", "ethereum")) -> Dict[str, dict]:
    return _cached("crypto", lambda: _fetch_crypto_uncached(ids))


def _fetch_crypto_uncached(ids) -> Dict[str, dict]:
    data = _get_json(
        f"{COINGECKO}?ids={','.join(ids)}&vs_currencies=usd&include_24hr_change=true")
    out = {}
    for key, row in (data or {}).items():
        price = _num(row.get("usd"))
        pct = _num(row.get("usd_24h_change"))
        prev = price / (1 + pct / 100) if (price is not None and pct not in (None, -100)) else None
        out[key] = {
            "price": price,
            "change_pct": pct,
            "change": (price - prev) if (price is not None and prev is not None) else None,
            "previous_close": prev,
            "currency": "USD",
        }
    if not out:
        raise DataFeedError("CoinGecko returned no prices")
    return out


# ---- FX --------------------------------------------------------------------

def fetch_fx(symbols=("INR", "EUR", "JPY", "GBP")) -> Dict[str, float]:
    return _cached("fx", lambda: _fetch_fx_uncached(symbols))


def _fetch_fx_uncached(symbols) -> Dict[str, float]:
    data = _get_json(f"{FRANKFURTER}?from=USD&to={','.join(symbols)}")
    rates = {k: _num(v) for k, v in (data.get("rates") or {}).items()}
    if not rates:
        raise DataFeedError("Frankfurter returned no rates")
    return rates


# ---- Finnhub quotes --------------------------------------------------------

def fetch_finnhub_quotes(symbols) -> Dict[str, dict]:
    """Finnhub's free tier covers US equities and ETFs but not index levels,
    so the board uses liquid ETFs as stand-ins (see module docstring)."""
    key = tuple(symbols)
    return _cached("finnhub", lambda: _fetch_finnhub_uncached(key))


def _fetch_finnhub_uncached(symbols) -> Dict[str, dict]:
    api_key = finnhub.resolve_api_key()
    if not api_key:
        raise DataFeedError("No Finnhub key configured")
    out = {}
    for symbol in symbols:
        try:
            d = _get_json(
                f"https://finnhub.io/api/v1/quote?symbol={urllib.parse.quote(symbol)}"
                f"&token={api_key}")
        except DataFeedError:
            continue
        price = _num(d.get("c"))
        if not price:  # Finnhub answers 0 for anything the plan doesn't cover
            continue
        out[symbol] = {
            "price": price,
            "previous_close": _num(d.get("pc")),
            "change": _num(d.get("d")),
            "change_pct": _num(d.get("dp")),
            "day_high": _num(d.get("h")),
            "day_low": _num(d.get("l")),
            "currency": "USD",
        }
    if not out:
        raise DataFeedError("Finnhub returned no usable quotes")
    return out
