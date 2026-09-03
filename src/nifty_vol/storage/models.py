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
