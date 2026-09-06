"""Adapter from storage snapshots to reader-owned Streamlit models.

Migration 002 split raw collection from computed pricing. The reader consumes
the priced half: ``pricing_runs`` metadata, ``option_analytics`` rows, and the
fitted ``pricing_smiles``. Forwards come straight from the pricer — the reader
no longer recomputes them.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol
from uuid import UUID

from app.models import (
    Contract,
    FittedSmile,
    RepositoryUnavailable,
    Snapshot,
    SnapshotSummary,
)
from nifty_vol.storage import OptionAnalytics, PricedSnapshotMeta, PricingSmile


class StorageSnapshotSource(Protocol):
    """Read methods supplied by the concrete storage repository (migration 002)."""

    def list_priced_snapshots(self) -> Sequence[PricedSnapshotMeta]: ...

    def get_priced_snapshot_meta(
        self, snapshot_id: UUID
    ) -> PricedSnapshotMeta | None: ...

    def list_expiries(self, snapshot_id: UUID) -> list[date]: ...

    def get_analytics_curve(
        self, snapshot_id: UUID, expiry: date, *, calculated_only: bool = True
    ) -> list[OptionAnalytics]: ...

    def list_smiles(self, snapshot_id: UUID) -> list[PricingSmile]: ...


def _contract(item: OptionAnalytics) -> Contract:
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
        fitted_iv=item.fitted_iv,
        iv_residual=item.iv_residual,
        richness_price=item.richness_price,
        richness_z=item.richness_z,
        valuation=item.valuation,
    )


def _smile(item: PricingSmile) -> FittedSmile:
    return FittedSmile(
        expiry=item.expiry,
        forward=item.forward,
        c0=item.c0,
        c1=item.c1,
        c2=item.c2,
        sample_size=item.sample_size,
        residual_scale=item.residual_scale,
    )


class StorageReaderAdapter:
    """Expose completed, priced storage snapshots through the reader protocol."""

    def __init__(self, repository: StorageSnapshotSource):
        self._repository = repository

    def list_completed_snapshots(self) -> list[SnapshotSummary]:
        return [
            SnapshotSummary(str(item.snapshot_id), item.collected_at)
            for item in self._repository.list_priced_snapshots()
        ]

    def get_completed_snapshot(self, snapshot_id: str) -> Snapshot:
        try:
            identifier = UUID(snapshot_id)
        except ValueError as exc:
            raise RepositoryUnavailable("invalid snapshot identifier") from exc
        meta = self._repository.get_priced_snapshot_meta(identifier)
        if meta is None:
            raise RepositoryUnavailable("completed snapshot was not found")

        expiries = self._repository.list_expiries(identifier)
        analytics = tuple(
            item
            for expiry in expiries
            for item in self._repository.get_analytics_curve(
                identifier, expiry, calculated_only=False
            )
        )
        smiles = {
            item.expiry: _smile(item)
            for item in self._repository.list_smiles(identifier)
        }
        return Snapshot(
            snapshot_id=str(meta.snapshot_id),
            captured_at=meta.collected_at,
            spot=meta.spot,
            contracts=tuple(_contract(item) for item in analytics),
            forwards=self._forwards(analytics, smiles),
            thresholds={key: str(value) for key, value in meta.thresholds.items()},
            smiles=smiles,
        )

    @staticmethod
    def _forwards(
        analytics: Sequence[OptionAnalytics],
        smiles: dict[date, FittedSmile],
    ) -> dict[date, float]:
        """Per-expiry forward: the fitted smile's, else any priced row's."""

        result: dict[date, float] = {
            expiry: smile.forward for expiry, smile in smiles.items()
        }
        for item in analytics:
            expiry = item.identity.expiry
            if expiry not in result and item.forward is not None:
                result[expiry] = item.forward
        return result
