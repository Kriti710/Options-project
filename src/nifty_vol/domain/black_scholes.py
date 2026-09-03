"""Black-Scholes-Merton prices, bounds, and Greeks."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class OptionType(StrEnum):
    """The explicit option payoff type."""

    CALL = "call"
    PUT = "put"


@dataclass(frozen=True, slots=True)
class PriceBounds:
    """Inclusive no-arbitrage bounds for a European option."""

    lower: float
    upper: float


@dataclass(frozen=True, slots=True)
class Greeks:
    """BSM sensitivities in the units exposed by the application.

    Vega is the price change for one volatility percentage point (0.01), and
    theta is the price change for one calendar day. Gamma is per spot point.
    """

    delta: float
    gamma: float
    vega: float
    theta: float


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _normal_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / math.sqrt(2.0 * math.pi)


def _option_type(value: OptionType | str) -> OptionType:
    try:
        return OptionType(value)
    except (TypeError, ValueError) as error:
        raise ValueError("option_type must be 'call' or 'put'") from error


def _validate_common(*, S: float, K: float, T: float, r: float, q: float) -> None:
    values = {"S": S, "K": K, "T": T, "r": r, "q": q}
    if any(
        not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in values.values()
    ):
        raise ValueError("all model inputs must be finite real numbers")
    if S <= 0.0:
        raise ValueError("S must be positive")
    if K <= 0.0:
        raise ValueError("K must be positive")
    if T <= 0.0:
        raise ValueError("T must be positive")


def _validate_sigma(sigma: float) -> None:
    if not isinstance(sigma, (int, float)) or not math.isfinite(sigma):
        raise ValueError("sigma must be a finite real number")
    if sigma <= 0.0:
        raise ValueError("sigma must be positive")


def _terms(
    *, S: float, K: float, T: float, r: float, q: float, sigma: float
) -> tuple[float, float]:
    root_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (
        sigma * root_t
    )
    return d1, d1 - sigma * root_t


def no_arbitrage_bounds(
    *, S: float, K: float, T: float, r: float, q: float, option_type: OptionType | str
) -> PriceBounds:
    """Return inclusive European-option price bounds under continuous carry."""

    _validate_common(S=S, K=K, T=T, r=r, q=q)
    kind = _option_type(option_type)
    discounted_spot = S * math.exp(-q * T)
    discounted_strike = K * math.exp(-r * T)
    if kind is OptionType.CALL:
        return PriceBounds(
            lower=max(0.0, discounted_spot - discounted_strike),
            upper=discounted_spot,
        )
    return PriceBounds(
        lower=max(0.0, discounted_strike - discounted_spot),
        upper=discounted_strike,
    )


def black_scholes_price(
    *,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    option_type: OptionType | str,
) -> float:
    """Price a European call or put with the Black-Scholes-Merton model."""

    _validate_common(S=S, K=K, T=T, r=r, q=q)
    _validate_sigma(sigma)
    kind = _option_type(option_type)
    d1, d2 = _terms(S=S, K=K, T=T, r=r, q=q, sigma=sigma)
    discounted_spot = S * math.exp(-q * T)
    discounted_strike = K * math.exp(-r * T)
    if kind is OptionType.CALL:
        return discounted_spot * _normal_cdf(d1) - discounted_strike * _normal_cdf(d2)
    return discounted_strike * _normal_cdf(-d2) - discounted_spot * _normal_cdf(-d1)


def black_scholes_greeks(
    *,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    option_type: OptionType | str,
) -> Greeks:
    """Return delta, gamma, vega/vol-point, and theta/calendar-day."""

    _validate_common(S=S, K=K, T=T, r=r, q=q)
    _validate_sigma(sigma)
    kind = _option_type(option_type)
    d1, d2 = _terms(S=S, K=K, T=T, r=r, q=q, sigma=sigma)
    root_t = math.sqrt(T)
    discounted_spot = S * math.exp(-q * T)
    discounted_strike = K * math.exp(-r * T)
    density = _normal_pdf(d1)

    gamma = math.exp(-q * T) * density / (S * sigma * root_t)
    vega = discounted_spot * density * root_t * 0.01
    common_theta = -(discounted_spot * density * sigma) / (2.0 * root_t)
    if kind is OptionType.CALL:
        delta = math.exp(-q * T) * _normal_cdf(d1)
        annual_theta = (
            common_theta
            + q * discounted_spot * _normal_cdf(d1)
            - r * discounted_strike * _normal_cdf(d2)
        )
    else:
        delta = math.exp(-q * T) * (_normal_cdf(d1) - 1.0)
        annual_theta = (
            common_theta
            - q * discounted_spot * _normal_cdf(-d1)
            + r * discounted_strike * _normal_cdf(-d2)
        )
    return Greeks(delta=delta, gamma=gamma, vega=vega, theta=annual_theta / 365.0)
