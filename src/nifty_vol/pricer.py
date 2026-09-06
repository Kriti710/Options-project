"""Pricer role: turn the collector's raw quotes into stored analytics.

For one collection snapshot this prices every observed contract with
Black-Scholes-Merton, fits a robust reference volatility smile per expiry,
scores each contract cheap / fair / expensive against that smile, and packages
the result for :meth:`SnapshotRepository.write_pricing_atomic`.

Raw market data and its selection rules stay shared with the collector-facing
pipeline (:mod:`nifty_vol.pipeline`); everything below the mark is pricer-owned.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from .collector import OptionRecord
from .domain import (
    CalculationStatus,
    Valuation,
    calculate_option,
    fit_smile,
    log_moneyness,
    score_expiry,
    time_to_expiry,
)
from .domain.richness import SmileQuote
from .pipeline import PipelineConfig, select_market_price, storage_expiry_date
from .storage import ContractIdentity, OptionAnalytics, PricingRun, PricingSmile

MODEL_NAME = "black_scholes_merton"

_ASSUMPTIONS = {
    "day_count": "ACT/365F",
    "expiry_time": "15:30:00",
    "expiry_timezone": "Asia/Kolkata",
    "rate_unit": "decimal",
    "volatility_unit": "decimal",
}


@dataclass(frozen=True, slots=True)
class PricedSnapshot:
    """Everything one pricing pass produces, ready to persist atomically."""

    run: PricingRun
    analytics: tuple[OptionAnalytics, ...]
    smiles: tuple[PricingSmile, ...]


def forward_price(
    *, spot: float, risk_free_rate: float, dividend_yield: float, years: float
) -> float:
    """Forward index level used for the ATM anchor: ``F = S * exp((r - q) * T)``."""

    return spot * math.exp((risk_free_rate - dividend_yield) * years)


@dataclass(slots=True)
class _Row:
    """A priced contract before its expiry's smile has been scored in."""

    identity: ContractIdentity
    calculation_status: str
    selected_price: float | None
    price_source: str | None
    forward: float | None
    time_to_expiry: float | None
    exclusion_reason: str | None = None
    implied_volatility: float | None = None
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None

    def with_richness(
        self,
        *,
        fitted_iv: float | None = None,
        iv_residual: float | None = None,
        richness_price: float | None = None,
        richness_z: float | None = None,
        valuation: str | None = None,
    ) -> OptionAnalytics:
        return OptionAnalytics(
            identity=self.identity,
            calculation_status=self.calculation_status,
            selected_price=self.selected_price,
            price_source=self.price_source,
            forward=self.forward,
            time_to_expiry=self.time_to_expiry,
            exclusion_reason=self.exclusion_reason,
            implied_volatility=self.implied_volatility,
            delta=self.delta,
            gamma=self.gamma,
            vega=self.vega,
            theta=self.theta,
            fitted_iv=fitted_iv,
            iv_residual=iv_residual,
            richness_price=richness_price,
            richness_z=richness_z,
            valuation=valuation,
        )


def _snapshot_spot(records: Sequence[OptionRecord]) -> float:
    spots = {item.underlying_spot for item in records}
    if len(spots) != 1:
        raise ValueError("one snapshot must carry exactly one underlying spot")
    spot = next(iter(spots))
    if not math.isfinite(spot) or spot <= 0.0:
        raise ValueError("underlying spot must be finite and positive")
    return spot


def _price_one(record: OptionRecord, config: PipelineConfig, spot: float) -> _Row:
    expiry = storage_expiry_date(record.expiry)
    selected = select_market_price(record)
    identity = ContractIdentity(expiry, record.strike, record.option_type)
    row = _Row(
        identity=identity,
        calculation_status=CalculationStatus.CALCULATED.value,
        selected_price=selected.value,
        price_source=selected.source,
        forward=None,
        time_to_expiry=None,
    )

    def excluded(status: CalculationStatus, reason: str) -> _Row:
        row.calculation_status = status.value
        row.exclusion_reason = reason
        return row

    if record.volume == 0:
        return excluded(CalculationStatus.EXCLUDED_ZERO_VOLUME, "volume is zero")
    if selected.value is None:
        return excluded(
            CalculationStatus.INVALID_MARKET_DATA,
            "neither a valid midpoint nor last traded price is available",
        )
    if selected.value < config.minimum_premium:
        return excluded(
            CalculationStatus.EXCLUDED_LOW_PREMIUM,
            f"selected price is below {config.minimum_premium}",
        )
    if not (math.isfinite(spot) and spot > 0.0) or not (
        math.isfinite(record.strike) and record.strike > 0.0
    ):
        return excluded(
            CalculationStatus.INVALID_MARKET_DATA,
            "spot and strike must be finite and positive",
        )
    distance = abs(record.strike - spot) / spot
    if distance > config.maximum_strike_distance:
        return excluded(
            CalculationStatus.EXCLUDED_OUTSIDE_STRIKE_RANGE,
            f"strike distance {distance} exceeds {config.maximum_strike_distance}",
        )

    years = time_to_expiry(expiry_date=expiry, as_of=record.observed_at)
    if years > 0.0:
        row.time_to_expiry = years
        row.forward = forward_price(
            spot=spot,
            risk_free_rate=config.risk_free_rate,
            dividend_yield=config.dividend_yield,
            years=years,
        )

    result = calculate_option(
        S=spot,
        K=record.strike,
        T=years,
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
        row.calculation_status = result.status.value
        row.exclusion_reason = result.failure.value if result.failure else "unknown"
        return row

    assert result.greeks is not None
    row.implied_volatility = result.implied_volatility
    row.delta = result.greeks.delta
    row.gamma = result.greeks.gamma
    row.vega = result.greeks.vega
    row.theta = result.greeks.theta
    return row


def _score_expiry_rows(
    rows: list[_Row],
) -> tuple[list[OptionAnalytics], PricingSmile | None]:
    calculated = [
        row
        for row in rows
        if row.calculation_status == CalculationStatus.CALCULATED.value
        and row.forward is not None
        and row.implied_volatility is not None
    ]
    quotes = [
        SmileQuote(
            key=row.identity,
            log_moneyness=log_moneyness(
                strike=row.identity.strike, forward=row.forward
            ),
            implied_volatility=row.implied_volatility,
            vega=row.vega if row.vega is not None else math.nan,
        )
        for row in calculated
    ]
    fit = fit_smile(quotes)

    if fit is None:
        analytics = [
            row.with_richness(
                valuation=(
                    Valuation.UNSCORED.value
                    if row.calculation_status == CalculationStatus.CALCULATED.value
                    else None
                )
            )
            for row in rows
        ]
        return analytics, None

    scored = {result.key: result for result in score_expiry(quotes, fit=fit)}
    analytics: list[OptionAnalytics] = []
    for row in rows:
        result = scored.get(row.identity)
        if result is None:
            analytics.append(row.with_richness())
            continue
        analytics.append(
            row.with_richness(
                fitted_iv=result.fitted_iv,
                iv_residual=result.iv_residual,
                richness_price=result.richness_price,
                richness_z=result.richness_z,
                valuation=result.valuation.value,
            )
        )

    forward = calculated[0].forward
    c0, c1, c2 = fit.coefficients
    smile = PricingSmile(
        expiry=calculated[0].identity.expiry,
        forward=forward,
        c0=c0,
        c1=c1,
        c2=c2,
        sample_size=fit.sample_size,
        residual_scale=fit.residual_scale,
    )
    return analytics, smile


def price_snapshot(
    *,
    snapshot_id: UUID,
    priced_at: datetime,
    records: Iterable[OptionRecord],
    config: PipelineConfig,
) -> PricedSnapshot:
    """Price one collection snapshot and score every contract against its smile."""

    items = tuple(records)
    if not items:
        raise ValueError("at least one option record is required")
    spot = _snapshot_spot(items)

    by_expiry: dict[date, list[_Row]] = defaultdict(list)
    seen: set[ContractIdentity] = set()
    for record in items:
        row = _price_one(record, config, spot)
        if row.identity in seen:
            raise ValueError(
                f"duplicate contract identity in snapshot: {row.identity}"
            )
        seen.add(row.identity)
        by_expiry[row.identity.expiry].append(row)

    analytics: list[OptionAnalytics] = []
    smiles: list[PricingSmile] = []
    for expiry in sorted(by_expiry):
        expiry_analytics, smile = _score_expiry_rows(by_expiry[expiry])
        analytics.extend(expiry_analytics)
        if smile is not None:
            smiles.append(smile)

    run = PricingRun(
        snapshot_id=snapshot_id,
        priced_at=priced_at,
        risk_free_rate=config.risk_free_rate,
        dividend_yield=config.dividend_yield,
        model_name=MODEL_NAME,
        assumptions=dict(_ASSUMPTIONS),
        thresholds=config.thresholds(),
    )
    return PricedSnapshot(
        run=run,
        analytics=tuple(analytics),
        smiles=tuple(smiles),
    )
