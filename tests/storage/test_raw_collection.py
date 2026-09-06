from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any
from unittest import TestCase
from uuid import uuid4

from nifty_vol.storage import (
    ContractIdentity,
    RawCollectionRun,
    RawOptionObservation,
)
from nifty_vol.storage.repository import SnapshotRepository


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.description = None
        self.closed = False

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        self.connection.statements.append((query, params))
        if self.connection.fail_on and self.connection.fail_on in query:
            raise RuntimeError("injected database failure")

    def executemany(self, query: str, params: Any) -> None:
        self.connection.statements.append((query, list(params)))
        if self.connection.fail_on and self.connection.fail_on in query:
            raise RuntimeError("injected database failure")

    def fetchall(self) -> list[Any]:
        return self.connection.rows

    def fetchone(self) -> Any | None:
        return self.connection.rows[0] if self.connection.rows else None

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, *, fail_on: str | None = None, rows: list[Any] | None = None):
        self.fail_on = fail_on
        self.rows = rows or []
        self.statements: list[tuple[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def observation(
    strike: float = 22000, option_type: str = "call"
) -> RawOptionObservation:
    return RawOptionObservation(
        identity=ContractIdentity(date(2026, 9, 24), strike, option_type),
        last_traded_price=100.0,
        bid=99.0,
        ask=101.0,
        volume=10,
        open_interest=20,
        nse_iv=0.21,
    )


def raw_run() -> RawCollectionRun:
    return RawCollectionRun(
        collected_at=datetime(2026, 9, 4, 10, tzinfo=UTC),
        spot=22000.0,
        observations=(observation(), observation(option_type="put")),
        attempt_count=2,
    )


class RawCollectionRunModelTests(TestCase):
    def test_normalizes_collected_at_to_utc(self) -> None:
        run = RawCollectionRun(
            collected_at=datetime(
                2026, 9, 4, 15, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))
            ),
            spot=22000.0,
            observations=(observation(),),
        )
        self.assertEqual(run.collected_at.isoformat(), "2026-09-04T10:00:00+00:00")

    def test_rejects_naive_timestamp(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            RawCollectionRun(
                collected_at=datetime(2026, 9, 4),
                spot=22000.0,
                observations=(),
            )

    def test_rejects_non_positive_spot(self) -> None:
        with self.assertRaisesRegex(ValueError, "spot must be positive"):
            RawCollectionRun(
                collected_at=datetime.now(UTC), spot=0.0, observations=()
            )

    def test_rejects_zero_attempt_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "attempt_count"):
            RawCollectionRun(
                collected_at=datetime.now(UTC),
                spot=22000.0,
                observations=(),
                attempt_count=0,
            )

    def test_rejects_duplicate_contract_identity(self) -> None:
        item = observation()
        with self.assertRaisesRegex(ValueError, "unique"):
            RawCollectionRun(
                collected_at=datetime.now(UTC),
                spot=22000.0,
                observations=(item, item),
            )

    def test_raw_observation_defaults_are_all_none(self) -> None:
        obs = RawOptionObservation(
            identity=ContractIdentity(date(2026, 9, 24), 22000, "call")
        )
        self.assertIsNone(obs.bid)
        self.assertIsNone(obs.nse_iv)


class RawWriterTests(TestCase):
    def test_writes_run_then_rows_then_promotes_and_commits_once(self) -> None:
        connection = FakeConnection()
        run = raw_run()

        result = SnapshotRepository(connection).write_collection_atomic(run)

        self.assertEqual(result, run.snapshot_id)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        statements = [s for s, _ in connection.statements]
        joined = " ".join(statements)
        self.assertLess(
            joined.index("INSERT INTO collection_runs"),
            joined.index("INSERT INTO option_observations"),
        )
        self.assertLess(
            joined.index("INSERT INTO option_observations"),
            joined.index("UPDATE collection_runs"),
        )
        # raw insert never touches the pricing columns
        self.assertNotIn("risk_free_rate", statements[0])
        self.assertNotIn("calculation_status", statements[1])
        self.assertIn("'in_progress'", statements[0])

    def test_row_failure_rolls_back_and_never_promotes(self) -> None:
        connection = FakeConnection(fail_on="INSERT INTO option_observations")

        with self.assertRaisesRegex(RuntimeError, "injected"):
            SnapshotRepository(connection).write_collection_atomic(raw_run())

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertNotIn(
            "UPDATE collection_runs",
            " ".join(s for s, _ in connection.statements),
        )

    def test_failed_collection_writes_failed_run_with_no_rows(self) -> None:
        connection = FakeConnection()
        snapshot_id = uuid4()

        result = SnapshotRepository(connection).record_failed_collection(
            snapshot_id,
            datetime(2026, 9, 4, 10, tzinfo=UTC),
            3,
            "NSE returned HTTP 401 after 3 attempts",
        )

        self.assertEqual(result, snapshot_id)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(len(connection.statements), 1)
        sql, params = connection.statements[0]
        self.assertIn("'failed'", sql)
        self.assertNotIn("option_observations", sql)
        self.assertEqual(params[2], 3)

    def test_failed_collection_rejects_naive_timestamp(self) -> None:
        connection = FakeConnection()
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            SnapshotRepository(connection).record_failed_collection(
                uuid4(), datetime(2026, 9, 4), 1, "boom"
            )


if __name__ == "__main__":  # pragma: no cover
    from unittest import main

    main()
