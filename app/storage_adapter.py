"""Adapter from storage snapshots to reader-owned Streamlit models."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from app.models import Contract, RepositoryUnavailable, Snapshot, SnapshotSummary
from nifty_vol.domain import time_to_expiry
from nifty_vol.storage import OptionObservation, SnapshotMeta


class StorageSnapshotSource(Protocol):
    """Read methods supplied by the concrete storage repository."""

    def list_snapshots(self) -> Sequence[SnapshotMeta]: ...

    def get_snapshot_meta(self, snapshot_id: UUID) -> SnapshotMeta | None: ...

    def list_expiries(self, snapshot_id: UUID) -> list[date]: ...

    def get_curve(
        self, snapshot_id: UUID, expiry: date, *, calculated_only: bool = True
    ) -> list[OptionObservation]: ...


def _contract(item: OptionObservation) -> Contract:
    return Contract(
        expiry=item.identity.expiry,
        strike=item.identity.strike,
        option_type=item.identity.option_type,
        status=item.calculation_status,
        market_price=item.selected_price,
        price_source=item.price_source,
        implied_volatility=item.implied_volatility,
        delta=item.delta,
        gamma=item.gamma,
        vega=item.vega,
        theta=item.theta,
    )


class StorageReaderAdapter:
    """Expose completed storage snapshots through the reader protocol."""

    def __init__(self, repository: StorageSnapshotSource):
        self._repository = repository

    def list_completed_snapshots(self) -> list[SnapshotSummary]:
        return [
            SnapshotSummary(str(item.snapshot_id), item.collected_at)
            for item in self._repository.list_snapshots()
        ]

    def get_completed_snapshot(self, snapshot_id: str) -> Snapshot:
        try:
            identifier = UUID(snapshot_id)
        except ValueError as exc:
            raise RepositoryUnavailable("invalid snapshot identifier") from exc
        meta = self._repository.get_snapshot_meta(identifier)
        if meta is None:
            raise RepositoryUnavailable("completed snapshot was not found")

        expiries = self._repository.list_expiries(identifier)
        observations = tuple(
            item
            for expiry in expiries
            for item in self._repository.get_curve(
                identifier, expiry, calculated_only=False
            )
        )
        forwards = self._forwards(
            expiries,
            spot=meta.spot,
            risk_free_rate=meta.risk_free_rate,
            dividend_yield=meta.dividend_yield,
            captured_at=meta.collected_at,
        )
        return Snapshot(
            snapshot_id=str(meta.snapshot_id),
            captured_at=meta.collected_at,
            spot=meta.spot,
            contracts=tuple(_contract(item) for item in observations),
            forwards=forwards,
            thresholds={key: str(value) for key, value in meta.thresholds.items()},
        )

    @staticmethod
    def _forwards(
        expiries: list[date],
        *,
        spot: float,
        risk_free_rate: float,
        dividend_yield: float,
        captured_at: datetime,
    ) -> dict[date, float]:
        result: dict[date, float] = {}
        for expiry in expiries:
            T = time_to_expiry(expiry_date=expiry, as_of=captured_at)
            if T > 0:
                result[expiry] = spot * math.exp(
                    (risk_free_rate - dividend_yield) * T
                )
        return result
