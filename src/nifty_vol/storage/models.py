"""Values crossing the storage boundary.

Rates and volatilities are decimals (not percentages), and every datetime is
normalized to UTC before it reaches SQL.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

CALCULATION_STATUSES = frozenset(
    {
        "calculated",
        "excluded_zero_volume",
        "excluded_low_premium",
        "excluded_outside_strike_range",
        "invalid_market_data",
        "invalid_model_input",
        "solver_did_not_converge",
    }
)
PRICE_SOURCES = frozenset({"midpoint", "last_traded_price"})
OPTION_TYPES = frozenset({"call", "put"})
VALUATIONS = frozenset({"cheap", "fair", "expensive", "unscored"})


def utc_datetime(value: datetime, field_name: str) -> datetime:
    """Reject naive datetimes and return an equivalent UTC datetime."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ContractIdentity:
    expiry: date
    strike: float
    option_type: str

    def __post_init__(self) -> None:
        if self.option_type not in OPTION_TYPES:
            raise ValueError("option_type must be 'call' or 'put'")
        if self.strike <= 0:
            raise ValueError("strike must be positive")


@dataclass(frozen=True, slots=True)
class RawOptionObservation:
    """One contract's raw NSE quote, before any pricing.

    Written by the `collector` role into `option_observations`. Carries only the
    market-data columns plus NSE's own published IV; the pricer fills the
    computed columns later via `option_analytics`.
    """

    identity: ContractIdentity
    last_traded_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    nse_iv: float | None = None


@dataclass(frozen=True, slots=True)
class RawCollectionRun:
    """One raw collection snapshot: the option chain as fetched, nothing priced.

    Written by the `collector` role. Promoted to `completed` atomically once
    every row is persisted.
    """

    collected_at: datetime
    spot: float
    observations: tuple[RawOptionObservation, ...]
    attempt_count: int = 1
    snapshot_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "collected_at", utc_datetime(self.collected_at, "collected_at")
        )
        if self.spot <= 0:
            raise ValueError("spot must be positive")
        if self.attempt_count < 1:
            raise ValueError("attempt_count must be at least 1")
        identities = [item.identity for item in self.observations]
        if len(identities) != len(set(identities)):
            raise ValueError("contract identity must be unique within a snapshot")


@dataclass(frozen=True, slots=True)
class OptionObservation:
    identity: ContractIdentity
    last_traded_price: float | None
    bid: float | None
    ask: float | None
    volume: int | None
    open_interest: int | None
    selected_price: float | None
    price_source: str | None
    calculation_status: str
    exclusion_reason: str | None = None
    implied_volatility: float | None = None
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None
    time_to_expiry: float | None = None

    def __post_init__(self) -> None:
        if self.calculation_status not in CALCULATION_STATUSES:
            raise ValueError(f"unknown calculation_status: {self.calculation_status}")
        if self.price_source is not None and self.price_source not in PRICE_SOURCES:
            raise ValueError(f"unknown price_source: {self.price_source}")
        if self.calculation_status == "calculated":
            if self.exclusion_reason is not None:
                raise ValueError(
                    "calculated observations cannot have an exclusion_reason"
                )
            required = (
                self.implied_volatility,
                self.delta,
                self.gamma,
                self.vega,
                self.theta,
                self.time_to_expiry,
            )
            if any(value is None for value in required):
                raise ValueError("calculated observations require IV, Greeks, and T")
        elif not self.exclusion_reason:
            raise ValueError("non-calculated observations require an exclusion_reason")


@dataclass(frozen=True, slots=True)
class CollectionRun:
    collected_at: datetime
    spot: float
    risk_free_rate: float
    dividend_yield: float
    model_name: str
    assumptions: Mapping[str, Any]
    thresholds: Mapping[str, Any]
    observations: tuple[OptionObservation, ...]
    snapshot_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "collected_at", utc_datetime(self.collected_at, "collected_at")
        )
        if self.spot <= 0:
            raise ValueError("spot must be positive")
        identities = [item.identity for item in self.observations]
        if len(identities) != len(set(identities)):
            raise ValueError("contract identity must be unique within a snapshot")


@dataclass(frozen=True, slots=True)
class SnapshotMeta:
    snapshot_id: UUID
    collected_at: datetime
    completed_at: datetime
    spot: float
    risk_free_rate: float
    dividend_yield: float
    model_name: str
    assumptions: Mapping[str, Any]
    thresholds: Mapping[str, Any]
    contract_count: int


# ---------------------------------------------------------------------------
# Split storage model (migration 002): raw collection and computed pricing are
# separate tables with separate write roles. `OptionObservation` above keeps the
# whole combined row for the legacy single-writer path; the models below carry
# only the pricer-owned half.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PricingRun:
    """One pricing pass over one collection snapshot: one rate and threshold set.

    Written by the `pricer` role into `pricing_runs`. `snapshot_id` matches the
    `collection_runs` row the raw quotes came from.
    """

    snapshot_id: UUID
    priced_at: datetime
    risk_free_rate: float
    dividend_yield: float
    model_name: str
    assumptions: Mapping[str, Any]
    thresholds: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "priced_at", utc_datetime(self.priced_at, "priced_at")
        )


@dataclass(frozen=True, slots=True)
class OptionAnalytics:
    """Computed implied volatility and Greeks for one observed contract.

    Written by the `pricer` role into `option_analytics`. The identity must
    match an existing `option_observations` row in the same snapshot.
    """

    identity: ContractIdentity
    calculation_status: str
    selected_price: float | None = None
    price_source: str | None = None
    forward: float | None = None
    time_to_expiry: float | None = None
    exclusion_reason: str | None = None
    implied_volatility: float | None = None
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None
    # Richness scoring (task #1). Advisory; populated by the pricer only when the
    # contract is `calculated` and its expiry had enough priced peers to fit a
    # reference smile, otherwise `valuation` is "unscored" and the rest are None.
    fitted_iv: float | None = None
    iv_residual: float | None = None
    richness_price: float | None = None
    richness_z: float | None = None
    valuation: str | None = None

    def __post_init__(self) -> None:
        if self.calculation_status not in CALCULATION_STATUSES:
            raise ValueError(
                f"unknown calculation_status: {self.calculation_status}"
            )
        if self.price_source is not None and self.price_source not in PRICE_SOURCES:
            raise ValueError(f"unknown price_source: {self.price_source}")
        if self.valuation is not None and self.valuation not in VALUATIONS:
            raise ValueError(f"unknown valuation: {self.valuation}")
        if (self.forward is None) != (self.time_to_expiry is None):
            raise ValueError(
                "forward is defined exactly when time_to_expiry is defined"
            )
        if self.calculation_status == "calculated":
            if self.exclusion_reason is not None:
                raise ValueError(
                    "calculated analytics cannot have an exclusion_reason"
                )
            required = (
                self.implied_volatility,
                self.delta,
                self.gamma,
                self.vega,
                self.theta,
                self.time_to_expiry,
            )
            if any(value is None for value in required):
                raise ValueError("calculated analytics require IV, Greeks, and T")
        elif not self.exclusion_reason:
            raise ValueError("non-calculated analytics require an exclusion_reason")


@dataclass(frozen=True, slots=True)
class PricingSmile:
    """The fitted reference smile for one expiry of one pricing pass.

    Written by the `pricer` role into `pricing_smiles`. Evaluated as
    ``iv = c0 + c1*k + c2*k**2`` with ``k = ln(strike / forward)`` (natural log).
    A row exists only for expiries with enough calculated contracts to fit.
    """

    expiry: date
    forward: float
    c0: float
    c1: float
    c2: float
    sample_size: int
    residual_scale: float

    def __post_init__(self) -> None:
        if self.forward <= 0:
            raise ValueError("forward must be positive")
        if self.sample_size < 3:
            raise ValueError("a quadratic smile fit needs at least 3 contracts")
        if self.residual_scale < 0:
            raise ValueError("residual_scale cannot be negative")

    def evaluate(self, strike: float) -> float:
        """Reference IV at *strike* on this smile."""
        from math import log

        k = log(strike / self.forward)
        return self.c0 + self.c1 * k + self.c2 * k * k


@dataclass(frozen=True, slots=True)
class PricedSnapshotMeta:
    """Run metadata for a completed, priced snapshot (collection + pricing)."""

    snapshot_id: UUID
    collected_at: datetime
    priced_at: datetime
    spot: float
    risk_free_rate: float
    dividend_yield: float
    model_name: str
    assumptions: Mapping[str, Any]
    thresholds: Mapping[str, Any]
    contract_count: int
