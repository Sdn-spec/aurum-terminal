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
# aurum.datafeed.provider). Unverified against a live key for the less
# common ones (OIL, DXY, NASDAQ) — adjust here if a symbol doesn't resolve.
TWELVEDATA_ALIASES = {
    "GOLD": "XAU/USD",
    "SILVER": "XAG/USD",
    "OIL": "WTI/USD",
    "BTC": "BTC/USD",
    "ETH": "ETH/USD",
    "SPX": "SPX",
    "NASDAQ": "NDX",
    "DXY": "DXY",
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
