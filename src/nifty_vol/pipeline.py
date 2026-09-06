"""Offline application pipeline from normalized quotes to snapshot values."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .collector import OptionRecord

try:
    _KOLKATA = ZoneInfo("Asia/Kolkata")
except ZoneInfoNotFoundError:  # pragma: no cover - tzdata is a runtime dependency
    _KOLKATA = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Rates, data-quality filters, and numerical solver controls."""

    risk_free_rate: float
    dividend_yield: float
    minimum_premium: float = 0.05
    maximum_strike_distance: float = 0.20
    price_tolerance: float = 1e-6
    volatility_tolerance: float = 1e-8
    minimum_volatility: float = 1e-6
    maximum_volatility: float = 5.0
    maximum_iterations: int = 200

    def __post_init__(self) -> None:
        finite_values = (
            self.risk_free_rate,
            self.dividend_yield,
            self.minimum_premium,
            self.maximum_strike_distance,
            self.price_tolerance,
            self.volatility_tolerance,
            self.minimum_volatility,
            self.maximum_volatility,
        )
        if not all(math.isfinite(value) for value in finite_values):
            raise ValueError("pipeline numeric configuration must be finite")
        if self.minimum_premium < 0:
            raise ValueError("minimum_premium cannot be negative")
        if self.maximum_strike_distance < 0:
            raise ValueError("maximum_strike_distance cannot be negative")
        if self.price_tolerance <= 0 or self.volatility_tolerance <= 0:
            raise ValueError("solver tolerances must be positive")
        if self.minimum_volatility <= 0:
            raise ValueError("minimum_volatility must be positive")
        if self.maximum_volatility <= self.minimum_volatility:
            raise ValueError("maximum_volatility must exceed minimum_volatility")
        if (
            not isinstance(self.maximum_iterations, int)
            or isinstance(self.maximum_iterations, bool)
            or self.maximum_iterations <= 0
        ):
            raise ValueError("maximum_iterations must be a positive integer")

    def thresholds(self) -> dict[str, float | int]:
        return {
            "minimum_premium": self.minimum_premium,
            "maximum_strike_distance": self.maximum_strike_distance,
            "price_tolerance": self.price_tolerance,
            "volatility_tolerance": self.volatility_tolerance,
            "minimum_volatility": self.minimum_volatility,
            "maximum_volatility": self.maximum_volatility,
            "maximum_iterations": self.maximum_iterations,
        }


@dataclass(frozen=True, slots=True)
class SelectedPrice:
    value: float | None
    source: str | None


def storage_expiry_date(expiry: datetime) -> date:
    """Return the exchange-local expiry date from an aware expiry instant."""

    if not isinstance(expiry, datetime):
        raise TypeError("expiry must be a datetime")
    if expiry.tzinfo is None or expiry.utcoffset() is None:
        raise ValueError("expiry must be timezone-aware")
    return expiry.astimezone(_KOLKATA).date()


def select_market_price(record: OptionRecord) -> SelectedPrice:
    """Prefer a valid two-sided midpoint and fall back to a valid LTP."""

    bid = record.bid
    ask = record.ask
    valid_midpoint = (
        bid is not None
        and ask is not None
        and math.isfinite(bid)
        and math.isfinite(ask)
        and bid > 0
        and ask >= bid
    )
    if valid_midpoint:
        return SelectedPrice((bid + ask) / 2.0, "midpoint")
    ltp = record.last_price
    if ltp is not None and math.isfinite(ltp) and ltp >= 0:
        return SelectedPrice(ltp, "last_traded_price")
    return SelectedPrice(None, None)
