"""Interactive Brokers, as an optional source of *your own* account truth:
positions, balances, and real executions — which the Journal can import
instead of you retyping fills from memory.

Deliberately not a market-data provider. IBKR real-time data is a paid,
per-exchange subscription, whereas the Markets board covers 35 instruments
across four regions for free; swapping that over would cost money and cover
less. What IBKR uniquely knows is what *you* actually did.

Unlike every other provider here, there is no API key. IBKR's model is that
you run their software locally (IB Gateway, or TWS) and connect to it over a
socket, so "configured" means "a gateway is reachable at this host/port",
not "a secret is present". Nothing here works unless that process is running.

Setup, briefly:
  1. IB Gateway (or TWS) → Global Configuration → API → Settings
  2. tick "Enable ActiveX and Socket Clients", trust 127.0.0.1
  3. leave read-only enabled unless you intend to place orders from here
  4. note the port -- they differ by live vs paper, which is the usual trap:
       TWS live 7496 / paper 7497, Gateway live 4001 / paper 4002

Configure with IBKR_HOST / IBKR_PORT / IBKR_CLIENT_ID, or the ibkr_host,
ibkr_port and ibkr_client_id keys in data/config.json. The default is the
Gateway *paper* port, because defaulting anything to a live trading account
is not a thing this should do quietly.
"""

import asyncio
import json
import os
import queue
import threading
from typing import Callable, List, Optional

from ..datafeed import provider
from ..datafeed.yahoo import DataFeedError

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4002  # IB Gateway, paper trading
DEFAULT_CLIENT_ID = 17
# Kept short on purpose. A dashboard must never sit blocked on a broker
# socket -- the markets board already had to learn that lesson the hard way.
CONNECT_TIMEOUT_SECONDS = 5.0
CALL_TIMEOUT_SECONDS = 20.0

# IBKR sends DBL_MAX for "this number does not apply", most visibly as
# realizedPNL on an opening fill. Left alone it shows up as 1.8e308 in the UI.
_UNSET_THRESHOLD = 1e300


class BrokerError(DataFeedError):
    """Raised when IB Gateway can't be reached or returns something unusable.

    Subclasses DataFeedError so the web layer's existing handling applies."""


def _config() -> dict:
    if provider.CONFIG_PATH.exists():
        try:
            return json.loads(provider.CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def resolve_settings() -> dict:
    cfg = _config()
    port = os.environ.get("IBKR_PORT") or cfg.get("ibkr_port") or DEFAULT_PORT
    client_id = os.environ.get("IBKR_CLIENT_ID") or cfg.get("ibkr_client_id") or DEFAULT_CLIENT_ID
    try:
        port = int(port)
        client_id = int(client_id)
    except (TypeError, ValueError):
        port, client_id = DEFAULT_PORT, DEFAULT_CLIENT_ID
    return {
        "host": os.environ.get("IBKR_HOST") or cfg.get("ibkr_host") or DEFAULT_HOST,
        "port": port,
        "client_id": client_id,
    }


def _clean_float(value) -> Optional[float]:
    """Turn IBKR's not-applicable sentinel into a real absence."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or abs(f) > _UNSET_THRESHOLD:  # NaN or DBL_MAX
        return None
    return f


def _run_with_ib(work: Callable, timeout: float = CALL_TIMEOUT_SECONDS):
    """Run `work(ib)` against a freshly connected IB, on its own thread with
    its own event loop.

    ib_async is asyncio-based and its synchronous helpers drive the *current
    thread's* event loop, which would collide with the one FastAPI is already
    running. A dedicated thread sidesteps that entirely. Connections are
    short-lived rather than pooled: these calls are user-initiated, not on a
    poll loop, so the simplicity is worth more than the reconnect cost.
    """
    settings = resolve_settings()
    outbox: queue.Queue = queue.Queue(maxsize=1)

    def runner():
        loop = None
        ib = None
        try:
            from ib_async import IB  # imported lazily: optional dependency

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            ib = IB()
            ib.connect(
                settings["host"],
                settings["port"],
                clientId=settings["client_id"],
                timeout=CONNECT_TIMEOUT_SECONDS,
                readonly=True,  # this module never places orders
            )
            outbox.put(("ok", work(ib)))
        except ImportError as e:
            outbox.put(("err", BrokerError(
                "ib_async is not installed — run: pip install ib_async")))
        except Exception as e:  # noqa: BLE001 - surfaced verbatim to the caller
            outbox.put(("err", BrokerError(
                f"Could not talk to IB Gateway at {settings['host']}:{settings['port']} "
                f"({type(e).__name__}: {e}). Is the gateway running with the API enabled?")))
        finally:
            try:
                if ib is not None and ib.isConnected():
                    ib.disconnect()
            except Exception:
                pass
            try:
                if loop is not None:
                    loop.close()
            except Exception:
                pass

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    try:
        status, payload = outbox.get(timeout=timeout)
    except queue.Empty:
        raise BrokerError(
            f"IB Gateway did not respond within {timeout:.0f}s at "
            f"{settings['host']}:{settings['port']}")
    if status == "err":
        raise payload
    return payload


def get_status() -> dict:
    """Whether a gateway is actually reachable. Cheap enough to call on page
    load: it connects, reads the account list, and drops the connection."""
    settings = resolve_settings()
    try:
        accounts = _run_with_ib(lambda ib: list(ib.managedAccounts()), timeout=CONNECT_TIMEOUT_SECONDS + 5)
    except BrokerError as e:
        return {"available": False, "detail": str(e), **settings}
    return {"available": True, "accounts": accounts, "detail": None, **settings}


def get_account_summary() -> List[dict]:
    def work(ib):
        return [
            {"account": v.account, "tag": v.tag, "value": v.value, "currency": v.currency}
            for v in ib.accountSummary()
        ]

    return _run_with_ib(work)


def get_positions() -> List[dict]:
    def work(ib):
        rows = []
        for p in ib.positions():
            contract = p.contract
            rows.append({
                "account": p.account,
                "symbol": getattr(contract, "localSymbol", "") or getattr(contract, "symbol", ""),
                "sec_type": getattr(contract, "secType", ""),
                "exchange": getattr(contract, "exchange", ""),
                "currency": getattr(contract, "currency", ""),
                "position": _clean_float(p.position),
                "avg_cost": _clean_float(p.avgCost),
            })
        return rows

    return _run_with_ib(work)


def get_fills() -> List[dict]:
    """Today's executions. IBKR only serves the current session's fills over
    this call, so this is "what happened today", not full history."""
    def work(ib):
        rows = []
        for f in ib.fills():
            ex, contract = f.execution, f.contract
            report = f.commissionReport
            when = getattr(ex, "time", None) or getattr(f, "time", None)
            rows.append({
                "exec_id": getattr(ex, "execId", ""),
                "time": when.isoformat() if hasattr(when, "isoformat") else None,
                "symbol": getattr(contract, "localSymbol", "") or getattr(contract, "symbol", ""),
                "sec_type": getattr(contract, "secType", ""),
                "currency": getattr(contract, "currency", ""),
                "side": getattr(ex, "side", ""),  # "BOT" / "SLD"
                "shares": _clean_float(getattr(ex, "shares", None)),
                "price": _clean_float(getattr(ex, "price", None)),
                "commission": _clean_float(getattr(report, "commission", None)),
                # only meaningful on a closing fill; opening fills carry the
                # not-applicable sentinel, which _clean_float turns into None
                "realized_pnl": _clean_float(getattr(report, "realizedPNL", None)),
            })
        return rows

    return _run_with_ib(work)


def fills_to_journal_trades(fills: List[dict]) -> List[dict]:
    """Shape IBKR fills into the Journal's own trade payload.

    Only fills that actually realised a P&L become journal entries: the
    Journal is a record of completed trades with an outcome, and an opening
    fill has no result yet. Importing those too would fill the ledger with
    zero-P&L rows that drag the win rate toward meaningless.
    """
    trades = []
    for f in fills:
        pnl = f.get("realized_pnl")
        if pnl is None:
            continue
        commission = f.get("commission") or 0.0
        trades.append({
            "ts": f.get("time"),
            "instrument": f.get("symbol") or "?",
            # IBKR reports the side of *this* fill; a sell that realises P&L
            # is the close of a long, so the trade being recorded is a long.
            "direction": "long" if f.get("side") == "SLD" else "short",
            "entry": None,
            "exit": f.get("price"),
            "size": f.get("shares"),
            "pnl": round(pnl - commission, 2),  # net of commission
            "risk": None,
            "ema": None,
            "trend": None,
            "setup": "none",
            "notes": (f"Imported from Interactive Brokers (exec {f.get('exec_id', '')[:18]}). "
                      f"Gross {pnl:+.2f}, commission {commission:.2f}."),
            "confidence": None,
        })
    return trades
