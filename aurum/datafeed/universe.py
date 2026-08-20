"""Friendly names for the instruments this terminal actually cares about,
mapped to their real Yahoo Finance ticker. Anything not in this map is
passed straight through as a raw ticker, so you can always type a symbol
Yahoo recognizes directly (e.g. "AAPL", "TSLA") even if it's not listed here.
"""

ALIASES = {
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "OIL": "CL=F",
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SPX": "^GSPC",
    "NASDAQ": "^NDX",
    "DXY": "DX-Y.NYB",
    "EURUSD": "EURUSD=X",
    "US10Y": "^TNX",
}

# The same instruments, in Twelve Data's own symbol format — used only as a
# fallback when Yahoo fails and a Twelve Data key is configured (see
# aurum.datafeed.provider). Verified live against a free-tier key on
# 2026-08-20 — free-tier coverage is genuinely partial, not a guess:
#   GOLD, BTC          -> work directly, real data confirmed live
#   SILVER, OIL,
#   SPX, NASDAQ         -> free tier returns "available starting with the
#                          Grow or Venture plan" — these fall through to
#                          Yahoo's own error when both providers fail
#   DXY                 -> no free-tier index symbol exists at all; mapped
#                          to UUP (Invesco's USD-bullish ETF) as a directional
#                          proxy — it tracks dollar strength, not the DXY
#                          index itself, so treat it as approximate
TWELVEDATA_ALIASES = {
    "GOLD": "XAU/USD",
    "SILVER": "XAG/USD",  # needs a paid plan on this key; kept mapped for when it's upgraded
    "OIL": "WTI/USD",  # needs a paid plan on this key; kept mapped for when it's upgraded
    "BTC": "BTC/USD",
    "ETH": "ETH/USD",
    "SPX": "SPX",  # needs a paid plan on this key; kept mapped for when it's upgraded
    "NASDAQ": "NDX",  # needs a paid plan on this key; kept mapped for when it's upgraded
    "DXY": "UUP",  # ETF proxy — see note above
    "EURUSD": "EUR/USD",
}

# The default watchlist for the terminal and for portfolio optimization —
# deliberately small and diversified across asset classes, matching what a
# single-instrument day trader (you, on Gold) would actually want context on.
DEFAULT_WATCHLIST = ["GOLD", "SILVER", "OIL", "BTC", "SPX", "NASDAQ", "DXY"]


def resolve(symbol: str) -> str:
    """Turn a friendly name (case-insensitive) into a real Yahoo ticker."""
    return ALIASES.get(symbol.upper(), symbol)


def resolve_twelvedata(symbol: str) -> str:
    """Turn a friendly name into a Twelve Data symbol. Falls back to the
    Yahoo ticker (still often close enough, e.g. "AAPL") and finally the
    raw input if neither map recognizes it."""
    upper = symbol.upper()
    return TWELVEDATA_ALIASES.get(upper, ALIASES.get(upper, symbol))


def display_name(symbol: str) -> str:
    """The reverse of resolve() — used for labels in the terminal UI."""
    for name, ticker in ALIASES.items():
        if ticker == symbol:
            return name
    return symbol


def is_commodity_or_index_alias(symbol: str) -> bool:
    """True for the app's own built-in non-stock names (GOLD, SILVER, BTC,
    SPX, ...). Finnhub's news/earnings/fundamentals endpoints are stock-
    oriented and some of these collide with real, unrelated tickers on
    Finnhub — "GOLD" is Gold.com Inc, a precious-metals distributor, not
    the commodity — so callers use this to skip those providers for these
    names rather than silently attributing a stranger's company data to
    the instrument the user actually means."""
    return symbol.upper() in ALIASES
