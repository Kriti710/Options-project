"""Synchronous DB-API repository for PostgreSQL.

The connection is supplied by the application.  A psycopg 3 connection is the
intended production implementation; keeping construction outside this module
also makes the boundary testable without network access.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from .models import (
    ContractIdentity,
    OptionAnalytics,
    PricedSnapshotMeta,
    PricingRun,
    PricingSmile,
    RawCollectionRun,
    utc_datetime,
)


class Cursor(Protocol):
    description: Sequence[Sequence[Any]] | None

    def execute(self, query: str, params: Sequence[Any] = ()) -> Any: ...
    def executemany(self, query: str, params: Iterable[Sequence[Any]]) -> Any: ...
    def fetchall(self) -> list[Any]: ...
    def fetchone(self) -> Any | None: ...
    def close(self) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _row_mapping(cursor: Cursor, row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if cursor.description is None:
        raise RuntimeError("query did not return a row description")
    return dict(zip((column[0] for column in cursor.description), row, strict=True))


class SnapshotRepository:
    """Read completed snapshots and atomically publish new ones."""

    def __init__(self, connection: Connection):
        self._connection = connection

    def list_expiries(self, snapshot_id: UUID) -> list[date]:
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                "SELECT DISTINCT o.expiry FROM option_observations o "
                "JOIN collection_runs r ON r.snapshot_id = o.snapshot_id "
                "WHERE o.snapshot_id = %s AND r.status = 'completed' "
                "ORDER BY o.expiry",
                (snapshot_id,),
            )
            return [row["expiry"] if isinstance(row, Mapping) else row[0]
                    for row in cursor.fetchall()]
        finally:
            cursor.close()

    # -- Raw collection write path: collector role ---------------------------

    def write_collection_atomic(self, run: RawCollectionRun) -> UUID:
        """Publish a raw collection snapshot in one transaction.

        Inserts `collection_runs` (in_progress, pricing columns left NULL) and
        the raw `option_observations`, then promotes to `completed`. Any row
        failure rolls the whole snapshot back.
        """
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO collection_runs "
                "(snapshot_id, collected_at, status, spot, attempt_count) "
                "VALUES (%s, %s, 'in_progress', %s, %s)",
                (run.snapshot_id, run.collected_at, run.spot, run.attempt_count),
            )
            cursor.executemany(
                "INSERT INTO option_observations "
                "(snapshot_id, expiry, strike, option_type, "
                "last_traded_price, bid, ask, volume, open_interest, nse_iv) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    (
                        run.snapshot_id,
                        item.identity.expiry,
                        item.identity.strike,
                        item.identity.option_type,
                        item.last_traded_price,
                        item.bid,
                        item.ask,
                        item.volume,
                        item.open_interest,
                        item.nse_iv,
                    )
                    for item in run.observations
                ),
            )
            cursor.execute(
                "UPDATE collection_runs SET status = 'completed', completed_at = now() "
                "WHERE snapshot_id = %s AND status = 'in_progress'",
                (run.snapshot_id,),
            )
            self._connection.commit()
            return run.snapshot_id
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def record_failed_collection(
        self,
        snapshot_id: UUID,
        collected_at: datetime,
        attempt_count: int,
        failure_diagnostics: str,
    ) -> UUID:
        """Record a failed collection: a `failed` run with no option rows.

        Never writes option rows and never touches the last completed snapshot.
        """
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO collection_runs (snapshot_id, collected_at, status, "
                "attempt_count, failure_diagnostics) "
                "VALUES (%s, %s, 'failed', %s, %s)",
                (
                    snapshot_id,
                    utc_datetime(collected_at, "collected_at"),
                    attempt_count,
                    failure_diagnostics,
                ),
            )
            self._connection.commit()
            return snapshot_id
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    # -- Pricer writes, reader reads ------------------------------------------

    _PRICED_META_COLUMNS = """
        r.snapshot_id, r.collected_at, p.priced_at, r.spot,
        p.risk_free_rate, p.dividend_yield, p.model_name,
        p.assumptions, p.thresholds,
        (SELECT count(*) FROM option_analytics a
         WHERE a.snapshot_id = r.snapshot_id) AS contract_count
    """

    def list_priced_snapshots(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[PricedSnapshotMeta]:
        if limit <= 0 or offset < 0:
            raise ValueError("limit must be positive and offset non-negative")
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                f"SELECT {self._PRICED_META_COLUMNS} FROM collection_runs r "
                "JOIN pricing_runs p ON p.snapshot_id = r.snapshot_id "
                "WHERE r.status = 'completed' "
                "ORDER BY r.collected_at DESC LIMIT %s OFFSET %s",
                (limit, offset),
            )
            return [self._priced_meta(cursor, row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def get_priced_snapshot_meta(
        self, snapshot_id: UUID
    ) -> PricedSnapshotMeta | None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                f"SELECT {self._PRICED_META_COLUMNS} FROM collection_runs r "
                "JOIN pricing_runs p ON p.snapshot_id = r.snapshot_id "
                "WHERE r.snapshot_id = %s AND r.status = 'completed'",
                (snapshot_id,),
            )
            row = cursor.fetchone()
            return None if row is None else self._priced_meta(cursor, row)
        finally:
            cursor.close()

    def get_analytics_curve(
        self,
        snapshot_id: UUID,
        expiry: date,
        *,
        calculated_only: bool = True,
    ) -> list[OptionAnalytics]:
        status_filter = (
            " AND a.calculation_status = 'calculated'" if calculated_only else ""
        )
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                "SELECT a.* FROM option_analytics a "
                "JOIN collection_runs r ON r.snapshot_id = a.snapshot_id "
                "WHERE a.snapshot_id = %s AND a.expiry = %s "
                f"AND r.status = 'completed'{status_filter} "
                "ORDER BY a.strike, a.option_type",
                (snapshot_id, expiry),
            )
            return [self._analytics(cursor, row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def get_analytics_contract(
        self, snapshot_id: UUID, identity: ContractIdentity
    ) -> OptionAnalytics | None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                "SELECT a.* FROM option_analytics a "
                "JOIN collection_runs r ON r.snapshot_id = a.snapshot_id "
                "WHERE a.snapshot_id = %s AND a.expiry = %s "
                "AND a.strike = %s AND a.option_type = %s "
                "AND r.status = 'completed'",
                (snapshot_id, identity.expiry, identity.strike, identity.option_type),
            )
            row = cursor.fetchone()
            return None if row is None else self._analytics(cursor, row)
        finally:
            cursor.close()

    def write_pricing_atomic(
        self,
        run: PricingRun,
        rows: Sequence[OptionAnalytics],
        smiles: Sequence[PricingSmile] = (),
    ) -> UUID:
        """Publish one pricing pass in a single transaction.

        The matching `collection_runs` row must already exist. Every analytics
        row and fitted smile is inserted or the whole pass rolls back.
        """
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO pricing_runs (snapshot_id, priced_at, risk_free_rate, "
                "dividend_yield, model_name, assumptions, thresholds) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
                (
                    run.snapshot_id,
                    run.priced_at,
                    run.risk_free_rate,
                    run.dividend_yield,
                    run.model_name,
                    json.dumps(dict(run.assumptions), sort_keys=True),
                    json.dumps(dict(run.thresholds), sort_keys=True),
                ),
            )
            if smiles:
                cursor.executemany(
                    "INSERT INTO pricing_smiles (snapshot_id, expiry, forward, "
                    "c0, c1, c2, sample_size, residual_scale) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        (
                            run.snapshot_id, smile.expiry, smile.forward,
                            smile.c0, smile.c1, smile.c2,
                            smile.sample_size, smile.residual_scale,
                        )
                        for smile in smiles
                    ),
                )
            cursor.executemany(
                "INSERT INTO option_analytics "
                "(snapshot_id, expiry, strike, option_type, selected_price, "
                "price_source, forward, time_to_expiry, calculation_status, "
                "exclusion_reason, implied_volatility, delta, gamma, vega, theta, "
                "fitted_iv, iv_residual, richness_price, richness_z, valuation) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s)",
                (
                    self._analytics_params(run.snapshot_id, item)
                    for item in rows
                ),
            )
            self._connection.commit()
            return run.snapshot_id
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def list_smiles(self, snapshot_id: UUID) -> list[PricingSmile]:
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                "SELECT s.* FROM pricing_smiles s "
                "JOIN collection_runs r ON r.snapshot_id = s.snapshot_id "
                "WHERE s.snapshot_id = %s AND r.status = 'completed' "
                "ORDER BY s.expiry",
                (snapshot_id,),
            )
            return [self._smile(cursor, row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def get_smile(
        self, snapshot_id: UUID, expiry: date
    ) -> PricingSmile | None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                "SELECT s.* FROM pricing_smiles s "
                "JOIN collection_runs r ON r.snapshot_id = s.snapshot_id "
                "WHERE s.snapshot_id = %s AND s.expiry = %s "
                "AND r.status = 'completed'",
                (snapshot_id, expiry),
            )
            row = cursor.fetchone()
            return None if row is None else self._smile(cursor, row)
        finally:
            cursor.close()

    def _smile(self, cursor: Cursor, row: Any) -> PricingSmile:
        item = _row_mapping(cursor, row)
        return PricingSmile(
            expiry=item["expiry"],
            forward=item["forward"],
            c0=item["c0"],
            c1=item["c1"],
            c2=item["c2"],
            sample_size=item["sample_size"],
            residual_scale=item["residual_scale"],
        )

    def _priced_meta(self, cursor: Cursor, row: Any) -> PricedSnapshotMeta:
        item = _row_mapping(cursor, row)
        return PricedSnapshotMeta(
            snapshot_id=item["snapshot_id"],
            collected_at=utc_datetime(item["collected_at"], "collected_at"),
            priced_at=utc_datetime(item["priced_at"], "priced_at"),
            spot=item["spot"],
            risk_free_rate=item["risk_free_rate"],
            dividend_yield=item["dividend_yield"],
            model_name=item["model_name"],
            assumptions=_json_value(item["assumptions"]),
            thresholds=_json_value(item["thresholds"]),
            contract_count=item["contract_count"],
        )

    def _analytics(self, cursor: Cursor, row: Any) -> OptionAnalytics:
        item = _row_mapping(cursor, row)
        return OptionAnalytics(
            identity=ContractIdentity(
                expiry=item["expiry"],
                strike=item["strike"],
                option_type=item["option_type"],
            ),
            calculation_status=item["calculation_status"],
            selected_price=item["selected_price"],
            price_source=item["price_source"],
            forward=item["forward"],
            time_to_expiry=item["time_to_expiry"],
            exclusion_reason=item["exclusion_reason"],
            implied_volatility=item["implied_volatility"],
            delta=item["delta"],
            gamma=item["gamma"],
            vega=item["vega"],
            theta=item["theta"],
            fitted_iv=item["fitted_iv"],
            iv_residual=item["iv_residual"],
            richness_price=item["richness_price"],
            richness_z=item["richness_z"],
            valuation=item["valuation"],
        )

    @staticmethod
    def _analytics_params(
        snapshot_id: UUID, item: OptionAnalytics
    ) -> tuple[Any, ...]:
        identity = item.identity
        return (
            snapshot_id, identity.expiry, identity.strike, identity.option_type,
            item.selected_price, item.price_source, item.forward,
            item.time_to_expiry, item.calculation_status, item.exclusion_reason,
            item.implied_volatility, item.delta, item.gamma, item.vega, item.theta,
            item.fitted_iv, item.iv_residual, item.richness_price, item.richness_z,
            item.valuation,
        )
