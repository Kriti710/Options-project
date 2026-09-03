"""Validated implied-volatility inversion and explicit calculation results."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from .black_scholes import (
    Greeks,
    OptionType,
    PriceBounds,
    black_scholes_greeks,
    black_scholes_price,
    no_arbitrage_bounds,
)


class CalculationStatus(StrEnum):
    """Frozen calculation statuses shared with other application components."""

    CALCULATED = "calculated"
    EXCLUDED_ZERO_VOLUME = "excluded_zero_volume"
    EXCLUDED_LOW_PREMIUM = "excluded_low_premium"
    EXCLUDED_OUTSIDE_STRIKE_RANGE = "excluded_outside_strike_range"
    INVALID_MARKET_DATA = "invalid_market_data"
    INVALID_MODEL_INPUT = "invalid_model_input"
    SOLVER_DID_NOT_CONVERGE = "solver_did_not_converge"


class CalculationFailure(StrEnum):
    """Machine-readable reasons for anticipated model and solver failures."""

    NON_FINITE_INPUT = "non_finite_input"
    NON_POSITIVE_SPOT = "non_positive_spot"
    NON_POSITIVE_STRIKE = "non_positive_strike"
    NON_POSITIVE_TIME = "non_positive_time"
    INVALID_OPTION_TYPE = "invalid_option_type"
    NEGATIVE_MARKET_PRICE = "negative_market_price"
    PRICE_BELOW_ARBITRAGE_BOUND = "price_below_arbitrage_bound"
    PRICE_ABOVE_ARBITRAGE_BOUND = "price_above_arbitrage_bound"
    INVALID_SOLVER_CONFIGURATION = "invalid_solver_configuration"
    VOLATILITY_NOT_BRACKETED = "volatility_not_bracketed"
    ITERATION_LIMIT_REACHED = "iteration_limit_reached"


@dataclass(frozen=True, slots=True)
class MarketPriceValidation:
    valid: bool
    bounds: PriceBounds | None
    failure: CalculationFailure | None


@dataclass(frozen=True, slots=True)
class ImpliedVolatilityResult:
    status: CalculationStatus
    volatility: float | None
    failure: CalculationFailure | None
    iterations: int
    price_error: float | None

    @property
    def succeeded(self) -> bool:
        return self.status is CalculationStatus.CALCULATED


@dataclass(frozen=True, slots=True)
class CalculationResult:
    status: CalculationStatus
    implied_volatility: float | None
    greeks: Greeks | None
    failure: CalculationFailure | None
    iterations: int
    price_error: float | None

    @property
    def succeeded(self) -> bool:
        return self.status is CalculationStatus.CALCULATED


def _input_failure(
    *,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    market_price: float,
    option_type: OptionType | str,
) -> CalculationFailure | None:
    numeric = (S, K, T, r, q, market_price)
    if any(
        not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in numeric
    ):
        return CalculationFailure.NON_FINITE_INPUT
    if S <= 0.0:
        return CalculationFailure.NON_POSITIVE_SPOT
    if K <= 0.0:
        return CalculationFailure.NON_POSITIVE_STRIKE
    if T <= 0.0:
        return CalculationFailure.NON_POSITIVE_TIME
    try:
        OptionType(option_type)
    except (TypeError, ValueError):
        return CalculationFailure.INVALID_OPTION_TYPE
    if market_price < 0.0:
        return CalculationFailure.NEGATIVE_MARKET_PRICE
    return None


def validate_market_price(
    *,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    market_price: float,
    option_type: OptionType | str,
    tolerance: float = 0.0,
) -> MarketPriceValidation:
    """Validate model inputs and inclusive no-arbitrage price bounds."""

    failure = _input_failure(
        S=S, K=K, T=T, r=r, q=q, market_price=market_price, option_type=option_type
    )
    if failure is not None:
        return MarketPriceValidation(valid=False, bounds=None, failure=failure)
    if (
        not isinstance(tolerance, (int, float))
        or not math.isfinite(tolerance)
        or tolerance < 0.0
    ):
        return MarketPriceValidation(
            valid=False,
            bounds=None,
            failure=CalculationFailure.INVALID_SOLVER_CONFIGURATION,
        )
    bounds = no_arbitrage_bounds(S=S, K=K, T=T, r=r, q=q, option_type=option_type)
    if market_price < bounds.lower - tolerance:
        failure = CalculationFailure.PRICE_BELOW_ARBITRAGE_BOUND
    elif market_price > bounds.upper + tolerance:
        failure = CalculationFailure.PRICE_ABOVE_ARBITRAGE_BOUND
    else:
        failure = None
    return MarketPriceValidation(valid=failure is None, bounds=bounds, failure=failure)


def implied_volatility(
    *,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    market_price: float,
    option_type: OptionType | str,
    price_tolerance: float = 1e-6,
    min_volatility: float = 1e-8,
    max_volatility: float = 5.0,
    max_iterations: int = 200,
) -> ImpliedVolatilityResult:
    """Invert BSM with bounded bisection and an explicit convergence result."""

    if not isinstance(market_price, (int, float)) or not math.isfinite(market_price):
        return ImpliedVolatilityResult(
            status=CalculationStatus.INVALID_MARKET_DATA,
            volatility=None,
            failure=CalculationFailure.NON_FINITE_INPUT,
            iterations=0,
            price_error=None,
        )
    validation = validate_market_price(
        S=S,
        K=K,
        T=T,
        r=r,
        q=q,
        market_price=market_price,
        option_type=option_type,
        tolerance=price_tolerance,
    )
    if not validation.valid:
        return ImpliedVolatilityResult(
            status=CalculationStatus.INVALID_MODEL_INPUT,
            volatility=None,
            failure=validation.failure,
            iterations=0,
            price_error=None,
        )
    config_values = (price_tolerance, min_volatility, max_volatility)
    config_valid = (
        all(
            isinstance(value, (int, float)) and math.isfinite(value)
            for value in config_values
        )
        and price_tolerance > 0.0
        and min_volatility > 0.0
        and max_volatility > min_volatility
        and isinstance(max_iterations, int)
        and not isinstance(max_iterations, bool)
        and max_iterations > 0
    )
    if not config_valid:
        return ImpliedVolatilityResult(
            status=CalculationStatus.INVALID_MODEL_INPUT,
            volatility=None,
            failure=CalculationFailure.INVALID_SOLVER_CONFIGURATION,
            iterations=0,
            price_error=None,
        )

    def error(volatility: float) -> float:
        price = black_scholes_price(
            S=S, K=K, T=T, r=r, q=q, sigma=volatility, option_type=option_type
        )
        return price - market_price

    low = min_volatility
    high = max_volatility
    low_error = error(low)
    high_error = error(high)
    if abs(low_error) <= price_tolerance:
        return ImpliedVolatilityResult(
            CalculationStatus.CALCULATED, low, None, 0, low_error
        )
    if abs(high_error) <= price_tolerance:
        return ImpliedVolatilityResult(
            CalculationStatus.CALCULATED, high, None, 0, high_error
        )
    if low_error > 0.0 or high_error < 0.0:
        nearest_error = low_error if abs(low_error) < abs(high_error) else high_error
        return ImpliedVolatilityResult(
            CalculationStatus.SOLVER_DID_NOT_CONVERGE,
            None,
            CalculationFailure.VOLATILITY_NOT_BRACKETED,
            0,
            nearest_error,
        )

    middle = low
    middle_error = low_error
    for iteration in range(1, max_iterations + 1):
        middle = low + (high - low) / 2.0
        middle_error = error(middle)
        if abs(middle_error) <= price_tolerance:
            return ImpliedVolatilityResult(
                CalculationStatus.CALCULATED, middle, None, iteration, middle_error
            )
        if middle_error < 0.0:
            low = middle
        else:
            high = middle
    return ImpliedVolatilityResult(
        CalculationStatus.SOLVER_DID_NOT_CONVERGE,
        None,
        CalculationFailure.ITERATION_LIMIT_REACHED,
        max_iterations,
        middle_error,
    )


def calculate_option(
    *,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    market_price: float,
    option_type: OptionType | str,
    price_tolerance: float = 1e-6,
    min_volatility: float = 1e-8,
    max_volatility: float = 5.0,
    max_iterations: int = 200,
) -> CalculationResult:
    """Calculate IV and dependent Greeks, preserving every failure explicitly."""

    iv = implied_volatility(
        S=S,
        K=K,
        T=T,
        r=r,
        q=q,
        market_price=market_price,
        option_type=option_type,
        price_tolerance=price_tolerance,
        min_volatility=min_volatility,
        max_volatility=max_volatility,
        max_iterations=max_iterations,
    )
    if not iv.succeeded:
        return CalculationResult(
            iv.status, None, None, iv.failure, iv.iterations, iv.price_error
        )
    greeks = black_scholes_greeks(
        S=S,
        K=K,
        T=T,
        r=r,
        q=q,
        sigma=iv.volatility,
        option_type=option_type,
    )
    return CalculationResult(
        iv.status, iv.volatility, greeks, None, iv.iterations, iv.price_error
    )
