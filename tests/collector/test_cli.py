from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, call, patch
from uuid import UUID

import psycopg
import pytest

from nifty_vol.collector import OptionRecord
from nifty_vol.collector.cli import collect_and_price_once, main
from nifty_vol.pipeline import PipelineConfig


def test_cli_uses_separate_collector_and_pricer_connections(capsys) -> None:
    collector_url = "postgresql://collector:secret@db.example/data"
    pricer_url = "postgresql://pricer:secret@db.example/data"
    collector_connection = MagicMock()
    pricer_connection = MagicMock()
    collector_context = MagicMock()
    pricer_context = MagicMock()
    collector_context.__enter__.return_value = collector_connection
    pricer_context.__enter__.return_value = pricer_connection
    snapshot_id = UUID("12345678-1234-5678-1234-567812345678")
    environment = {
        "COLLECTOR_DATABASE_URL": collector_url,
        "PRICER_DATABASE_URL": pricer_url,
        "RISK_FREE_RATE_DECIMAL": "0.065",
        "DIVIDEND_YIELD_DECIMAL": "0.01",
    }

    with (
        patch.dict("os.environ", environment, clear=True),
        patch(
            "psycopg.connect", side_effect=[collector_context, pricer_context]
        ) as connect,
        patch("nifty_vol.collector.cli.NSEClient"),
        patch(
            "nifty_vol.collector.cli.collect_and_price_once",
            return_value=snapshot_id,
        ),
    ):
        main()

    connect.assert_has_calls([call(collector_url), call(pricer_url)])
    assert capsys.readouterr().out.strip() == str(snapshot_id)


def test_cli_database_failure_does_not_expose_credentials() -> None:
    database_url = "postgresql://writer:super-secret@db.example/data"
    environment = {
        "COLLECTOR_DATABASE_URL": database_url,
        "PRICER_DATABASE_URL": "postgresql://pricer:secret@db.example/data",
        "RISK_FREE_RATE_DECIMAL": "0.065",
        "DIVIDEND_YIELD_DECIMAL": "0.01",
    }
    with (
        patch.dict("os.environ", environment, clear=True),
        patch("psycopg.connect", side_effect=psycopg.OperationalError(database_url)),
        pytest.raises(
            RuntimeError, match="collection/pricing database operation failed"
        ) as exc,
    ):
        main()

    assert "super-secret" not in str(exc.value)


class FixtureClient:
    attempt_count = 3

    def fetch_option_chain(self) -> list[OptionRecord]:
        return [
            OptionRecord(
                symbol="NIFTY",
                observed_at=datetime(2026, 9, 7, 4, tzinfo=UTC),
                expiry=datetime(2026, 9, 24, 10, tzinfo=UTC),
                strike=25000.0,
                option_type="call",
                underlying_spot=25000.0,
                last_price=500.0,
                bid=499.0,
                ask=501.0,
                volume=100,
                open_interest=1000,
                nse_iv=0.18,
            )
        ]


def test_collect_and_price_publishes_raw_before_analytics() -> None:
    events: list[tuple[str, object]] = []
    collector = MagicMock()
    pricer = MagicMock()
    collector.write_collection_atomic.side_effect = (
        lambda run: events.append(("raw", run)) or run.snapshot_id
    )
    pricer.write_pricing_atomic.side_effect = (
        lambda run, rows, smiles: events.append(("pricing", (run, rows, smiles)))
        or run.snapshot_id
    )

    snapshot_id = collect_and_price_once(
        FixtureClient(),
        collector,
        pricer,
        PipelineConfig(risk_free_rate=0.06, dividend_yield=0.01),
        priced_at=datetime(2026, 9, 7, 4, 1, tzinfo=UTC),
    )

    assert [event[0] for event in events] == ["raw", "pricing"]
    raw = events[0][1]
    pricing_run, analytics, smiles = events[1][1]
    assert raw.attempt_count == 3
    assert raw.snapshot_id == snapshot_id == pricing_run.snapshot_id
    assert len(raw.observations) == len(analytics) == 1
    assert raw.observations[0].nse_iv == 0.18
    assert smiles == ()
