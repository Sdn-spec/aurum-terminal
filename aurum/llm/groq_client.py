"""Turns an Analyze report's already-computed numbers into a short, plain-
English narrative via Groq's free-tier LLM API (OpenAI-compatible chat
completions) — no credit card, generous free tier (30 RPM / 14,400 RPD as
of writing). This module never computes anything itself: it takes the
same dict the frontend already renders and asks a model to explain it in
prose, the way an analyst's note would read instead of a checklist.

Needs a free key from https://console.groq.com/keys, set as
GROQ_API_KEY or {"groq_api_key": "..."} in data/config.json — same file
and resolution order as the other provider keys.
"""

import json
import os
import urllib.error
import urllib.request
from typing import Optional

from ..datafeed import provider
from ..datafeed.yahoo import DataFeedError

BASE_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = (
    "You are a plain-English trading-desk analyst. You're given a report that has "
    "already been fully computed by rule-based logic (a verdict, a bull/bear case, "
    "risk checks, trade plans) -- your only job is to explain that existing "
    "reasoning in clear prose, the way an analyst's note reads instead of a "
    "checklist. Do not invent facts, prices, or reasoning not present in the data "
    "you're given. Do not issue a directive to buy or sell -- describe the case "
    "for and against, and note what would change your read. Keep it to 3-4 short "
    "paragraphs, no headers, no bullet points, plain text."
)


def resolve_api_key() -> Optional[str]:
    key = os.environ.get("GROQ_API_KEY")
    if key:
        return key
    if provider.CONFIG_PATH.exists():
        try:
            return json.loads(provider.CONFIG_PATH.read_text()).get("groq_api_key")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _fmt(value, suffix: str = "") -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def build_prompt(report: dict) -> str:
    """`report` is exactly the dict aurum.web.server's /api/analyze/{name}
    already returns -- the same shape the frontend renders."""
    r = report
    research = r.get("research", {})
    debate = r.get("debate", {})
    day_trade = r.get("day_trade", {})
    long_term = r.get("long_term", {})
    risk = r.get("risk", {})
    fundamentals = r.get("fundamentals")
    macro = r.get("macro") or []
    earnings = r.get("earnings")

    lines = [
        f"Symbol: {r.get('symbol')}",
        f"Last close: {_fmt(r.get('last_close'))}",
        f"Verdict: {r.get('verdict')} ({r.get('confidence')} confidence, score {r.get('score')})",
        f"200-day trend: {research.get('trend_regime')}",
        f"Momentum (21d): {_fmt(research.get('momentum_pct'), '%')}",
        f"Annualized volatility: {_fmt(research.get('volatility_annualized_pct'), '%')}",
        f"Distance from 1y high/low: {_fmt(research.get('distance_from_year_high_pct'), '%')} / {_fmt(research.get('distance_from_year_low_pct'), '%')}",
        "",
        "Bull case: " + ("; ".join(debate.get("bull_points", [])) or "none"),
        "Bear case: " + ("; ".join(debate.get("bear_points", [])) or "none"),
        "",
        f"Day-trade plan: {day_trade.get('direction')}, entry {_fmt(day_trade.get('entry'))}, stop {_fmt(day_trade.get('stop'))}, "
        f"TP1 {_fmt(day_trade.get('take_profit_1'))}, TP2 {_fmt(day_trade.get('take_profit_2'))}, R:R {_fmt(day_trade.get('risk_reward_ratio'))}",
        f"Long-term plan: {long_term.get('direction')}, entry {_fmt(long_term.get('entry'))}, stop {_fmt(long_term.get('stop'))}, "
        f"TP1 {_fmt(long_term.get('take_profit_1'))}, TP2 {_fmt(long_term.get('take_profit_2'))}, R:R {_fmt(long_term.get('risk_reward_ratio'))}",
        "",
        f"Risk gate: {risk.get('status')}",
    ]
    if fundamentals:
        lines.append(
            f"Fundamentals: P/E {_fmt(fundamentals.get('pe_ttm'))}, beta {_fmt(fundamentals.get('beta'))}, "
            f"dividend yield {_fmt(fundamentals.get('dividend_yield_pct'), '%')}"
        )
    if earnings:
        lines.append(f"Next earnings: {earnings.get('date')}")
    if macro:
        macro_line = ", ".join(f"{m.get('label')}: {_fmt(m.get('latest_value'))}" for m in macro)
        lines.append(f"Macro backdrop: {macro_line}")

    return "\n".join(lines)


def generate_narrative(report: dict, api_key: str, model: str = DEFAULT_MODEL) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(report)},
        ],
        "temperature": 0.4,
        "max_tokens": 600,
    }
    request = urllib.request.Request(
        BASE_URL,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace") if hasattr(e, "read") else str(e)
        raise DataFeedError(f"Groq returned HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        raise DataFeedError(f"Could not reach Groq: {reason}") from e
    except json.JSONDecodeError as e:
        raise DataFeedError("Groq returned unparseable data") from e

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as e:
        raise DataFeedError(f"Groq returned an unexpected response shape: {data}") from e
