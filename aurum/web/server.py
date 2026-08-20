"""The website: a FastAPI backend serving a small JSON API, plus the static
dashboard frontend. Every module built for the terminal (datafeed, optimize,
forecast, backtest, risk, signals, decision) is reused as-is here — this
file is purely a second presentation layer, not a second implementation.

Run with: python3 -m aurum.web.server
Then open http://127.0.0.1:8000 in a browser.
"""

import asyncio
import dataclasses
import json
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..alerts import store as alerts_store
from ..backtest.adapter import run_strategy
from ..datafeed import cache, finnhub, fred, universe, watchlist_store, yahoo
from ..decision import memo as decision_memo
from ..forecast import baseline
from ..optimize import engine as optimize_engine
from ..report import analyzer
from ..signals import scanner

STATE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "state.json"
DEFAULT_STATE = {"equity": 3000.0, "peak_equity": 3000.0, "realized_pnl_today": 0.0}

# Process-local caches for the research providers: macro barely moves within a
# day and news/earnings don't need to be refetched on every Analyze click, so
# these avoid hammering FRED/Finnhub without needing a full disk-cache layer
# like aurum.datafeed.cache uses for price data.
_MACRO_CACHE_TTL_SECONDS = 6 * 3600
_NEWS_CACHE_TTL_SECONDS = 3600
_macro_cache = {"data": None, "at": 0.0}
_news_cache: dict = {}  # symbol -> ((news, earnings), fetched_at)


def _get_macro_snapshot_cached() -> Optional[list]:
    api_key = fred.resolve_api_key()
    if not api_key:
        return None
    now = time.time()
    if _macro_cache["data"] is not None and now - _macro_cache["at"] < _MACRO_CACHE_TTL_SECONDS:
        return _macro_cache["data"]
    try:
        snapshot = fred.get_macro_snapshot(api_key)
    except yahoo.DataFeedError:
        return _macro_cache["data"]  # serve stale (possibly None) rather than fail the whole report
    _macro_cache["data"] = [dataclasses.asdict(s) for s in snapshot]
    _macro_cache["at"] = now
    return _macro_cache["data"]


def _get_news_and_earnings_cached(symbol: str):
    # GOLD, SILVER, BTC, SPX, ... are this app's own commodity/index/crypto
    # names, not Finnhub stock tickers — some of them collide with real,
    # unrelated companies on Finnhub (GOLD is Gold.com Inc, a precious-metals
    # distributor). Skip the call entirely rather than risk attributing a
    # stranger's earnings/news to the instrument the user actually means.
    if universe.is_commodity_or_index_alias(symbol):
        return [], None
    api_key = finnhub.resolve_api_key()
    if not api_key:
        return [], None
    now = time.time()
    cached = _news_cache.get(symbol)
    if cached and now - cached[1] < _NEWS_CACHE_TTL_SECONDS:
        return cached[0]
    try:
        news_items = finnhub.get_company_news(symbol, api_key)
        earnings_event = finnhub.get_next_earnings(symbol, api_key)
    except yahoo.DataFeedError:
        return cached[0] if cached else ([], None)
    result = ([dataclasses.asdict(n) for n in news_items], dataclasses.asdict(earnings_event) if earnings_event else None)
    _news_cache[symbol] = (result, now)
    return result


_FUNDAMENTALS_CACHE_TTL_SECONDS = 6 * 3600  # ratios like P/E don't move meaningfully within a day
_fundamentals_cache: dict = {}  # symbol -> (fundamentals_dict_or_None, fetched_at)


def _get_fundamentals_cached(symbol: str) -> Optional[dict]:
    # same reasoning as _get_news_and_earnings_cached: GOLD/SILVER/BTC/... are
    # this app's own names, not Finnhub tickers, and some collide with real
    # unrelated companies -- never send them to a stock-fundamentals endpoint.
    if universe.is_commodity_or_index_alias(symbol):
        return None
    api_key = finnhub.resolve_api_key()
    if not api_key:
        return None
    now = time.time()
    cached = _fundamentals_cache.get(symbol)
    if cached and now - cached[1] < _FUNDAMENTALS_CACHE_TTL_SECONDS:
        return cached[0]
    try:
        fundamentals = finnhub.get_fundamentals(symbol, api_key)
    except yahoo.DataFeedError:
        return cached[0] if cached else None
    result = dataclasses.asdict(fundamentals) if fundamentals else None
    _fundamentals_cache[symbol] = (result, now)
    return result


app = FastAPI(title="Aurum")


def _to_dict(obj):
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return obj


def _plan_dict(plan) -> dict:
    # dataclasses.asdict() only serializes actual fields — risk_per_unit,
    # reward_per_unit, and risk_reward_ratio are @property, so they'd
    # silently vanish from the JSON without being added back explicitly.
    d = dataclasses.asdict(plan)
    d["risk_per_unit"] = plan.risk_per_unit
    d["reward_per_unit"] = plan.reward_per_unit
    d["risk_reward_ratio"] = plan.risk_reward_ratio
    return d


def _risk_dict(risk) -> dict:
    d = dataclasses.asdict(risk)
    d["status"] = risk.status  # same @property gap as _plan_dict
    return d


def _memo_dict(memo) -> dict:
    return {
        "symbol": memo.symbol,
        "scan": _to_dict(memo.scan),
        "risk": _risk_dict(memo.risk),
        "plan": _plan_dict(memo.plan),
        "verdict": memo.verdict,
        "reasons": memo.reasons,
    }


def _analysis_report_dict(report) -> dict:
    return {
        "symbol": report.symbol,
        "last_close": report.last_close,
        "research": _to_dict(report.research),
        "debate": _to_dict(report.debate),
        "scan": _to_dict(report.scan),
        "risk": _risk_dict(report.risk),  # RiskAssessment.status is an @property, needs the same fix as _memo_dict
        "day_trade": _to_dict(report.day_trade),
        "long_term": _to_dict(report.long_term),
        "verdict": report.verdict,
        "confidence": report.confidence,
        "score": report.score,
        "summary": report.summary,
        "macro": report.macro,
        "news": report.news,
        "earnings": report.earnings,
        "fundamentals": report.fundamentals,
    }


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return {**DEFAULT_STATE, **json.loads(STATE_PATH.read_text())}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_STATE)


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


# ---- watchlist / quotes / history ---------------------------------------


def _watchlist_dict(symbols):
    return [{"name": name, "ticker": universe.resolve(name)} for name in symbols]


@app.get("/api/watchlist")
async def get_watchlist():
    return _watchlist_dict(watchlist_store.load_watchlist())


@app.post("/api/watchlist")
async def post_watchlist(payload: dict):
    try:
        symbols = await asyncio.to_thread(watchlist_store.add_symbol, payload.get("name", ""))
    except watchlist_store.DuplicateSymbolError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _watchlist_dict(symbols)


@app.put("/api/watchlist/{name}")
async def put_watchlist(name: str, payload: dict):
    try:
        symbols = await asyncio.to_thread(watchlist_store.rename_symbol, name, payload.get("name", ""))
    except watchlist_store.SymbolNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except watchlist_store.DuplicateSymbolError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _watchlist_dict(symbols)


@app.delete("/api/watchlist/{name}")
async def delete_watchlist(name: str):
    try:
        symbols = await asyncio.to_thread(watchlist_store.remove_symbol, name)
    except watchlist_store.SymbolNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _watchlist_dict(symbols)


# ---- price alerts ---------------------------------------------------------
# There's no background poller in this app -- these are just the persisted
# rules. The frontend checks them client-side against the watchlist's own
# live quote poll (see app.js's startLiveWatchlistLoop) and calls DELETE
# here itself once a rule fires, so alerts are one-shot, not repeating.


@app.get("/api/alerts")
async def get_alerts():
    return [dataclasses.asdict(a) for a in await asyncio.to_thread(alerts_store.load_price_alerts)]


@app.post("/api/alerts")
async def post_alert(payload: dict):
    try:
        alert = await asyncio.to_thread(
            alerts_store.add_price_alert, payload.get("symbol", ""), payload.get("condition", ""), float(payload.get("price", 0))
        )
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    return dataclasses.asdict(alert)


@app.delete("/api/alerts/{alert_id}")
async def delete_alert(alert_id: str):
    try:
        await asyncio.to_thread(alerts_store.remove_price_alert, alert_id)
    except alerts_store.AlertNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"removed": alert_id}


@app.get("/api/quote/{name}")
async def get_quote(name: str):
    try:
        quote = await asyncio.to_thread(cache.get_quote, name)
    except yahoo.DataFeedError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return _to_dict(quote)


@app.get("/api/history/{name}")
async def get_history(name: str, range: str = "10y", interval: str = "1d"):
    try:
        bars = await asyncio.to_thread(cache.get_history, name, range, interval)
    except yahoo.DataFeedError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not bars:
        raise HTTPException(status_code=502, detail=f"No history returned for {name}")
    return [_to_dict(b) for b in bars]


# ---- portfolio optimizer --------------------------------------------------


@app.get("/api/optimize")
async def get_optimize(method: str = "hrp"):
    bars_by_symbol = {}
    failed = []
    for name in watchlist_store.load_watchlist():
        try:
            bars_by_symbol[name] = await asyncio.to_thread(cache.get_history, name, "10y", "1d")
        except yahoo.DataFeedError:
            failed.append(name)
    if len(bars_by_symbol) < 2:
        raise HTTPException(status_code=502, detail=f"Not enough symbols loaded to optimize (failed: {failed})")
    returns = optimize_engine.returns_from_bars(bars_by_symbol)
    if len(returns) < 30:
        raise HTTPException(status_code=422, detail="Not enough overlapping history yet to optimize")
    try:
        result = optimize_engine.optimize(returns, method=method)
    except Exception as e:  # noqa: BLE001 - skfolio can raise on a near-singular/degenerate correlation structure
        raise HTTPException(status_code=422, detail=f"Optimizer failed on this data ({e}) — try the other method")
    return {**_to_dict(result), "skipped_symbols": failed}


@app.get("/api/correlation")
async def get_correlation():
    """Pairwise correlation of daily returns across the whole watchlist —
    flags positions that look diversified by name but actually move
    together (Gold/Silver/DXY are the classic case here)."""
    bars_by_symbol = {}
    failed = []
    for name in watchlist_store.load_watchlist():
        try:
            bars_by_symbol[name] = await asyncio.to_thread(cache.get_history, name, "10y", "1d")
        except yahoo.DataFeedError:
            failed.append(name)
    if len(bars_by_symbol) < 2:
        raise HTTPException(status_code=502, detail=f"Not enough symbols loaded to correlate (failed: {failed})")
    returns = optimize_engine.returns_from_bars(bars_by_symbol)
    if len(returns) < 30:
        raise HTTPException(status_code=422, detail="Not enough overlapping history yet to correlate")
    result = optimize_engine.correlation_matrix(returns)
    return {**_to_dict(result), "skipped_symbols": failed}


# ---- forecasting ------------------------------------------------------------


@app.get("/api/forecast/baseline/{name}")
async def get_forecast_baseline(name: str, horizon: int = 10):
    try:
        bars = await asyncio.to_thread(cache.get_history, name, "10y", "1d")
    except yahoo.DataFeedError as e:
        raise HTTPException(status_code=502, detail=str(e))
    try:
        result = baseline.forecast([b.close for b in bars], horizon=horizon)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _to_dict(result)


@app.get("/api/forecast/kronos/{name}")
async def get_forecast_kronos(name: str, horizon: int = 10):
    from ..forecast import kronos_adapter  # imported lazily: importing pulls in torch, which is slow

    try:
        bars = await asyncio.to_thread(cache.get_history, name, "10y", "1d")
    except yahoo.DataFeedError as e:
        raise HTTPException(status_code=502, detail=str(e))

    import pandas as pd

    df = pd.DataFrame(
        {
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        }
    )
    timestamps = pd.Series(pd.to_datetime([b.timestamp for b in bars], unit="s"))
    try:
        result = await asyncio.to_thread(kronos_adapter.forecast, df, timestamps, horizon)
    except Exception as e:  # noqa: BLE001 - Kronos is a best-effort optional path
        raise HTTPException(status_code=503, detail=f"Kronos unavailable: {e}")
    return _to_dict(result)


# ---- setup scanner ----------------------------------------------------------


@app.get("/api/scan/{name}")
async def get_scan(name: str):
    try:
        bars = await asyncio.to_thread(cache.get_history, name, "10y", "1d")
    except yahoo.DataFeedError as e:
        raise HTTPException(status_code=502, detail=str(e))
    try:
        result = scanner.scan(bars, name)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _to_dict(result)


# ---- risk + decision memo ----------------------------------------------------


@app.get("/api/state")
async def get_state():
    return _load_state()


@app.post("/api/state")
async def post_state(payload: dict):
    state = _load_state()
    for key in ("equity", "peak_equity", "realized_pnl_today"):
        if key in payload:
            state[key] = float(payload[key])
    state["peak_equity"] = max(state["peak_equity"], state["equity"])
    _save_state(state)
    return state


@app.get("/api/decision/{name}")
async def get_decision(name: str):
    try:
        bars = await asyncio.to_thread(cache.get_history, name, "10y", "1d")
    except yahoo.DataFeedError as e:
        raise HTTPException(status_code=502, detail=str(e))

    state = _load_state()
    try:
        result = decision_memo.decide_for_symbol(
            name, bars, state["equity"], state["peak_equity"], state["realized_pnl_today"]
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _memo_dict(result)


@app.get("/api/fund")
async def get_fund():
    """The "private hedge fund" view: run scan -> risk -> decide across the
    whole watchlist at once, then suggest how to split capital across
    whatever cleared the bar. Reuses decide_for_symbol per name — nothing
    new to get wrong here, just aurum.fund.engine orchestrating the pieces
    that already exist."""
    from ..fund.engine import scan_watchlist

    state = _load_state()
    bars_by_symbol = {}
    fetch_errors = {}
    for name in watchlist_store.load_watchlist():
        try:
            bars_by_symbol[name] = await asyncio.to_thread(cache.get_history, name, "10y", "1d")
        except yahoo.DataFeedError as e:
            fetch_errors[name] = str(e)

    report = await asyncio.to_thread(
        scan_watchlist, bars_by_symbol, state["equity"], state["peak_equity"], state["realized_pnl_today"]
    )

    entries = [
        {"symbol": e.symbol, "memo": _memo_dict(e.memo) if e.memo else None, "error": e.error} for e in report.entries
    ]
    entries += [{"symbol": name, "memo": None, "error": err} for name, err in fetch_errors.items()]

    return {
        "entries": entries,
        "allocation": _to_dict(report.allocation) if report.allocation else None,
        "approved_symbols": report.approved_symbols,
        "watchlist_symbols": report.watchlist_symbols,
    }


# ---- one-input analysis report -------------------------------------------------


@app.get("/api/macro")
async def get_macro():
    """The standalone macro snapshot (fed funds rate, CPI, unemployment,
    10-year yield) — not symbol-specific, cached for a few hours since none
    of these move intraday. Returns an empty list rather than 502 when no
    FRED key is configured, since macro context is optional everywhere it's
    used, not a hard dependency."""
    snapshot = await asyncio.to_thread(_get_macro_snapshot_cached)
    return snapshot or []


@app.get("/api/analyze/{name}")
async def get_analysis(name: str):
    """The "type a symbol, get a verdict" endpoint: research + bull/bear
    debate + risk check + day-trade and long-term plans, in one call. Works
    for anything cache.get_history can fetch — the default watchlist names
    or a raw ticker like AAPL — not just the commodities in DEFAULT_WATCHLIST.
    Folds in macro (FRED), news/earnings, and fundamentals (Finnhub) context
    when a key is configured — all optional, never block the report."""
    try:
        bars = await asyncio.to_thread(cache.get_history, name, "10y", "1d")
    except yahoo.DataFeedError as e:
        raise HTTPException(status_code=502, detail=str(e))

    state = _load_state()
    macro = await asyncio.to_thread(_get_macro_snapshot_cached)
    news, earnings = await asyncio.to_thread(_get_news_and_earnings_cached, name)
    fundamentals = await asyncio.to_thread(_get_fundamentals_cached, name)
    try:
        result = await asyncio.to_thread(
            analyzer.analyze_symbol,
            name,
            bars,
            state["equity"],
            state["peak_equity"],
            state["realized_pnl_today"],
            macro=macro,
            news=news,
            earnings=earnings,
            fundamentals=fundamentals,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # "Did this change since I last checked?" -- compared against whatever
    # verdict was recorded the last time this symbol was analyzed (no
    # background poller needed; the comparison just happens on next open).
    previous_verdict = await asyncio.to_thread(alerts_store.get_last_verdict, name)
    await asyncio.to_thread(alerts_store.set_last_verdict, name, result.verdict)

    response = _analysis_report_dict(result)
    response["previous_verdict"] = previous_verdict
    return response


# ---- backtest -----------------------------------------------------------------


@app.get("/api/backtest/{name}")
async def get_backtest(name: str, cash: float = 3000.0):
    from strategies.trend_pullback import TrendPullbackStrategy  # noqa: E402 - path wired by backtest.adapter import above

    try:
        bars = await asyncio.to_thread(cache.get_history, name, "10y", "1d")
    except yahoo.DataFeedError as e:
        raise HTTPException(status_code=502, detail=str(e))

    strategy = TrendPullbackStrategy(name, risk_per_trade=cash * 0.01)
    portfolio, stats = await asyncio.to_thread(run_strategy, strategy, bars, name, cash)

    buy_hold_curve = []
    if bars:
        units = cash / bars[0].close
        buy_hold_curve = [{"timestamp": b.timestamp, "equity": units * b.close} for b in bars]

    # equity_curve entries are (datetime, equity) — convert to unix seconds for the frontend
    strategy_curve = [{"timestamp": int(ts.timestamp()), "equity": e} for ts, e in portfolio.equity_curve]

    return {
        "stats": _to_dict(stats),
        "strategy_equity_curve": strategy_curve,
        "buy_hold_equity_curve": buy_hold_curve,
        "bar_count": len(bars),
    }


# ---- static frontend -------------------------------------------------------------

_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(_STATIC_DIR / "index.html"))


def main() -> None:
    import uvicorn

    uvicorn.run("aurum.web.server:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
