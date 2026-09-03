from datetime import UTC, date, datetime
from typing import Any
from unittest import TestCase
from uuid import uuid4

from nifty_vol.storage import CollectionRun, ContractIdentity, OptionObservation
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
        materialized = list(params)
        self.connection.statements.append((query, materialized))
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
        self.cursors: list[FakeCursor] = []

    def cursor(self) -> FakeCursor:
        cursor = FakeCursor(self)
        self.cursors.append(cursor)
        return cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def excluded() -> OptionObservation:
    return OptionObservation(
        identity=ContractIdentity(date(2026, 9, 24), 22000, "put"),
        last_traded_price=0,
        bid=0,
        ask=0,
        volume=0,
        open_interest=100,
        selected_price=0,
        price_source="midpoint",
        calculation_status="excluded_zero_volume",
        exclusion_reason="volume is zero",
    )


def run() -> CollectionRun:
    return CollectionRun(
        collected_at=datetime(2026, 9, 4, 10, tzinfo=UTC),
        spot=22000,
        risk_free_rate=0.065,
        dividend_yield=0,
        model_name="black_scholes_merton",
        assumptions={"day_count": "ACT/365F", "expiry_timezone": "Asia/Kolkata"},
        thresholds={"minimum_premium": 0.5, "solver_tolerance": 1e-6},
        observations=(excluded(),),
    )


class AtomicWriterTests(TestCase):
    def test_writer_inserts_rows_promotes_and_commits_once(self) -> None:
        connection = FakeConnection()
        item = run()

        result = SnapshotRepository(connection).write_snapshot_atomic(item)

        self.assertEqual(result, item.snapshot_id)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        sql = " ".join(statement for statement, _ in connection.statements)
        self.assertLess(
            sql.index("INSERT INTO collection_runs"),
            sql.index("INSERT INTO option_observations"),
        )
        self.assertLess(
            sql.index("INSERT INTO option_observations"),
            sql.index("UPDATE collection_runs"),
        )
        self.assertIn("status = 'completed'", sql)
        run_params = connection.statements[0][1]
        self.assertEqual(run_params[1].tzinfo, UTC)
        self.assertIn('"solver_tolerance": 1e-06', run_params[-1])
        self.assertTrue(all(cursor.closed for cursor in connection.cursors))

    def test_any_row_failure_rolls_back_and_never_promotes_or_commits(self) -> None:
        connection = FakeConnection(fail_on="INSERT INTO option_observations")

        with self.assertRaisesRegex(RuntimeError, "injected"):
            SnapshotRepository(connection).write_snapshot_atomic(run())

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        sql = " ".join(statement for statement, _ in connection.statements)
        self.assertNotIn("UPDATE collection_runs", sql)


class CompletedReaderTests(TestCase):
    def test_every_reader_joins_or_filters_completed_runs(self) -> None:
        connection = FakeConnection()
        repository = SnapshotRepository(connection)
        snapshot_id = uuid4()
        identity = ContractIdentity(date(2026, 9, 24), 22000, "call")

        repository.list_snapshots()
        repository.get_snapshot_meta(snapshot_id)
        repository.list_expiries(snapshot_id)
        repository.get_curve(snapshot_id, identity.expiry)
        repository.get_contract(snapshot_id, identity)
        repository.get_exclusion_summary(snapshot_id)

        self.assertEqual(len(connection.statements), 6)
        for sql, _ in connection.statements:
            self.assertIn("status = 'completed'", sql)

    def test_curve_defaults_to_calculated_contracts(self) -> None:
        connection = FakeConnection()
        SnapshotRepository(connection).get_curve(uuid4(), date(2026, 9, 24))
        self.assertIn("calculation_status = 'calculated'", connection.statements[0][0])

    def test_exclusion_summary_maps_status_counts(self) -> None:
        connection = FakeConnection(
            rows=[
                {"calculation_status": "excluded_zero_volume", "count": 4},
                {"calculation_status": "invalid_market_data", "count": 2},
            ]
        )
        summary = SnapshotRepository(connection).get_exclusion_summary(uuid4())
        self.assertEqual(
            summary, {"excluded_zero_volume": 4, "invalid_market_data": 2}
        )
