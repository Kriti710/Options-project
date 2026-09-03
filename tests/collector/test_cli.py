from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import UUID

import psycopg
import pytest

from nifty_vol.collector.cli import main


def test_cli_uses_collector_write_connection(capsys) -> None:
    database_url = "postgresql://writer:secret@db.example/data"
    connection = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = connection
    snapshot_id = UUID("12345678-1234-5678-1234-567812345678")
    environment = {
        "COLLECTOR_DATABASE_URL": database_url,
        "RISK_FREE_RATE_DECIMAL": "0.065",
        "DIVIDEND_YIELD_DECIMAL": "0.01",
    }

    with (
        patch.dict("os.environ", environment, clear=True),
        patch("psycopg.connect", return_value=context) as connect,
        patch("nifty_vol.collector.cli.NSEClient"),
        patch("nifty_vol.collector.cli.collect_once", return_value=snapshot_id),
    ):
        main()

    connect.assert_called_once_with(database_url)
    assert capsys.readouterr().out.strip() == str(snapshot_id)


def test_cli_database_failure_does_not_expose_credentials() -> None:
    database_url = "postgresql://writer:super-secret@db.example/data"
    environment = {
        "COLLECTOR_DATABASE_URL": database_url,
        "RISK_FREE_RATE_DECIMAL": "0.065",
        "DIVIDEND_YIELD_DECIMAL": "0.01",
    }
    with (
        patch.dict("os.environ", environment, clear=True),
        patch("psycopg.connect", side_effect=psycopg.OperationalError(database_url)),
        pytest.raises(RuntimeError, match="collector database operation failed") as exc,
    ):
        main()

    assert "super-secret" not in str(exc.value)
