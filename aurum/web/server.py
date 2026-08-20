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
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..backtest.adapter import run_strategy
from ..datafeed import cache, universe, yahoo
from ..decision import memo as decision_memo
from ..forecast import baseline
from ..optimize import engine as optimize_engine
from ..report import analyzer
from ..signals import scanner

STATE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "state.json"
DEFAULT_STATE = {"equity": 3000.0, "peak_equity": 3000.0, "realized_pnl_today": 0.0}

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


@app.get("/api/watchlist")
async def get_watchlist():
    return [{"name": name, "ticker": universe.resolve(name)} for name in universe.DEFAULT_WATCHLIST]


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
    for name in universe.DEFAULT_WATCHLIST:
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
    for name in universe.DEFAULT_WATCHLIST:
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


@app.get("/api/analyze/{name}")
async def get_analysis(name: str):
    """The "type a symbol, get a verdict" endpoint: research + bull/bear
    debate + risk check + day-trade and long-term plans, in one call. Works
    for anything cache.get_history can fetch — the default watchlist names
    or a raw ticker like AAPL — not just the commodities in DEFAULT_WATCHLIST."""
    try:
        bars = await asyncio.to_thread(cache.get_history, name, "10y", "1d")
    except yahoo.DataFeedError as e:
        raise HTTPException(status_code=502, detail=str(e))

    state = _load_state()
    try:
        result = await asyncio.to_thread(
            analyzer.analyze_symbol, name, bars, state["equity"], state["peak_equity"], state["realized_pnl_today"]
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _analysis_report_dict(result)


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
