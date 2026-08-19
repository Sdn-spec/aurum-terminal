# aurum-terminal

A personal, single-user trading desk — live-ish quotes, 20+ years of daily
history, portfolio optimization, price forecasting, a setup scanner, a risk
gate, a decision memo, and a fund-wide scan across your whole watchlist at
once. Two interfaces share the same backend: a **website** (the default for
now) and a **Textual TUI** (switch to it later with
`python3 -m aurum.terminal.app` — same data, same modules, just a different
screen).

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
  scan → signal → trade plan → risk gate → backtest → decision → journal →
  and, in the "AI hedge fund" variant, one system running that whole loop
  across many symbols at once and allocating capital across whatever passed.
  The **Risk Module**, **Setup Scanner**, **Decision Memo**, and **Fund**
  view below are real implementations of that shape, not mockups.

Backtesting is **not duplicated here** — it reuses [nautilus-mini](../nautilus-mini)
as a sibling project, the same way any real system separates concerns
instead of copy-pasting an engine into every tool that needs one.

## Quick start (website)

```bash
cd aurum-terminal
.venv/bin/python3 -m unittest discover -s tests -v   # 81 tests, all passing
.venv/bin/python3 -m aurum.web.server                 # starts on http://127.0.0.1:8000
```

Open `http://127.0.0.1:8000` in a browser. It's a local server — nothing
leaves your machine except the outbound calls to Yahoo/Twelve Data/Hugging
Face for data and model weights.

Set your account equity/peak-equity/today's realized P&L in the header —
that's what the Risk Gate, Decision Memo, and Fund view size against. It
persists to `data/state.json` between runs.

**The Fund tab is the flagship view** — it's the default tab. Press "Scan
whole watchlist" to run the scanner and risk gate over every symbol at once
and see a suggested capital split (via skfolio) across whatever cleared the
bar. Nothing here trades for you — it's a recommendation, same as the
single-symbol Decision Memo.

**The UI:** a dark, glass-panel dashboard with real motion, not decoration —
a live price flashes green/red on change (the same way a real terminal
does), the chart's line actually draws itself in and glows, the Risk panel
renders each check as a radial gauge against its limit, and the Fund/
Optimizer views render the suggested split as an animated donut plus
per-row allocation bars. Everything respects `prefers-reduced-motion`.

## If Yahoo is rate-limiting you (HTTP 429 / 502 errors)

This happened repeatedly during development, and to a live user session too
— it's a real, not-fully-avoidable property of Yahoo's undocumented rate
limit, not a bug in this code (yfinance, the popular Python library, uses
the same endpoint and has the same issue). Three real mitigations are
already in place:

1. **A short-TTL quote cache (45s) and long-TTL history cache (12h), both
   with per-key locking.** Before this, every click and every open browser
   tab fired its own request — the server log showed the exact problem:
   `/api/quote/GOLD` hit from four different client ports within the same
   second. Now concurrent callers for the same symbol share one fetch.
2. **A failed refresh falls back to whatever's cached, however stale**,
   instead of surfacing a 502 when perfectly usable data already exists.
3. **A second provider, Twelve Data, as an automatic fallback** — only
   attempted when Yahoo fails *and* you've configured a free key. Get one
   (no credit card) at <https://twelvedata.com/pricing>, then either:
   ```bash
   export TWELVEDATA_API_KEY=your-key-here
   ```
   or create `data/config.json`:
   ```json
   {"twelvedata_api_key": "your-key-here"}
   ```
   No key configured means no fallback attempt — you'll see Yahoo's own
   error, unchanged.

   **Verified live against a real free-tier key (2026-08-20), not a
   guess:** GOLD and BTC work directly — confirmed with real data, including
   5000 real daily Gold bars back to 2008. SILVER, OIL, SPX, and NASDAQ
   return "available starting with the Grow or Venture plan" on the free
   tier — they fall through to Yahoo's own error when both providers fail,
   same as having no fallback at all for those four. DXY has no free-tier
   index symbol at all; it's mapped to `UUP` (an ETF that tracks dollar
   strength) as an approximate proxy, not the literal index. All of this is
   recorded with dates in `aurum/datafeed/universe.py`'s `TWELVEDATA_ALIASES`
   — if Twelve Data's free tier changes, that's where to update it.

Other providers considered and why they lost: **Google Finance** has no
public API anymore (the old one was deprecated years ago). **Stooq** is
blocked by an actual JavaScript proof-of-work challenge, not a rate limit —
no HTTP client can pass it, key or not. **Alpha Vantage** is a real
official API, but its free tier is 25 requests/day, which a 7-symbol
watchlist burns through almost immediately for an interactive dashboard.
Twelve Data's free tier (hundreds of requests/day) is the most usable
combination of "actually free," "actually documented," and "actually
enough volume" found.

## Switching to the terminal later

```bash
.venv/bin/python3 -m aurum.terminal.app
```

Keys: `r` refresh quotes, `o` optimize, `f` baseline forecast, `k` Kronos
forecast, `b` backtest, arrows + Enter to pick a watchlist row, `q` to quit.
The scanner/risk/decision/fund modules aren't wired into the TUI yet —
they're website-only for now; the underlying modules are already tested
and self-contained, so wiring them in later is a UI-only change, not new
logic.

If you're setting this up fresh instead of using the included `.venv`:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Architecture

```
aurum/
  datafeed/
    yahoo.py         Yahoo Finance chart API client (no key, no signup) —
                       live-ish quotes + up to ~25 years of daily history
    twelvedata.py      fallback provider (needs a free key) — same Quote/
                        HistoryBar shape as yahoo.py, so nothing downstream cares
    provider.py         picks Yahoo first, Twelve Data second if configured
    cache.py             disk cache in front of provider.py: 12h history TTL,
                          45s quote TTL, per-key locking, stale-on-failure fallback
    universe.py            friendly names -> tickers, one alias map per provider
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
    memo.py               decide_for_symbol(): scan -> plan -> risk -> one
                            APPROVED / WATCHLIST / REJECTED verdict, with reasons —
                            the single source of truth both /api/decision and
                            the fund scanner call, so they can't drift apart
  fund/
    engine.py              runs decide_for_symbol across the whole watchlist,
                            then suggests a capital split across whatever's approved
  backtest/
    adapter.py               bridges aurum's data to nautilus-mini's tested backtest engine
  web/
    server.py                 FastAPI backend — one JSON endpoint per module above
    static/                    the dashboard: index.html + style.css + app.js (vanilla, no build step)
  terminal/
    app.py                     the Textual TUI — same backend, older interface
tests/                         81 tests: yahoo + twelvedata clients (mocked HTTP),
                                provider fallback routing, cache (incl. concurrent-
                                request de-dup and stale-on-failure), optimizer,
                                baseline forecaster, scanner, risk, decision memo,
                                fund engine, 4 headless Textual UI tests, 14 FastAPI
                                endpoint tests
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
- The scanner, risk gate, decision memo, and fund scan are exercised
  end-to-end through the live FastAPI server (not just unit tests) — scan →
  risk → plan → verdict → allocation, all checked against real
  request/response cycles, including a run that produced a real 4-of-7
  APPROVED split with a genuine skfolio HRP allocation across the winners.
- The backtest endpoint returns the strategy's equity curve *and* a
  buy-and-hold benchmark curve over the same period, so you can see whether
  the strategy actually beat doing nothing.
- Concurrent-request de-duplication is tested with real threads (5 threads
  requesting the same symbol simultaneously → exactly 1 real fetch).

**Bugs found by actually running it, and fixed:**
- The watchlist table was truncating prices (`4,57` instead of `4,576.43`)
  because Textual's `DataTable` sizes columns from their initial content,
  not from values written in later via `update_cell` — fixed with explicit
  column widths.
- Refreshing all 7 watchlist symbols fired requests back-to-back and Yahoo
  429'd 6 of them, even though each individual call had retry/backoff — the
  burst itself was the problem, made worse by multiple browser tabs or a
  page reload firing overlapping request waves with zero coordination
  between them (a live server log showed exactly this: four different
  client ports hitting `/api/quote/GOLD` within the same second). Fixed
  with the quote/history caching + per-key locking described above, plus
  ~0.7-0.8s spacing between requests in the TUI and the website's frontend.
- `aurum/backtest/adapter.py` located the sibling `nautilus-mini` directory
  with a hardcoded `parents[3]` — broke the moment the file was one level
  deeper than expected (e.g. running from inside a git worktree). Fixed to
  search upward for the directory instead of assuming a fixed depth.
- `dataclasses.asdict()` silently drops `@property` fields — `TradePlan.
  risk_reward_ratio` and `RiskAssessment.status` vanished from the JSON API
  entirely, breaking the Decision and Fund panels' rendering. Fixed with
  explicit serializers (`_plan_dict`, `_risk_dict`) that add them back, plus
  tests that assert on their presence so this doesn't silently regress again.
- skfolio's HRP can raise on a near-singular/degenerate correlation
  structure (surfaced by an unstable test-data seed during development,
  but a real possible failure mode on real data too, e.g. two nearly
  identical price series). The fund scan and `/api/optimize` both now
  catch this and degrade to "no allocation" / a clean 422 instead of a
  raw 500.

**Known limitation, environment-specific:** Yahoo's endpoint rate-limits
per IP, and both dev sandboxes *and* a live user session hit sustained
HTTP 429s — sometimes for 20+ minutes, sometimes on 6 of 7 symbols in one
burst. The caching, locking, retry/backoff, and Twelve Data fallback
described above measurably help but can't fully eliminate this; it's a
property of Yahoo's undocumented rate limit, not a bug in this code. If
quotes still stall after the fixes above: configure a Twelve Data key, or
wait a few minutes and hit Refresh again.

**Kronos, honestly assessed:** Kronos-mini is the smallest checkpoint
(4.1M params) specifically because it's the only one realistic to run
without a GPU. It has no native confidence interval — the adapter reports
that explicitly rather than faking one (compare to the baseline forecaster,
which does give you a real, if simplistic, uncertainty band). Kronos-small/
base/large exist and are more capable, but expect CPU inference to get
meaningfully slower as you go up in size.

**Single-instrument backtesting.** Same scope decision as nautilus-mini:
this trades one instrument per backtest run, matching how you actually
trade (Gold). The portfolio optimizer and Fund view, by contrast, *are*
multi-instrument — that's a different question (how to allocate capital
across a watchlist) from how to time entries on one of them.

**Nothing here places a trade, ever.** The Decision Memo and Fund view are
recommendations — take it, watch it, or skip it — with the reasons spelled
out. A failed risk check always rejects, regardless of how good the setup
looks; that mirrors "risk always comes first," not a suggestion.

## This is a personal tool

No auth, no multi-user support, no deployment story — it's meant to run on
your machine, for you. Nothing here should be exposed to the internet.
