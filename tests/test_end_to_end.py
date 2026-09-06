from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from app.storage_adapter import StorageReaderAdapter
from nifty_vol.collector import parse_option_chain
from nifty_vol.collector.cli import collect_and_price_once
from nifty_vol.pipeline import PipelineConfig
from nifty_vol.storage import (
    OptionAnalytics,
    PricedSnapshotMeta,
    PricingRun,
    PricingSmile,
    RawCollectionRun,
)

FIXTURE = Path(__file__).parent / "collector" / "fixtures" / "option_chain.json"


class MemorySnapshotRepository:
    def __init__(self) -> None:
        self.collection: RawCollectionRun | None = None
        self.pricing: PricingRun | None = None
        self.analytics: tuple[OptionAnalytics, ...] = ()
        self.smiles: tuple[PricingSmile, ...] = ()

    def write_collection_atomic(self, run: RawCollectionRun) -> UUID:
        self.collection = run
        return run.snapshot_id

    def write_pricing_atomic(
        self,
        run: PricingRun,
        rows: tuple[OptionAnalytics, ...],
        smiles: tuple[PricingSmile, ...] = (),
    ) -> UUID:
        assert self.collection is not None
        assert run.snapshot_id == self.collection.snapshot_id
        self.pricing = run
        self.analytics = rows
        self.smiles = smiles
        return run.snapshot_id

    def list_priced_snapshots(self) -> list[PricedSnapshotMeta]:
        return [self._meta()]

    def get_priced_snapshot_meta(
        self, snapshot_id: UUID
    ) -> PricedSnapshotMeta | None:
        meta = self._meta()
        return meta if meta.snapshot_id == snapshot_id else None

    def list_expiries(self, snapshot_id: UUID):
        assert self.pricing is not None and snapshot_id == self.pricing.snapshot_id
        return sorted({item.identity.expiry for item in self.analytics})

    def get_analytics_curve(
        self, snapshot_id: UUID, expiry, *, calculated_only: bool = True
    ) -> list[OptionAnalytics]:
        assert self.pricing is not None and snapshot_id == self.pricing.snapshot_id
        return [
            item
            for item in self.analytics
            if item.identity.expiry == expiry
            and (not calculated_only or item.calculation_status == "calculated")
        ]

    def list_smiles(self, snapshot_id: UUID) -> list[PricingSmile]:
        assert self.pricing is not None and snapshot_id == self.pricing.snapshot_id
        return list(self.smiles)

    def _meta(self) -> PricedSnapshotMeta:
        assert self.collection is not None and self.pricing is not None
        return PricedSnapshotMeta(
            snapshot_id=self.pricing.snapshot_id,
            collected_at=self.collection.collected_at,
            priced_at=self.pricing.priced_at,
            spot=self.collection.spot,
            risk_free_rate=self.pricing.risk_free_rate,
            dividend_yield=self.pricing.dividend_yield,
            model_name=self.pricing.model_name,
            assumptions=self.pricing.assumptions,
            thresholds=self.pricing.thresholds,
            contract_count=len(self.analytics),
        )


class FixtureClient:
    def __init__(self, records) -> None:
        self.records = records
        self.attempt_count = 1

    def fetch_option_chain(self):
        return self.records


def test_stored_fixture_flows_to_reader_projection() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    records = parse_option_chain(
        payload, fetched_at=datetime(2026, 9, 4, 10, tzinfo=UTC)
    )
    repository = MemorySnapshotRepository()
    snapshot_id = collect_and_price_once(
        FixtureClient(records),
        repository,
        repository,
        PipelineConfig(risk_free_rate=0.06, dividend_yield=0.01),
        priced_at=datetime(2026, 9, 4, 10, 1, tzinfo=UTC),
    )

    reader = StorageReaderAdapter(repository)
    summary = reader.list_completed_snapshots()[0]
    assert summary.snapshot_id == str(snapshot_id)
    snapshot = reader.get_completed_snapshot(summary.snapshot_id)

    assert len(snapshot.contracts) == 3
    assert [item.status for item in snapshot.contracts].count("calculated") == 2
    assert [item.status for item in snapshot.contracts].count(
        "excluded_zero_volume"
    ) == 1
    assert all(
        item.implied_volatility is not None
        for item in snapshot.contracts
        if item.status == "calculated"
    )
    expiry = snapshot.contracts[0].expiry
    assert snapshot.forward_for(expiry) > snapshot.spot
    assert snapshot.forwards == {expiry: pytest.approx(snapshot.forward_for(expiry))}
