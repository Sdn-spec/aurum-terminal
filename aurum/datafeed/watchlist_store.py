"""Persists the user's own watchlist (add/remove/rename) to a small local
JSON file, falling back to universe.DEFAULT_WATCHLIST until it's ever been
customized. Mirrors how aurum.web.server persists account equity in
data/state.json — same idea, just for the symbol list instead.

A symbol here is just a name resolved through aurum.datafeed.universe.resolve()
at read time, same as the built-in ones — nothing is validated against a real
ticker on add, so a typo just shows as a failed quote (the "—" the UI already
shows for the built-in symbols Twelve Data's free tier can't cover), not a
rejected add.
"""

import json
from pathlib import Path
from typing import List

from .universe import DEFAULT_WATCHLIST

WATCHLIST_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "watchlist.json"


class DuplicateSymbolError(ValueError):
    pass


class SymbolNotFoundError(ValueError):
    pass


def load_watchlist() -> List[str]:
    if WATCHLIST_PATH.exists():
        try:
            data = json.loads(WATCHLIST_PATH.read_text())
            symbols = data.get("symbols")
            if isinstance(symbols, list) and symbols:
                return [str(s).upper() for s in symbols]
        except (json.JSONDecodeError, OSError):
            pass
    return list(DEFAULT_WATCHLIST)


def save_watchlist(symbols: List[str]) -> None:
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_PATH.write_text(json.dumps({"symbols": symbols}, indent=2))


def add_symbol(name: str) -> List[str]:
    name = name.strip().upper()
    if not name:
        raise ValueError("symbol name can't be empty")
    symbols = load_watchlist()
    if name in symbols:
        raise DuplicateSymbolError(f"{name} is already on the watchlist")
    symbols.append(name)
    save_watchlist(symbols)
    return symbols


def remove_symbol(name: str) -> List[str]:
    name = name.strip().upper()
    symbols = load_watchlist()
    if name not in symbols:
        raise SymbolNotFoundError(f"{name} is not on the watchlist")
    symbols.remove(name)
    save_watchlist(symbols)
    return symbols


def rename_symbol(old_name: str, new_name: str) -> List[str]:
    old_name = old_name.strip().upper()
    new_name = new_name.strip().upper()
    if not new_name:
        raise ValueError("new symbol name can't be empty")
    symbols = load_watchlist()
    if old_name not in symbols:
        raise SymbolNotFoundError(f"{old_name} is not on the watchlist")
    if new_name != old_name and new_name in symbols:
        raise DuplicateSymbolError(f"{new_name} is already on the watchlist")
    symbols[symbols.index(old_name)] = new_name
    save_watchlist(symbols)
    return symbols
