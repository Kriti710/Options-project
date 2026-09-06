from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nifty_vol.collector import OptionRecord
from nifty_vol.domain import black_scholes_greeks, black_scholes_price, time_to_expiry
from nifty_vol.pipeline import PipelineConfig
from nifty_vol.pricer import MODEL_NAME, forward_price, price_snapshot

_OBSERVED_AT = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
_NEAR_EXPIRY = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
_FAR_EXPIRY = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
_SPOT = 22_800.0
_R, _Q = 0.066, 0.012


@pytest.fixture
def config() -> PipelineConfig:
    return PipelineConfig(
        risk_free_rate=_R,
        dividend_yield=_Q,
        minimum_premium=0.05,
        maximum_strike_distance=0.20,
    )


def _smile_iv(strike: float, forward: float) -> float:
    k = math.log(strike / forward)
    return 0.14 - 0.35 * k + 1.8 * k * k


def _record(
    *, expiry: datetime, strike: float, option_type: str, **changes: object
) -> OptionRecord:
    years = time_to_expiry(expiry_date=expiry.date(), as_of=_OBSERVED_AT)
    forward = forward_price(
        spot=_SPOT, risk_free_rate=_R, dividend_yield=_Q, years=years
    )
    sigma = _smile_iv(strike, forward)
    fair = black_scholes_price(
        S=_SPOT, K=strike, T=years, r=_R, q=_Q, sigma=sigma, option_type=option_type
    )
    fields: dict[str, object] = dict(
        symbol="NIFTY",
        observed_at=_OBSERVED_AT,
        expiry=expiry,
        strike=strike,
        option_type=option_type,
        underlying_spot=_SPOT,
        last_price=fair,
        bid=fair - 0.5,
        ask=fair + 0.5,
        volume=5_000,
        open_interest=20_000,
    )
    fields.update(changes)
    return OptionRecord(**fields)  # type: ignore[arg-type]


def _full_chain() -> list[OptionRecord]:
    records: list[OptionRecord] = []
    for expiry in (_NEAR_EXPIRY, _FAR_EXPIRY):
        for strike in range(21_000, 24_601, 150):
            for option_type in ("call", "put"):
                records.append(
                    _record(expiry=expiry, strike=strike, option_type=option_type)
                )
    return records


def test_prices_every_contract_and_recovers_the_input_smile(
    config: PipelineConfig,
) -> None:
    snapshot_id = uuid4()
    priced = price_snapshot(
        snapshot_id=snapshot_id,
        priced_at=datetime(2026, 9, 4, 10, 1, tzinfo=UTC),
        records=_full_chain(),
        config=config,
    )

    assert priced.run.snapshot_id == snapshot_id
    assert priced.run.model_name == MODEL_NAME
    assert priced.run.risk_free_rate == _R
    assert len(priced.analytics) == len(_full_chain())

    calculated = [a for a in priced.analytics if a.calculation_status == "calculated"]
    assert len(calculated) == len(priced.analytics)
    for analytic in calculated:
        assert analytic.implied_volatility == pytest.approx(
            _smile_iv(analytic.identity.strike, analytic.forward), abs=1e-4
        )
        assert analytic.forward is not None and analytic.forward > _SPOT
        assert analytic.valuation == "fair"
        assert abs(analytic.iv_residual) < 5e-3


def test_one_smile_per_scored_expiry_with_matching_parameterization(
    config: PipelineConfig,
) -> None:
    priced = price_snapshot(
        snapshot_id=uuid4(),
        priced_at=_OBSERVED_AT,
        records=_full_chain(),
        config=config,
    )

    assert {s.expiry for s in priced.smiles} == {
        _NEAR_EXPIRY.date(),
        _FAR_EXPIRY.date(),
    }
    for smile in priced.smiles:
        matching = [
            a
            for a in priced.analytics
            if a.identity.expiry == smile.expiry
            and a.calculation_status == "calculated"
        ]
        for analytic in matching:
            assert smile.evaluate(analytic.identity.strike) == pytest.approx(
                analytic.fitted_iv, abs=1e-9
            )


def test_a_rich_strike_is_flagged_expensive(config: PipelineConfig) -> None:
    records = _full_chain()
    # push one near-expiry call's premium up ~4 vol points above the smile
    target = next(
        i
        for i, r in enumerate(records)
        if r.expiry == _NEAR_EXPIRY and r.strike == 22_650 and r.option_type == "call"
    )
    years = time_to_expiry(expiry_date=_NEAR_EXPIRY.date(), as_of=_OBSERVED_AT)
    forward = forward_price(
        spot=_SPOT, risk_free_rate=_R, dividend_yield=_Q, years=years
    )
    inflated = black_scholes_price(
        S=_SPOT,
        K=22_650,
        T=years,
        r=_R,
        q=_Q,
        sigma=_smile_iv(22_650, forward) + 0.04,
        option_type="call",
    )
    records[target] = replace(
        records[target],
        last_price=inflated,
        bid=inflated - 0.5,
        ask=inflated + 0.5,
    )

    priced = price_snapshot(
        snapshot_id=uuid4(),
        priced_at=_OBSERVED_AT,
        records=records,
        config=config,
    )
    rich = next(
        a
        for a in priced.analytics
        if a.identity.expiry == _NEAR_EXPIRY.date()
        and a.identity.strike == 22_650
        and a.identity.option_type == "call"
    )
    assert rich.valuation == "expensive"
    assert rich.richness_price > 0.0
    others_fair = [
        a
        for a in priced.analytics
        if a.identity.expiry == _NEAR_EXPIRY.date()
        and a.calculation_status == "calculated"
        and not (a.identity.strike == 22_650 and a.identity.option_type == "call")
    ]
    assert all(a.valuation == "fair" for a in others_fair)


def test_excluded_contracts_carry_a_reason_and_null_richness(
    config: PipelineConfig,
) -> None:
    records = _full_chain()
    target = next(
        i
        for i, r in enumerate(records)
        if r.expiry == _NEAR_EXPIRY and r.strike == 22_800 and r.option_type == "call"
    )
    records[target] = replace(records[target], volume=0)

    priced = price_snapshot(
        snapshot_id=uuid4(),
        priced_at=_OBSERVED_AT,
        records=records,
        config=config,
    )
    excluded = [
        a for a in priced.analytics if a.calculation_status == "excluded_zero_volume"
    ]
    assert excluded
    for analytic in excluded:
        assert analytic.exclusion_reason
        assert analytic.valuation is None
        assert analytic.fitted_iv is None
        assert analytic.forward is None


def test_a_thin_expiry_is_unscored(config: PipelineConfig) -> None:
    thin = [
        _record(expiry=_FAR_EXPIRY, strike=strike, option_type="call")
        for strike in (22_650, 22_800, 22_950)
    ]
    priced = price_snapshot(
        snapshot_id=uuid4(),
        priced_at=_OBSERVED_AT,
        records=thin,
        config=config,
    )

    assert priced.smiles == ()
    assert {a.valuation for a in priced.analytics} == {"unscored"}
    assert all(a.calculation_status == "calculated" for a in priced.analytics)
    assert all(a.fitted_iv is None for a in priced.analytics)


def test_greeks_match_the_domain_layer(config: PipelineConfig) -> None:
    priced = price_snapshot(
        snapshot_id=uuid4(),
        priced_at=_OBSERVED_AT,
        records=[
            _record(expiry=_NEAR_EXPIRY, strike=22_800, option_type="call"),
        ],
        config=config,
    )
    analytic = priced.analytics[0]
    years = analytic.time_to_expiry
    assert years is not None
    expected = black_scholes_greeks(
        S=_SPOT,
        K=22_800,
        T=years,
        r=_R,
        q=_Q,
        sigma=analytic.implied_volatility,
        option_type="call",
    )
    assert analytic.delta == pytest.approx(expected.delta, abs=1e-9)
    assert analytic.vega == pytest.approx(expected.vega, abs=1e-9)
    assert analytic.theta == pytest.approx(expected.theta, abs=1e-9)
