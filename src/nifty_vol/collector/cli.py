"""One-shot collector command composition."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from nifty_vol.collector.client import NSEClient
from nifty_vol.collector.models import OptionRecord
from nifty_vol.pipeline import PipelineConfig, storage_expiry_date
from nifty_vol.pricer import price_snapshot
from nifty_vol.settings import EnvironmentConfig
from nifty_vol.storage import (
    ContractIdentity,
    OptionAnalytics,
    PricingRun,
    PricingSmile,
    RawCollectionRun,
    RawOptionObservation,
    SnapshotRepository,
)


class ChainClient(Protocol):
    def fetch_option_chain(self) -> list[OptionRecord]: ...


class CountedChainClient(ChainClient, Protocol):
    @property
    def attempt_count(self) -> int: ...


class RawSnapshotWriter(Protocol):
    def write_collection_atomic(self, run: RawCollectionRun) -> UUID: ...


class PricingWriter(Protocol):
    def write_pricing_atomic(
        self,
        run: PricingRun,
        rows: tuple[OptionAnalytics, ...],
        smiles: tuple[PricingSmile, ...] = (),
    ) -> UUID: ...


def build_raw_collection_run(
    records: list[OptionRecord], *, attempt_count: int
) -> RawCollectionRun:
    """Project normalized NSE records into the collector-owned storage model."""

    if not records:
        raise ValueError("at least one option record is required")
    symbols = {item.symbol for item in records}
    spots = {item.underlying_spot for item in records}
    if len(symbols) != 1 or len(spots) != 1:
        raise ValueError("one snapshot must contain one symbol and one spot value")
    return RawCollectionRun(
        collected_at=max(item.observed_at.astimezone(UTC) for item in records),
        spot=records[0].underlying_spot,
        attempt_count=attempt_count,
        observations=tuple(
            RawOptionObservation(
                identity=ContractIdentity(
                    expiry=storage_expiry_date(item.expiry),
                    strike=item.strike,
                    option_type=item.option_type,
                ),
                last_traded_price=item.last_price,
                bid=item.bid,
                ask=item.ask,
                volume=item.volume,
                open_interest=item.open_interest,
                nse_iv=item.nse_iv,
            )
            for item in records
        ),
    )


def collect_and_price_once(
    client: CountedChainClient,
    collector_writer: RawSnapshotWriter,
    pricer_writer: PricingWriter,
    config: PipelineConfig,
    *,
    priced_at: datetime | None = None,
) -> UUID:
    """Fetch once, persist raw quotes, then publish computed pricing atomically."""

    records = client.fetch_option_chain()
    raw = build_raw_collection_run(records, attempt_count=client.attempt_count)
    collector_writer.write_collection_atomic(raw)
    priced = price_snapshot(
        snapshot_id=raw.snapshot_id,
        priced_at=priced_at or datetime.now(UTC),
        records=records,
        config=config,
    )
    return pricer_writer.write_pricing_atomic(
        priced.run, priced.analytics, priced.smiles
    )


def main() -> None:
    """Run one configured collection; scheduling remains an external concern."""

    settings = EnvironmentConfig.from_env()
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - packaging failure
        raise RuntimeError("psycopg is required by the collector CLI") from exc

    try:
        with (
            psycopg.connect(settings.collector_database_url) as collector_connection,
            psycopg.connect(settings.pricer_database_url) as pricer_connection,
        ):
            snapshot_id = collect_and_price_once(
                NSEClient(settings.collector),
                SnapshotRepository(collector_connection),
                SnapshotRepository(pricer_connection),
                settings.pipeline,
            )
    except psycopg.Error:
        raise RuntimeError("collection/pricing database operation failed") from None
    print(snapshot_id)


if __name__ == "__main__":  # pragma: no cover
    main()
