"""The interface every forecaster implements, so the terminal UI (and
anything else) can swap BaselineForecaster for KronosForecaster — or any
future model — without changing a single call site.
"""

from dataclasses import dataclass
from typing import List, Protocol


@dataclass
class ForecastResult:
    horizon: int                # number of bars ahead
    point_forecast: List[float]  # length == horizon
    lower_band: List[float]      # a rough uncertainty band, same length
    upper_band: List[float]
    method: str
    note: str = ""               # caveats specific to this forecaster/run


class Forecaster(Protocol):
    def forecast(self, closes: List[float], horizon: int = 10) -> ForecastResult: ...
