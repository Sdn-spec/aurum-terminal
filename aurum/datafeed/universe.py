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

# The default watchlist for the terminal and for portfolio optimization —
# deliberately small and diversified across asset classes, matching what a
# single-instrument day trader (you, on Gold) would actually want context on.
DEFAULT_WATCHLIST = ["GOLD", "SILVER", "OIL", "BTC", "SPX", "NASDAQ", "DXY"]


def resolve(symbol: str) -> str:
    """Turn a friendly name (case-insensitive) into a real Yahoo ticker."""
    return ALIASES.get(symbol.upper(), symbol)


def display_name(symbol: str) -> str:
    """The reverse of resolve() — used for labels in the terminal UI."""
    for name, ticker in ALIASES.items():
        if ticker == symbol:
            return name
    return symbol
