# aurum-terminal

A personal, single-user trading desk — live-ish quotes, 20+ years of daily
history, portfolio optimization, price forecasting, a setup scanner, a risk
gate, and a decision memo that ties them together. Two interfaces share the
same backend: a **website** (the default for now) and a **Textual TUI**
(switch to it later with `python3 -m aurum.terminal.app` — same data, same
modules, just a different screen).

Built by pulling in two things pointed at directly, plus a third source of
inspiration:

- **[skfolio](https://github.com/skfolio/skfolio)** — a real, scikit-learn-compatible
  portfolio optimization library. `aurum/optimize/` wraps it directly
  (mean-variance and Hierarchical Risk Parity), with a pure-numpy fallback
  if skfolio's dependency chain isn't installed.
- **[Kronos](https://github.com/shiyu-coder/Kronos)** — an open-source
  foundation model for candlestick forecasting. `aurum/forecast/kronos_vendor/`
  vendors its actual model code (MIT-licensed, unmodified except one import
  path fix — see `NOTICE.md` there) and `kronos_adapter.py` runs the
  **real Kronos-mini checkpoint**, downloaded from Hugging Face Hub, on CPU.
  This is not a stub — it's tested and working (~1-2s per 10-bar forecast).
- **Three "I built an AI trading system" reference carousels** (not code —
  design/architecture posts) consistently described the same pipeline:
  scan → signal → trade plan → risk gate → backtest → decision → journal.
  The **Risk Module**, **Setup Scanner**, and **Decision Memo** below are
  real implementations of that shape, not mockups.

Backtesting is **not duplicated here** — it reuses [nautilus-mini](../nautilus-mini)
as a sibling project, the same way any real system separates concerns
instead of copy-pasting an engine into every tool that needs one.

## Quick start (website)

```bash
cd aurum-terminal
.venv/bin/python3 -m unittest discover -s tests -v   # 52 tests, all passing
.venv/bin/python3 -m aurum.web.server                 # starts on http://127.0.0.1:8000
```

Open `http://127.0.0.1:8000` in a browser. It's a local server — nothing
leaves your machine except the outbound calls to Yahoo/Hugging Face for
data and model weights.

Set your account equity/peak-equity/today's realized P&L in the header —
that's what the Risk Gate and Decision Memo size against. It persists to
`data/state.json` between runs.

## Switching to the terminal later

```bash
.venv/bin/python3 -m aurum.terminal.app
```

Keys: `r` refresh quotes, `o` optimize, `f` baseline forecast, `k` Kronos
forecast, `b` backtest, arrows + Enter to pick a watchlist row, `q` to quit.
The scanner/risk/decision modules aren't wired into the TUI yet — they're
website-only for now; the panels are already tested and self-contained, so
wiring them in later is a UI-only change, not new logic.

If you're setting this up fresh instead of using the included `.venv`:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Architecture

```
aurum/
  datafeed/
    yahoo.py       Yahoo Finance chart API client (no key, no signup) —
                    live-ish quotes + up to ~25 years of daily history
    cache.py         local CSV cache so nothing refetches on every launch
    universe.py       friendly names -> tickers (Gold, Silver, BTC, SPX, ...)
  optimize/
    engine.py         skfolio (mean-variance / HRP) wrapper + numpy fallback
  forecast/
    base.py            the Forecaster interface both implementations share
    baseline.py         always-available statistical forecaster (random walk + drift)
    kronos_vendor/       vendored Kronos model source (MIT, from shiyu-coder/Kronos)
    kronos_adapter.py     runs real Kronos-mini via Hugging Face Hub weights
  signals/
    scanner.py          detects Trend/Momentum/Volume/Key-level confirmations and
                          classifies Breakout/Pullback/Momentum/Trend Continuation/Reversal
  risk/
    engine.py            pre-trade gate: position size, exposure, drawdown,
                          volatility, daily loss — any failed check blocks the trade
  decision/
    memo.py               combines scanner + risk + a trade plan into one
                            APPROVED / WATCHLIST / REJECTED verdict, with reasons
  backtest/
    adapter.py             bridges aurum's data to nautilus-mini's tested backtest engine
  web/
    server.py               FastAPI backend — one JSON endpoint per module above
    static/                  the dashboard: index.html + style.css + app.js (vanilla, no build step)
  terminal/
    app.py                   the Textual TUI — same backend, older interface
tests/                       52 tests: yahoo client (mocked HTTP), cache, optimizer,
                              baseline forecaster, scanner, risk, decision memo,
                              4 headless Textual UI tests, 10 FastAPI endpoint tests
```

## What's real vs. what to know about

**Real and tested:**
- Yahoo's chart API genuinely returns live-ish quotes and deep history with
  no API key — confirmed with real requests: 25 years of Gold monthly data,
  458 15-minute bars over 5 days, current price/day-high/day-low.
- skfolio mean-variance and HRP both run and produce valid long-only weights
  on real return data.
- Kronos-mini loads its real pretrained weights (~31MB, cached after first
  run) and produces genuine forecasts on CPU in 1-2 seconds.
- The scanner, risk gate, and decision memo are exercised end-to-end through
  the live FastAPI server (not just unit tests) — scan → risk → plan →
  verdict all checked against real request/response cycles.
- The backtest endpoint returns the strategy's equity curve *and* a
  buy-and-hold benchmark curve over the same period, so you can see whether
  the strategy actually beat doing nothing.

**Bugs found by actually running it, and fixed:**
- The watchlist table was truncating prices (`4,57` instead of `4,576.43`)
  because Textual's `DataTable` sizes columns from their initial content,
  not from values written in later via `update_cell` — fixed with explicit
  column widths.
- Refreshing all 7 watchlist symbols fired requests back-to-back and Yahoo
  429'd 6 of them, even though each individual call had retry/backoff —
  the burst itself was the problem. Fixed by spacing requests ~0.7-0.8s
  apart, in both the TUI and the website's frontend.
- `aurum/backtest/adapter.py` located the sibling `nautilus-mini` directory
  with a hardcoded `parents[3]` — broke the moment the file was one level
  deeper than expected (e.g. running from inside a git worktree). Fixed to
  search upward for the directory instead of assuming a fixed depth.

**Known limitation, environment-specific:** Yahoo's endpoint rate-limits
per IP, and both the original dev sandbox *and* a live user session hit
sustained HTTP 429s — sometimes for 20+ minutes, sometimes on 6 of 7
symbols in one burst. The retry/backoff and per-request spacing described
above measurably help but don't eliminate this; it's a property of Yahoo's
undocumented rate limit on a shared/high-traffic connection, not a bug in
this code (this is the same endpoint the popular `yfinance` library uses).
If quotes stall: wait a few minutes and hit Refresh again. The local cache
(`max_age_hours`, default 12h) minimizes how often this matters day to day.

**Kronos, honestly assessed:** Kronos-mini is the smallest checkpoint
(4.1M params) specifically because it's the only one realistic to run
without a GPU. It has no native confidence interval — the adapter reports
that explicitly rather than faking one (compare to the baseline forecaster,
which does give you a real, if simplistic, uncertainty band). Kronos-small/
base/large exist and are more capable, but expect CPU inference to get
meaningfully slower as you go up in size.

**Single-instrument backtesting.** Same scope decision as nautilus-mini:
this trades one instrument per backtest run, matching how you actually
trade (Gold). The portfolio optimizer, by contrast, *is* multi-instrument —
that's a different question (how to allocate capital across a watchlist)
from how to time entries on one of them.

**The Decision Memo never places a trade.** It's a recommendation — take
it, watch it, or skip it — with the reasons spelled out. A failed risk
check always rejects, regardless of how good the setup looks; that mirrors
"risk always comes first," not a suggestion.

## This is a personal tool

No auth, no multi-user support, no deployment story — it's meant to run on
your machine, for you. Nothing here should be exposed to the internet.
