"""Reader-owned repository contract and immutable view data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Mapping, Protocol, Sequence


CALCULATED = "calculated"
KNOWN_STATUSES = (
    CALCULATED,
    "excluded_zero_volume",
    "excluded_low_premium",
    "excluded_outside_strike_range",
    "invalid_market_data",
    "invalid_model_input",
    "solver_did_not_converge",
)


@dataclass(frozen=True, slots=True)
class SnapshotSummary:
    """A completed, atomically published snapshot available to the reader."""

    snapshot_id: str
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class Contract:
    """One attempted option calculation in a completed snapshot."""

    expiry: date
    strike: float
    option_type: str
    status: str
    market_price: float | None = None
    price_source: str | None = None
    implied_volatility: float | None = None
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None

    def __post_init__(self) -> None:
        if self.option_type not in {"call", "put"}:
            raise ValueError("option_type must be 'call' or 'put'")
        if self.status not in KNOWN_STATUSES:
            raise ValueError(f"unknown calculation status: {self.status}")
        if self.status == CALCULATED and self.implied_volatility is None:
            raise ValueError("a calculated contract must have implied volatility")


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Reader projection of a single completed snapshot."""

    snapshot_id: str
    captured_at: datetime
    spot: float
    forward: float | None
    contracts: tuple[Contract, ...]
    thresholds: Mapping[str, str] = field(default_factory=dict)


class SnapshotRepository(Protocol):
    """Read-only boundary supplied by storage composition code.

    Implementations must return completed snapshots only, in accordance with
    the atomicity contract.  The UI calls no other data source.
    """

    def list_completed_snapshots(self) -> Sequence[SnapshotSummary]: ...

    def get_completed_snapshot(self, snapshot_id: str) -> Snapshot: ...


class RepositoryUnavailable(RuntimeError):
    """A safe, user-displayable repository failure."""
