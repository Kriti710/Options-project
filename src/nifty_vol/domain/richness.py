"""Relative-value scoring: how rich or cheap each option is versus its expiry smile.

The reference for "fair" is the option chain itself. For one expiry we fit a
smooth volatility smile across strikes and compare every contract's implied
volatility to that curve. A contract trading well above the fitted smile is
expensive; well below, cheap; close to it, fairly priced.

The fit is a weighted quadratic in log-moneyness solved with plain normal
equations, refined by a few iteratively reweighted least-squares passes with a
Tukey biweight so a handful of stale or crossed strikes cannot drag the curve.
No third-party numerics: the model space is small and the domain layer stays
dependency-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

#: Fewer priced contracts than this in an expiry and no reference smile is fitted.
MIN_QUOTES_FOR_FIT = 6

#: Residual z-score inside this band counts as fairly priced.
FAIR_Z = 1.0

#: A contract must also sit at least this far (in decimal vol) off the fitted
#: smile before it is called rich or cheap. Stops a very tight chain from
#: labelling sub-basis-point noise.
MIN_ABS_RESIDUAL = 1.5e-3

#: Reported z-scores are clamped to this magnitude. Past it the verdict is
#: unambiguous and the exact number is not informative.
MAX_ABS_Z = 50.0

#: Vol-point floor on the robust residual scale. When the chain fits the smile
#: this tightly, z-scores stop being meaningful and the absolute-residual gate
#: above carries the classification.
MIN_RESIDUAL_SCALE = 1e-4

#: Domain Greeks express vega per one volatility point (0.01), so a residual in
#: decimal vol converts to price units by dividing by this.
_VEGA_VOL_STEP = 0.01

_BIWEIGHT_C = 4.685
_IRLS_ITERATIONS = 5
_MIN_MONEYNESS_SPREAD = 1e-6


class Valuation(StrEnum):
    """The rich/cheap verdict for a single contract."""

    CHEAP = "cheap"
    FAIR = "fair"
    EXPENSIVE = "expensive"
    UNSCORED = "unscored"


@dataclass(frozen=True, slots=True)
class SmileQuote:
    """One priced contract's inputs to the relative-value fit.

    ``key`` is opaque to this module and echoed back on the result so callers
    can rejoin scores to their own rows.
    """

    key: object
    log_moneyness: float
    implied_volatility: float
    vega: float
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class SmileFit:
    """Weighted quadratic reference smile ``iv ~= c0 + c1*k + c2*k**2``."""

    coefficients: tuple[float, float, float]
    sample_size: int
    residual_scale: float

    def evaluate(self, log_moneyness: float) -> float:
        """Fitted implied volatility at a log-moneyness point."""

        c0, c1, c2 = self.coefficients
        return c0 + c1 * log_moneyness + c2 * log_moneyness * log_moneyness


@dataclass(frozen=True, slots=True)
class RichnessResult:
    """Per-contract relative-value score.

    ``iv_residual`` is market IV minus fitted IV in decimal vol. ``richness_price``
    is that residual re-expressed in option price units via the contract's vega:
    positive means the market is paying above fair, negative means below.
    """

    key: object
    valuation: Valuation
    fitted_iv: float | None
    iv_residual: float | None
    richness_price: float | None
    richness_z: float | None


def log_moneyness(*, strike: float, forward: float) -> float:
    """Return ``ln(strike / forward)``; the smile's x-axis."""

    if not (math.isfinite(strike) and math.isfinite(forward)):
        raise ValueError("strike and forward must be finite")
    if strike <= 0.0 or forward <= 0.0:
        raise ValueError("strike and forward must be positive")
    return math.log(strike / forward)


def _solve_symmetric_3x3(
    matrix: list[list[float]], rhs: list[float]
) -> tuple[float, float, float] | None:
    """Gaussian elimination with partial pivoting for a 3x3 system."""

    augmented = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for column in range(3):
        pivot_row = max(range(column, 3), key=lambda r: abs(augmented[r][column]))
        if abs(augmented[pivot_row][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot_row] = (
            augmented[pivot_row],
            augmented[column],
        )
        pivot = augmented[column][column]
        for row in range(3):
            if row == column:
                continue
            factor = augmented[row][column] / pivot
            for col in range(column, 4):
                augmented[row][col] -= factor * augmented[column][col]
    return tuple(augmented[i][3] / augmented[i][i] for i in range(3))  # type: ignore[return-value]


def _weighted_quadratic(
    ks: list[float], ivs: list[float], weights: list[float]
) -> tuple[float, float, float] | None:
    """Solve ``(X^T W X) c = X^T W y`` for the basis ``[1, k, k**2]``."""

    normal = [[0.0] * 3 for _ in range(3)]
    moment = [0.0, 0.0, 0.0]
    for k, iv, w in zip(ks, ivs, weights):
        basis = (1.0, k, k * k)
        for i in range(3):
            moment[i] += w * basis[i] * iv
            for j in range(3):
                normal[i][j] += w * basis[i] * basis[j]
    return _solve_symmetric_3x3(normal, moment)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2 == 1:
        return ordered[middle]
    return 0.5 * (ordered[middle - 1] + ordered[middle])


def _robust_scale(residuals: list[float]) -> float:
    """MAD-based robust standard deviation of the residuals."""

    centre = _median(residuals)
    absolute_deviation = [abs(r - centre) for r in residuals]
    return 1.4826 * _median(absolute_deviation)


def fit_smile(quotes: list[SmileQuote]) -> SmileFit | None:
    """Fit a robust weighted quadratic smile, or ``None`` if it is not supported.

    Returns ``None`` when there are too few contracts or their strikes do not
    span enough log-moneyness to identify a curve.
    """

    usable = [
        q
        for q in quotes
        if math.isfinite(q.log_moneyness)
        and math.isfinite(q.implied_volatility)
        and q.implied_volatility > 0.0
        and math.isfinite(q.weight)
        and q.weight > 0.0
    ]
    if len(usable) < MIN_QUOTES_FOR_FIT:
        return None

    ks = [q.log_moneyness for q in usable]
    if max(ks) - min(ks) < _MIN_MONEYNESS_SPREAD:
        return None
    ivs = [q.implied_volatility for q in usable]
    weights = [q.weight for q in usable]

    coefficients = _weighted_quadratic(ks, ivs, weights)
    if coefficients is None:
        return None

    residuals = [0.0] * len(usable)
    for _ in range(_IRLS_ITERATIONS):
        c0, c1, c2 = coefficients
        residuals = [iv - (c0 + c1 * k + c2 * k * k) for k, iv in zip(ks, ivs)]
        scale = _robust_scale(residuals)
        if scale <= MIN_RESIDUAL_SCALE:
            break
        reweighted: list[float] = []
        for base_weight, residual in zip(weights, residuals):
            unit = residual / (_BIWEIGHT_C * scale)
            biweight = (1.0 - unit * unit) ** 2 if abs(unit) < 1.0 else 0.0
            reweighted.append(base_weight * biweight)
        if sum(1 for w in reweighted if w > 0.0) < MIN_QUOTES_FOR_FIT:
            break
        refined = _weighted_quadratic(ks, ivs, reweighted)
        if refined is None:
            break
        coefficients = refined

    c0, c1, c2 = coefficients
    residuals = [iv - (c0 + c1 * k + c2 * k * k) for k, iv in zip(ks, ivs)]
    residual_scale = max(_robust_scale(residuals), MIN_RESIDUAL_SCALE)
    return SmileFit(
        coefficients=coefficients,
        sample_size=len(usable),
        residual_scale=residual_scale,
    )


def _classify(z_score: float, iv_residual: float) -> Valuation:
    if abs(iv_residual) < MIN_ABS_RESIDUAL:
        return Valuation.FAIR
    if z_score >= FAIR_Z:
        return Valuation.EXPENSIVE
    if z_score <= -FAIR_Z:
        return Valuation.CHEAP
    return Valuation.FAIR


def score_expiry(
    quotes: list[SmileQuote], *, fit: SmileFit | None = None
) -> list[RichnessResult]:
    """Score every contract in one expiry against its reference smile.

    When no smile can be fitted every contract comes back ``UNSCORED`` with null
    metrics. Pass a precomputed ``fit`` to reuse one across calls.
    """

    smile = fit if fit is not None else fit_smile(quotes)
    if smile is None:
        return [
            RichnessResult(q.key, Valuation.UNSCORED, None, None, None, None)
            for q in quotes
        ]

    results: list[RichnessResult] = []
    for quote in quotes:
        if not (
            math.isfinite(quote.log_moneyness)
            and math.isfinite(quote.implied_volatility)
            and quote.implied_volatility > 0.0
        ):
            results.append(
                RichnessResult(quote.key, Valuation.UNSCORED, None, None, None, None)
            )
            continue
        fitted_iv = smile.evaluate(quote.log_moneyness)
        iv_residual = quote.implied_volatility - fitted_iv
        z_score = max(
            -MAX_ABS_Z, min(MAX_ABS_Z, iv_residual / smile.residual_scale)
        )
        richness_price = (
            quote.vega * iv_residual / _VEGA_VOL_STEP
            if math.isfinite(quote.vega)
            else None
        )
        results.append(
            RichnessResult(
                key=quote.key,
                valuation=_classify(z_score, iv_residual),
                fitted_iv=fitted_iv,
                iv_residual=iv_residual,
                richness_price=richness_price,
                richness_z=z_score,
            )
        )
    return results
