from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from app.storage_adapter import StorageReaderAdapter
from nifty_vol.collector import parse_option_chain
from nifty_vol.collector.cli import collect_once
from nifty_vol.pipeline import PipelineConfig
from nifty_vol.storage import CollectionRun, OptionObservation, SnapshotMeta

FIXTURE = Path(__file__).parent / "collector" / "fixtures" / "option_chain.json"


class MemorySnapshotRepository:
    def __init__(self) -> None:
        self.run: CollectionRun | None = None

    def write_snapshot_atomic(self, run: CollectionRun) -> UUID:
        self.run = run
        return run.snapshot_id

    def list_snapshots(self) -> list[SnapshotMeta]:
        return [self._meta()]

    def get_snapshot_meta(self, snapshot_id: UUID) -> SnapshotMeta | None:
        meta = self._meta()
        return meta if meta.snapshot_id == snapshot_id else None

    def list_expiries(self, snapshot_id: UUID):
        assert self.run is not None and snapshot_id == self.run.snapshot_id
        return sorted({item.identity.expiry for item in self.run.observations})

    def get_curve(
        self, snapshot_id: UUID, expiry, *, calculated_only: bool = True
    ) -> list[OptionObservation]:
        assert self.run is not None and snapshot_id == self.run.snapshot_id
        return [
            item
            for item in self.run.observations
            if item.identity.expiry == expiry
            and (not calculated_only or item.calculation_status == "calculated")
        ]

    def _meta(self) -> SnapshotMeta:
        assert self.run is not None
        return SnapshotMeta(
            snapshot_id=self.run.snapshot_id,
            collected_at=self.run.collected_at,
            completed_at=self.run.collected_at,
            spot=self.run.spot,
            risk_free_rate=self.run.risk_free_rate,
            dividend_yield=self.run.dividend_yield,
            model_name=self.run.model_name,
            assumptions=self.run.assumptions,
            thresholds=self.run.thresholds,
            contract_count=len(self.run.observations),
        )


class FixtureClient:
    def __init__(self, records) -> None:
        self.records = records

    def fetch_option_chain(self):
        return self.records


def test_stored_fixture_flows_to_reader_projection() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    records = parse_option_chain(
        payload, fetched_at=datetime(2026, 9, 4, 10, tzinfo=UTC)
    )
    repository = MemorySnapshotRepository()
    snapshot_id = collect_once(
        FixtureClient(records),
        repository,
        PipelineConfig(risk_free_rate=0.06, dividend_yield=0.01),
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
