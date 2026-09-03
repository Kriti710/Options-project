"""One-shot collector command composition."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from nifty_vol.collector.client import NSEClient
from nifty_vol.collector.models import OptionRecord
from nifty_vol.pipeline import PipelineConfig, build_collection_run
from nifty_vol.settings import EnvironmentConfig
from nifty_vol.storage import CollectionRun, SnapshotRepository


class ChainClient(Protocol):
    def fetch_option_chain(self) -> list[OptionRecord]: ...


class SnapshotWriter(Protocol):
    def write_snapshot_atomic(self, run: CollectionRun) -> UUID: ...


def collect_once(
    client: ChainClient, writer: SnapshotWriter, config: PipelineConfig
) -> UUID:
    """Fetch, calculate, and atomically publish exactly one snapshot."""

    records = client.fetch_option_chain()
    return writer.write_snapshot_atomic(build_collection_run(records, config))


def main() -> None:
    """Run one configured collection; scheduling remains an external concern."""

    settings = EnvironmentConfig.from_env()
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - packaging failure
        raise RuntimeError("psycopg is required by the collector CLI") from exc

    try:
        with psycopg.connect(settings.collector_database_url) as connection:
            snapshot_id = collect_once(
                NSEClient(settings.collector),
                SnapshotRepository(connection),
                settings.pipeline,
            )
    except psycopg.Error:
        raise RuntimeError("collector database operation failed") from None
    print(snapshot_id)


if __name__ == "__main__":  # pragma: no cover
    main()
