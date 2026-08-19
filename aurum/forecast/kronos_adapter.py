"""Wraps the real Kronos foundation model (vendored in `kronos_vendor/`,
sourced from https://github.com/shiyu-coder/Kronos) behind the same
Forecaster interface as the statistical baseline.

Uses Kronos-mini (4.1M params, 2048-token context) deliberately — it's the
only checkpoint realistic to run on CPU with no GPU. Kronos-small/base are
5-25x larger and were built with GPU inference in mind; loading them here
would work but predicting could take a very long time per call.

Weights download from Hugging Face Hub the first time this is used
(NeoQuasar/Kronos-mini + NeoQuasar/Kronos-Tokenizer-2k) and are cached by
huggingface_hub in the usual `~/.cache/huggingface` location after that.
"""

from typing import List, Optional

import pandas as pd

from .base import ForecastResult

_MODEL_REPO = "NeoQuasar/Kronos-mini"
_TOKENIZER_REPO = "NeoQuasar/Kronos-Tokenizer-2k"
_MAX_CONTEXT = 2048

_predictor = None
_load_error: Optional[str] = None


def _get_predictor():
    global _predictor, _load_error
    if _predictor is not None or _load_error is not None:
        return _predictor

    try:
        from .kronos_vendor import Kronos, KronosPredictor, KronosTokenizer

        tokenizer = KronosTokenizer.from_pretrained(_TOKENIZER_REPO)
        model = Kronos.from_pretrained(_MODEL_REPO)
        _predictor = KronosPredictor(model, tokenizer, device="cpu", max_context=_MAX_CONTEXT)
    except Exception as e:  # noqa: BLE001 - deliberately broad: this is an optional, best-effort path
        _load_error = f"{type(e).__name__}: {e}"
    return _predictor


def available() -> bool:
    """Cheap check the UI can call before offering Kronos as an option,
    without eating the cost of a failed load on every call."""
    return _get_predictor() is not None


def load_error() -> Optional[str]:
    _get_predictor()
    return _load_error


def forecast(
    ohlcv: pd.DataFrame,
    timestamps: pd.Series,
    horizon: int = 10,
    sample_count: int = 1,
) -> ForecastResult:
    """`ohlcv` needs columns open/high/low/close (volume optional), indexed
    to align with `timestamps`. Uses up to the last `_MAX_CONTEXT` rows as
    the lookback window."""
    predictor = _get_predictor()
    if predictor is None:
        raise RuntimeError(f"Kronos model unavailable: {_load_error}")

    lookback_df = ohlcv.tail(_MAX_CONTEXT).reset_index(drop=True)
    x_timestamp = pd.to_datetime(timestamps.tail(_MAX_CONTEXT)).reset_index(drop=True)

    last_ts = x_timestamp.iloc[-1]
    freq = pd.infer_freq(x_timestamp) or (x_timestamp.iloc[-1] - x_timestamp.iloc[-2])
    y_timestamp = pd.Series(pd.date_range(start=last_ts, periods=horizon + 1, freq=freq)[1:])

    pred_df = predictor.predict(
        df=lookback_df,
        x_timestamp=x_timestamp,
        y_timestamp=y_timestamp,
        pred_len=horizon,
        T=1.0,
        top_p=0.9,
        sample_count=sample_count,
        verbose=False,
    )

    closes = pred_df["close"].tolist()
    # Kronos gives point predictions, not a native uncertainty band; report a
    # zero-width band rather than inventing a fake confidence interval like
    # the baseline forecaster's — the honest answer is "we don't have one."
    return ForecastResult(
        horizon=horizon,
        point_forecast=closes,
        lower_band=closes,
        upper_band=closes,
        method=f"kronos:{_MODEL_REPO}",
        note=(
            "Kronos-mini, CPU inference. No native confidence interval — "
            "lower/upper bands equal the point forecast; treat as a single "
            "sampled path, not a calibrated range. Run multiple times with "
            "sample_count>1 and compare paths for a rough sense of spread."
        ),
    )
