from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from app.models import Contract, Snapshot, SnapshotRepository, SnapshotSummary


class FakeRepository:
    def __init__(self) -> None:
        self.requests: list[str] = []
        self.data = Snapshot(
            snapshot_id="completed-1",
            captured_at=datetime(2026, 9, 4, tzinfo=UTC),
            spot=25_000,
            contracts=(
                Contract(
                    expiry=date(2026, 9, 10),
                    strike=25_000,
                    option_type="call",
                    status="calculated",
                    implied_volatility=0.2,
                ),
            ),
            forwards={date(2026, 9, 10): 25_010},
        )

    def list_completed_snapshots(self):
        return [SnapshotSummary(self.data.snapshot_id, self.data.captured_at)]

    def get_completed_snapshot(self, snapshot_id: str) -> Snapshot:
        self.requests.append(snapshot_id)
        return self.data


class RepositoryBoundaryTest(unittest.TestCase):
    def test_mock_implements_reader_repository_and_returns_completed_data(self) -> None:
        repository: SnapshotRepository = FakeRepository()
        summaries = repository.list_completed_snapshots()
        loaded = repository.get_completed_snapshot(summaries[0].snapshot_id)
        self.assertEqual(loaded.snapshot_id, "completed-1")
        self.assertEqual(repository.requests, ["completed-1"])


if __name__ == "__main__":
    unittest.main()
