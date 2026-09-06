"""Reader-owned repository contract and immutable view data."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from math import log
from typing import Protocol

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

# Advisory richness labels (task #1). ``unscored`` means the contract is priced
# but its expiry lacked enough calculated peers to fit a reference smile.
VALUATIONS = ("cheap", "fair", "expensive", "unscored")


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
    # Richness scoring (task #1). Advisory: populated only when the contract is
    # ``calculated`` and its expiry had a fitted reference smile, otherwise
    # ``valuation`` is ``"unscored"`` (or None) and the rest are None.
    fitted_iv: float | None = None
    iv_residual: float | None = None
    richness_price: float | None = None
    richness_z: float | None = None
    valuation: str | None = None

    def __post_init__(self) -> None:
        if self.option_type not in {"call", "put"}:
            raise ValueError("option_type must be 'call' or 'put'")
        if self.status not in KNOWN_STATUSES:
            raise ValueError(f"unknown calculation status: {self.status}")
        if self.status == CALCULATED and self.implied_volatility is None:
            raise ValueError("a calculated contract must have implied volatility")
        if self.valuation is not None and self.valuation not in VALUATIONS:
            raise ValueError(f"unknown valuation: {self.valuation}")


@dataclass(frozen=True, slots=True)
class FittedSmile:
    """A fitted reference volatility smile for one expiry.

    Evaluated as ``iv = c0 + c1*k + c2*k**2`` with ``k = ln(strike / forward)``
    (natural log), matching the pricer's ``pricing_smiles`` contract.
    """

    expiry: date
    forward: float
    c0: float
    c1: float
    c2: float
    sample_size: int
    residual_scale: float

    def evaluate(self, strike: float) -> float:
        """Reference IV at *strike* on this smile."""

        k = log(strike / self.forward)
        return self.c0 + self.c1 * k + self.c2 * k * k


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Reader projection of a single completed snapshot."""

    snapshot_id: str
    captured_at: datetime
    spot: float
    contracts: tuple[Contract, ...]
    forwards: Mapping[date, float] = field(default_factory=dict)
    thresholds: Mapping[str, str] = field(default_factory=dict)
    smiles: Mapping[date, FittedSmile] = field(default_factory=dict)

    def forward_for(self, expiry: date) -> float:
        """Return the carry forward for an expiry, falling back to spot."""

        return self.forwards.get(expiry, self.spot)

    def smile_for(self, expiry: date) -> FittedSmile | None:
        """Return the fitted reference smile for an expiry, if one was fit."""

        return self.smiles.get(expiry)


class SnapshotRepository(Protocol):
    """Read-only boundary supplied by storage composition code.

    Implementations must return completed snapshots only, in accordance with
    the atomicity contract.  The UI calls no other data source.
    """

    def list_completed_snapshots(self) -> Sequence[SnapshotSummary]: ...

    def get_completed_snapshot(self, snapshot_id: str) -> Snapshot: ...


class RepositoryUnavailable(RuntimeError):
    """A safe, user-displayable repository failure."""
