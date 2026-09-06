import math

import pytest

from nifty_vol.domain import (
    SmileQuote,
    Valuation,
    fit_smile,
    log_moneyness,
    score_expiry,
)

# A plausible NIFTY monthly smile: ~14% ATM vol, negative skew, mild convexity,
# quoted across a realistic ladder of ~25 strikes inside +/-10% of the forward.
_C0, _C1, _C2 = 0.14, -0.35, 1.8
_STRIKE_GRID = list(range(21000, 24601, 150))
_FORWARD = 22800.0


def _true_iv(k: float) -> float:
    return _C0 + _C1 * k + _C2 * k * k


def _chain(overrides: dict[int, float] | None = None) -> list[SmileQuote]:
    overrides = overrides or {}
    quotes: list[SmileQuote] = []
    for strike in _STRIKE_GRID:
        k = log_moneyness(strike=strike, forward=_FORWARD)
        iv = overrides.get(strike, _true_iv(k))
        quotes.append(
            SmileQuote(key=strike, log_moneyness=k, implied_volatility=iv, vega=12.0)
        )
    return quotes


def test_contracts_on_the_curve_are_all_fair() -> None:
    results = score_expiry(_chain())

    assert {r.valuation for r in results} == {Valuation.FAIR}
    for result in results:
        assert result.fitted_iv == pytest.approx(
            _true_iv(log_moneyness(strike=result.key, forward=_FORWARD)), abs=1e-6
        )
        assert result.iv_residual == pytest.approx(0.0, abs=1e-6)


def test_a_strike_above_the_smile_is_expensive_and_the_rest_stay_fair() -> None:
    rich_strike = 22650
    on_curve = _true_iv(log_moneyness(strike=rich_strike, forward=_FORWARD))
    results = {r.key: r for r in score_expiry(_chain({rich_strike: on_curve + 0.03}))}

    assert results[rich_strike].valuation is Valuation.EXPENSIVE
    assert results[rich_strike].iv_residual == pytest.approx(0.03, abs=5e-3)
    assert results[rich_strike].richness_price == pytest.approx(
        12.0 * results[rich_strike].iv_residual / 0.01, rel=1e-6
    )
    others = [v for k, v in results.items() if k != rich_strike]
    assert all(r.valuation is Valuation.FAIR for r in others)


def test_a_strike_below_the_smile_is_cheap_with_negative_richness_price() -> None:
    cheap_strike = 23100
    on_curve = _true_iv(log_moneyness(strike=cheap_strike, forward=_FORWARD))
    results = {r.key: r for r in score_expiry(_chain({cheap_strike: on_curve - 0.03}))}

    assert results[cheap_strike].valuation is Valuation.CHEAP
    assert results[cheap_strike].richness_price < 0.0


def test_gross_outliers_at_the_wings_do_not_drag_the_curve() -> None:
    outliers = {21000: 0.45, 24600: 0.02, 22800: 0.30}
    results = {r.key: r for r in score_expiry(_chain(outliers))}

    clean = [v for k, v in results.items() if k not in outliers]
    assert all(r.valuation is Valuation.FAIR for r in clean)
    assert results[21000].valuation is Valuation.EXPENSIVE
    assert results[24600].valuation is Valuation.CHEAP
    assert results[22800].valuation is Valuation.EXPENSIVE
    assert all(abs(r.richness_z) <= 50.0 for r in results.values())


def test_a_tight_chain_does_not_flag_sub_basis_point_noise() -> None:
    quotes = _chain()
    # nudge one strike by 0.0005 vol — inside the absolute-residual gate
    quotes[10] = SmileQuote(
        key=quotes[10].key,
        log_moneyness=quotes[10].log_moneyness,
        implied_volatility=quotes[10].implied_volatility + 5e-4,
        vega=12.0,
    )
    scored = {r.key: r for r in score_expiry(quotes)}

    assert scored[quotes[10].key].valuation is Valuation.FAIR


def test_too_few_contracts_are_unscored() -> None:
    quotes = _chain()[:4]
    results = score_expiry(quotes)

    assert {r.valuation for r in results} == {Valuation.UNSCORED}
    assert all(r.fitted_iv is None and r.richness_z is None for r in results)
    assert fit_smile(quotes) is None


def test_a_single_strike_cannot_identify_a_smile() -> None:
    k = log_moneyness(strike=22800, forward=_FORWARD)
    quotes = [
        SmileQuote(key=i, log_moneyness=k, implied_volatility=0.14, vega=10.0)
        for i in range(8)
    ]

    assert fit_smile(quotes) is None
    assert {r.valuation for r in score_expiry(quotes)} == {Valuation.UNSCORED}


def test_missing_vega_still_scores_the_valuation() -> None:
    quotes = _chain()
    quotes[12] = SmileQuote(
        key=quotes[12].key,
        log_moneyness=quotes[12].log_moneyness,
        implied_volatility=quotes[12].implied_volatility + 0.05,
        vega=math.nan,
    )
    result = {r.key: r for r in score_expiry(quotes)}[quotes[12].key]

    assert result.valuation is Valuation.EXPENSIVE
    assert result.iv_residual is not None
    assert result.richness_price is None


def test_log_moneyness_rejects_non_positive_inputs() -> None:
    with pytest.raises(ValueError):
        log_moneyness(strike=0.0, forward=100.0)
    with pytest.raises(ValueError):
        log_moneyness(strike=100.0, forward=-1.0)


def test_reusing_a_fit_matches_scoring_from_scratch() -> None:
    anchor = log_moneyness(strike=22650, forward=_FORWARD)
    quotes = _chain({22650: _true_iv(anchor) + 0.02})
    smile = fit_smile(quotes)
    assert smile is not None

    reused = score_expiry(quotes, fit=smile)
    fresh = score_expiry(quotes)
    assert [r.valuation for r in reused] == [r.valuation for r in fresh]
    for a, b in zip(reused, fresh):
        assert a.richness_z == pytest.approx(b.richness_z)
