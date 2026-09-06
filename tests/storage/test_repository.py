from datetime import date
from typing import Any
from unittest import TestCase
from uuid import uuid4

from nifty_vol.storage.repository import SnapshotRepository


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.description = None
        self.closed = False

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        self.connection.statements.append((query, params))

    def executemany(self, query: str, params: Any) -> None:
        self.connection.statements.append((query, list(params)))

    def fetchall(self) -> list[Any]:
        return self.connection.rows

    def fetchone(self) -> Any | None:
        return self.connection.rows[0] if self.connection.rows else None

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []
        self.statements: list[tuple[str, Any]] = []
        self.cursors: list[FakeCursor] = []

    def cursor(self) -> FakeCursor:
        cursor = FakeCursor(self)
        self.cursors.append(cursor)
        return cursor

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class RawReaderTests(TestCase):
    def test_list_expiries_reads_only_completed_collection_runs(self) -> None:
        expiry = date(2026, 9, 24)
        connection = FakeConnection(rows=[{"expiry": expiry}])

        result = SnapshotRepository(connection).list_expiries(uuid4())

        self.assertEqual(result, [expiry])
        self.assertIn("r.status = 'completed'", connection.statements[0][0])
        self.assertTrue(all(cursor.closed for cursor in connection.cursors))
