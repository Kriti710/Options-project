"""Offline application pipeline from normalized quotes to snapshot values."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .collector import OptionRecord
from .domain import CalculationStatus, calculate_option, time_to_expiry
from .storage import CollectionRun, ContractIdentity, OptionObservation

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


def _excluded_observation(
    record: OptionRecord,
    expiry: date,
    selected: SelectedPrice,
    status: CalculationStatus,
    reason: str,
    *,
    T: float | None = None,
) -> OptionObservation:
    return OptionObservation(
        identity=ContractIdentity(expiry, record.strike, record.option_type),
        last_traded_price=record.last_price,
        bid=record.bid,
        ask=record.ask,
        volume=record.volume,
        open_interest=record.open_interest,
        selected_price=selected.value,
        price_source=selected.source,
        calculation_status=status.value,
        exclusion_reason=reason,
        time_to_expiry=T,
    )


def calculate_record(
    record: OptionRecord, config: PipelineConfig
) -> OptionObservation:
    """Filter and calculate one quote while preserving its explicit outcome."""

    expiry = storage_expiry_date(record.expiry)
    selected = select_market_price(record)
    identity = ContractIdentity(expiry, record.strike, record.option_type)
    common = {
        "identity": identity,
        "last_traded_price": record.last_price,
        "bid": record.bid,
        "ask": record.ask,
        "volume": record.volume,
        "open_interest": record.open_interest,
        "selected_price": selected.value,
        "price_source": selected.source,
    }

    if record.volume == 0:
        return _excluded_observation(
            record,
            expiry,
            selected,
            CalculationStatus.EXCLUDED_ZERO_VOLUME,
            "volume is zero",
        )
    if selected.value is None:
        return _excluded_observation(
            record,
            expiry,
            selected,
            CalculationStatus.INVALID_MARKET_DATA,
            "neither a valid midpoint nor last traded price is available",
        )
    if selected.value < config.minimum_premium:
        return _excluded_observation(
            record,
            expiry,
            selected,
            CalculationStatus.EXCLUDED_LOW_PREMIUM,
            f"selected price is below {config.minimum_premium}",
        )
    if (
        not math.isfinite(record.underlying_spot)
        or record.underlying_spot <= 0
        or not math.isfinite(record.strike)
        or record.strike <= 0
    ):
        return _excluded_observation(
            record,
            expiry,
            selected,
            CalculationStatus.INVALID_MARKET_DATA,
            "spot and strike must be finite and positive",
        )
    distance = abs(record.strike - record.underlying_spot) / record.underlying_spot
    if distance > config.maximum_strike_distance:
        return _excluded_observation(
            record,
            expiry,
            selected,
            CalculationStatus.EXCLUDED_OUTSIDE_STRIKE_RANGE,
            f"strike distance {distance} exceeds {config.maximum_strike_distance}",
        )

    T = time_to_expiry(expiry_date=expiry, as_of=record.observed_at)
    result = calculate_option(
        S=record.underlying_spot,
        K=record.strike,
        T=T,
        r=config.risk_free_rate,
        q=config.dividend_yield,
        market_price=selected.value,
        option_type=record.option_type,
        price_tolerance=config.price_tolerance,
        volatility_tolerance=config.volatility_tolerance,
        min_volatility=config.minimum_volatility,
        max_volatility=config.maximum_volatility,
        max_iterations=config.maximum_iterations,
    )
    if not result.succeeded:
        return OptionObservation(
            **common,
            calculation_status=result.status.value,
            exclusion_reason=(result.failure.value if result.failure else "unknown"),
            time_to_expiry=T,
        )
    assert result.greeks is not None
    return OptionObservation(
        **common,
        calculation_status=result.status.value,
        implied_volatility=result.implied_volatility,
        delta=result.greeks.delta,
        gamma=result.greeks.gamma,
        vega=result.greeks.vega,
        theta=result.greeks.theta,
        time_to_expiry=T,
    )


def build_collection_run(
    records: Iterable[OptionRecord], config: PipelineConfig
) -> CollectionRun:
    """Build one immutable storage snapshot from a normalized option chain."""

    items = tuple(records)
    if not items:
        raise ValueError("at least one option record is required")
    symbols = {item.symbol for item in items}
    spots = {item.underlying_spot for item in items}
    if len(symbols) != 1 or len(spots) != 1:
        raise ValueError("one snapshot must contain one symbol and one spot value")
    collected_at = max(item.observed_at.astimezone(UTC) for item in items)
    observations = tuple(calculate_record(item, config) for item in items)
    return CollectionRun(
        collected_at=collected_at,
        spot=items[0].underlying_spot,
        risk_free_rate=config.risk_free_rate,
        dividend_yield=config.dividend_yield,
        model_name="black_scholes_merton",
        assumptions={
            "day_count": "ACT/365F",
            "expiry_time": "15:30:00",
            "expiry_timezone": "Asia/Kolkata",
            "rate_unit": "decimal",
            "volatility_unit": "decimal",
        },
        thresholds=config.thresholds(),
        observations=observations,
    )
