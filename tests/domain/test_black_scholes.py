import math

import pytest

from nifty_vol.domain import (
    CalculationFailure,
    CalculationStatus,
    OptionType,
    black_scholes_greeks,
    black_scholes_price,
    calculate_option,
    implied_volatility,
    no_arbitrage_bounds,
    validate_market_price,
)


def test_golden_prices_and_greeks_with_dividend_yield() -> None:
    inputs = dict(S=100.0, K=100.0, T=1.0, r=0.05, q=0.02, sigma=0.20)

    call = black_scholes_price(**inputs, option_type=OptionType.CALL)
    put = black_scholes_price(**inputs, option_type=OptionType.PUT)
    call_greeks = black_scholes_greeks(**inputs, option_type="call")
    put_greeks = black_scholes_greeks(**inputs, option_type="put")

    assert call == pytest.approx(9.2270055082, abs=1e-9)
    assert put == pytest.approx(6.3300806275, abs=1e-9)
    assert call_greeks.delta == pytest.approx(0.5868511461, abs=1e-9)
    assert put_greeks.delta == pytest.approx(-0.3933475272, abs=1e-9)
    assert call_greeks.gamma == pytest.approx(0.0189505788, abs=1e-9)
    assert call_greeks.vega == pytest.approx(0.3790115751, abs=1e-9)
    assert call_greeks.theta == pytest.approx(-5.089318913998 / 365, abs=1e-9)
    assert put_greeks.theta == pytest.approx(-2.2935691381 / 365, abs=1e-9)


@pytest.mark.parametrize("sigma", [0.05, 0.2, 0.8])
def test_put_call_parity(sigma: float) -> None:
    inputs = dict(S=22450.0, K=22500.0, T=23 / 365, r=0.065, q=0.012, sigma=sigma)
    call = black_scholes_price(**inputs, option_type="call")
    put = black_scholes_price(**inputs, option_type="put")

    expected = (
        inputs["S"] * math.exp(-inputs["q"] * inputs["T"])
        - inputs["K"] * math.exp(-inputs["r"] * inputs["T"])
    )
    assert call - put == pytest.approx(expected, abs=1e-10)


@pytest.mark.parametrize("kind", ["call", "put"])
@pytest.mark.parametrize("sigma", [0.03, 0.18, 1.25, 4.0])
def test_implied_volatility_round_trip(kind: str, sigma: float) -> None:
    inputs = dict(S=22125.5, K=22500.0, T=37.25 / 365, r=0.064, q=0.011)
    market_price = black_scholes_price(**inputs, sigma=sigma, option_type=kind)

    result = implied_volatility(
        **inputs, market_price=market_price, option_type=kind, price_tolerance=1e-9
    )

    assert result.status is CalculationStatus.CALCULATED
    assert result.failure is None
    assert result.volatility == pytest.approx(sigma, abs=1e-9)
    assert abs(result.price_error or 0.0) <= 1e-9


def test_full_calculation_exposes_iv_and_greeks() -> None:
    inputs = dict(S=100.0, K=105.0, T=0.5, r=0.04, q=0.01)
    market_price = black_scholes_price(**inputs, sigma=0.3, option_type="put")
    result = calculate_option(**inputs, market_price=market_price, option_type="put")

    assert result.succeeded
    assert result.implied_volatility == pytest.approx(0.3, abs=1e-6)
    assert result.greeks is not None
    assert result.failure is None


@pytest.mark.parametrize(
    ("changes", "failure"),
    [
        ({"S": 0.0}, CalculationFailure.NON_POSITIVE_SPOT),
        ({"K": -1.0}, CalculationFailure.NON_POSITIVE_STRIKE),
        ({"T": 0.0}, CalculationFailure.NON_POSITIVE_TIME),
        ({"market_price": -0.01}, CalculationFailure.NEGATIVE_MARKET_PRICE),
        ({"option_type": "straddle"}, CalculationFailure.INVALID_OPTION_TYPE),
    ],
)
def test_invalid_inputs_return_explicit_failure(
    changes: dict[str, object], failure: CalculationFailure
) -> None:
    inputs: dict[str, object] = dict(
        S=100.0, K=100.0, T=1.0, r=0.05, q=0.0, market_price=10.0, option_type="call"
    )
    inputs.update(changes)

    result = implied_volatility(**inputs)  # type: ignore[arg-type]

    assert result.status is CalculationStatus.INVALID_MODEL_INPUT
    assert result.failure is failure
    assert result.volatility is None


def test_non_finite_market_price_is_invalid_market_data() -> None:
    result = implied_volatility(
        S=100,
        K=100,
        T=1,
        r=0.05,
        q=0,
        market_price=math.nan,
        option_type="call",
    )

    assert result.status is CalculationStatus.INVALID_MARKET_DATA
    assert result.failure is CalculationFailure.NON_FINITE_INPUT


def test_non_finite_model_input_is_invalid_model_input() -> None:
    result = implied_volatility(
        S=100,
        K=100,
        T=1,
        r=math.nan,
        q=0,
        market_price=10,
        option_type="call",
    )

    assert result.status is CalculationStatus.INVALID_MODEL_INPUT
    assert result.failure is CalculationFailure.NON_FINITE_INPUT


def test_direct_pricing_rejects_invalid_sigma() -> None:
    with pytest.raises(ValueError, match="sigma must be positive"):
        black_scholes_price(S=100, K=100, T=1, r=0, q=0, sigma=0, option_type="call")


@pytest.mark.parametrize("kind", ["call", "put"])
def test_no_arbitrage_bounds_are_inclusive(kind: str) -> None:
    S, K = (100.0, 90.0) if kind == "call" else (90.0, 100.0)
    inputs = dict(S=S, K=K, T=0.75, r=0.06, q=0.02, option_type=kind)
    bounds = no_arbitrage_bounds(**inputs)

    assert validate_market_price(**inputs, market_price=bounds.lower).valid
    assert validate_market_price(**inputs, market_price=bounds.upper).valid

    below = validate_market_price(**inputs, market_price=bounds.lower - 1e-4)
    above = validate_market_price(**inputs, market_price=bounds.upper + 1e-4)
    assert below.failure is CalculationFailure.PRICE_BELOW_ARBITRAGE_BOUND
    assert above.failure is CalculationFailure.PRICE_ABOVE_ARBITRAGE_BOUND


def test_price_inside_arbitrage_bounds_but_outside_volatility_bracket_fails() -> None:
    inputs = dict(S=100.0, K=100.0, T=1.0, r=0.0, q=0.0, option_type="call")
    result = implied_volatility(
        **inputs, market_price=40.0, min_volatility=0.01, max_volatility=0.10
    )

    assert result.status is CalculationStatus.SOLVER_DID_NOT_CONVERGE
    assert result.failure is CalculationFailure.VOLATILITY_NOT_BRACKETED


def test_iteration_limit_is_reported() -> None:
    inputs = dict(S=100.0, K=100.0, T=1.0, r=0.0, q=0.0, option_type="call")
    market_price = black_scholes_price(**inputs, sigma=0.2)
    result = implied_volatility(
        **inputs, market_price=market_price, price_tolerance=1e-15, max_iterations=1
    )

    assert result.status is CalculationStatus.SOLVER_DID_NOT_CONVERGE
    assert result.failure is CalculationFailure.ITERATION_LIMIT_REACHED
