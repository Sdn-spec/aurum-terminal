# aurum-terminal

A personal, single-user trading terminal — live-ish quotes, 20+ years of
daily history, portfolio optimization, and price forecasting, combined
behind one Textual TUI screen. Built by pulling in two things you pointed
at directly:

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

Backtesting is **not duplicated here** — it reuses [nautilus-mini](../nautilus-mini)
as a sibling project, the same way any real system separates concerns
instead of copy-pasting an engine into every tool that needs one.

## Why a TUI, not a browser dashboard

"Bloomberg terminal" is a specific interface idiom — dense, keyboard-driven,
multiple panels on one screen, no mouse required. Textual gets you that for
real, running in your actual terminal, not a browser tab pretending to be one.

## Quick start

```bash
cd aurum-terminal
source .venv/bin/activate        # a venv is already set up with everything installed
python3 -m unittest discover -s tests -v    # 21 tests, all passing
python3 -m aurum.terminal.app               # launch the terminal
```

Keys once it's running: `r` refresh quotes, `o` optimize the watchlist,
`f` baseline forecast, `k` Kronos forecast, `b` backtest Trend Pullback on
the selected symbol's real history, arrows + Enter to pick a watchlist row,
`q` to quit.

If you're setting this up fresh instead of using the included `.venv`:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Architecture

```
aurum/
  datafeed/
    yahoo.py       Yahoo Finance chart API client (no key, no signup) —
                    live-ish quotes + up to ~25 years of daily history
    cache.py        local CSV cache so the terminal doesn't refetch on every launch
    universe.py      friendly names -> tickers (Gold, Silver, BTC, SPX, ...)
  optimize/
    engine.py        skfolio (mean-variance / HRP) wrapper + numpy fallback
  forecast/
    base.py           the Forecaster interface both implementations share
    baseline.py        always-available statistical forecaster (random walk + drift)
    kronos_vendor/      vendored Kronos model source (MIT, from shiyu-coder/Kronos)
    kronos_adapter.py    runs real Kronos-mini via Hugging Face Hub weights
  backtest/
    adapter.py        bridges aurum's data to nautilus-mini's tested backtest engine
  terminal/
    app.py             the Textual TUI — the thing that ties all of the above together
tests/                 21 tests: yahoo client (mocked HTTP), cache, optimizer,
                        baseline forecaster, and 4 headless Textual UI tests
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
- The Textual UI is tested headless (`tests/test_terminal_app.py`) — boots,
  populates the watchlist, and runs all four side panels without crashing.

**Known limitation you'll likely hit in a sandboxed dev environment (like
the one this was built in) but probably won't on your own machine:**
Yahoo's endpoint rate-limits per IP, and this environment shares an egress
IP across many concurrent sessions — during development here it got stuck
at HTTP 429 for over 20 minutes despite the very first request of the
session succeeding cleanly. The retry/backoff logic in `yahoo.py` is
correct (tested with mocked 429 responses), and the parsing/caching logic
was verified against real captured responses — what couldn't be fully
re-verified live, from here, is a sustained connection. On a normal
residential/office connection this endpoint is what the popular `yfinance`
library uses under the hood and works reliably for personal-scale use.
If you do hit 429s: wait a few minutes, or lower your own request rate —
the cache (`max_age_hours`, default 12h) already minimizes how often this
happens in normal use.

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

## This is a personal tool

No auth, no multi-user support, no deployment story — it's meant to run on
your machine, for you, the same way a real Bloomberg terminal is a seat
license for one person. Nothing here should be exposed to the internet.
