from datetime import UTC, date, datetime
from typing import Any
from unittest import TestCase
from uuid import uuid4

from nifty_vol.storage import (
    ContractIdentity,
    OptionAnalytics,
    PricingRun,
    PricingSmile,
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


def pricing_run(snapshot_id: Any = None) -> PricingRun:
    return PricingRun(
        snapshot_id=snapshot_id or uuid4(),
        priced_at=datetime(2026, 9, 4, 10, tzinfo=UTC),
        risk_free_rate=0.065,
        dividend_yield=0.0,
        model_name="black_scholes_merton",
        assumptions={"day_count": "ACT/365F"},
        thresholds={"solver_tolerance": 1e-6},
    )


def calculated_row() -> OptionAnalytics:
    return OptionAnalytics(
        identity=ContractIdentity(date(2026, 9, 24), 22000, "call"),
        calculation_status="calculated",
        selected_price=100.0,
        price_source="midpoint",
        forward=22050.0,
        time_to_expiry=0.05,
        implied_volatility=0.2,
        delta=0.5,
        gamma=0.001,
        vega=10.0,
        theta=-5.0,
    )


def excluded_row() -> OptionAnalytics:
    return OptionAnalytics(
        identity=ContractIdentity(date(2026, 9, 24), 30000, "call"),
        calculation_status="excluded_outside_strike_range",
        exclusion_reason="strike distance exceeds limit",
        selected_price=1.2,
        price_source="last_traded_price",
    )


def smile() -> PricingSmile:
    return PricingSmile(
        expiry=date(2026, 9, 24),
        forward=22050.0,
        c0=0.18,
        c1=-0.04,
        c2=0.5,
        sample_size=9,
        residual_scale=0.01,
    )


class PricerWriterTests(TestCase):
    def test_pricing_run_written_before_analytics_and_commits_once(self) -> None:
        connection = FakeConnection()
        run = pricing_run()

        result = SnapshotRepository(connection).write_pricing_atomic(
            run, (calculated_row(), excluded_row()), (smile(),)
        )

        self.assertEqual(result, run.snapshot_id)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        sql = " ".join(statement for statement, _ in connection.statements)
        self.assertLess(
            sql.index("INSERT INTO pricing_runs"),
            sql.index("INSERT INTO pricing_smiles"),
        )
        self.assertLess(
            sql.index("INSERT INTO pricing_smiles"),
            sql.index("INSERT INTO option_analytics"),
        )

    def test_smiles_are_optional(self) -> None:
        connection = FakeConnection()
        SnapshotRepository(connection).write_pricing_atomic(
            pricing_run(), (calculated_row(),)
        )
        sql = " ".join(statement for statement, _ in connection.statements)
        self.assertNotIn("INSERT INTO pricing_smiles", sql)

    def test_analytics_failure_rolls_back_and_never_commits(self) -> None:
        connection = FakeConnection(fail_on="INSERT INTO option_analytics")

        with self.assertRaisesRegex(RuntimeError, "injected"):
            SnapshotRepository(connection).write_pricing_atomic(
                pricing_run(), (calculated_row(),)
            )

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)


class PricedReaderTests(TestCase):
    def test_readers_filter_to_completed_runs(self) -> None:
        connection = FakeConnection()
        repository = SnapshotRepository(connection)
        snapshot_id = uuid4()
        identity = ContractIdentity(date(2026, 9, 24), 22000, "call")

        repository.list_priced_snapshots()
        repository.get_priced_snapshot_meta(snapshot_id)
        repository.get_analytics_curve(snapshot_id, identity.expiry)
        repository.get_analytics_contract(snapshot_id, identity)
        repository.list_smiles(snapshot_id)
        repository.get_smile(snapshot_id, identity.expiry)

        self.assertEqual(len(connection.statements), 6)
        for sql, _ in connection.statements:
            self.assertIn("status = 'completed'", sql)

    def test_analytics_curve_defaults_to_calculated_only(self) -> None:
        connection = FakeConnection()
        SnapshotRepository(connection).get_analytics_curve(uuid4(), date(2026, 9, 24))
        self.assertIn(
            "calculation_status = 'calculated'", connection.statements[0][0]
        )

    def test_analytics_curve_can_include_excluded_rows(self) -> None:
        connection = FakeConnection()
        SnapshotRepository(connection).get_analytics_curve(
            uuid4(), date(2026, 9, 24), calculated_only=False
        )
        self.assertNotIn(
            "calculation_status = 'calculated'", connection.statements[0][0]
        )

    def test_priced_meta_maps_row(self) -> None:
        connection = FakeConnection(
            rows=[
                {
                    "snapshot_id": uuid4(),
                    "collected_at": datetime(2026, 9, 4, 10, tzinfo=UTC),
                    "priced_at": datetime(2026, 9, 4, 10, 5, tzinfo=UTC),
                    "spot": 22000.0,
                    "risk_free_rate": 0.065,
                    "dividend_yield": 0.0,
                    "model_name": "black_scholes_merton",
                    "assumptions": {"day_count": "ACT/365F"},
                    "thresholds": {"solver_tolerance": 1e-6},
                    "contract_count": 42,
                }
            ]
        )
        meta = SnapshotRepository(connection).get_priced_snapshot_meta(uuid4())
        assert meta is not None
        self.assertEqual(meta.contract_count, 42)
        self.assertEqual(meta.priced_at.tzinfo, UTC)


if __name__ == "__main__":  # pragma: no cover
    from unittest import main

    main()
