from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from nifty_vol.collector import OptionRecord
from nifty_vol.pipeline import (
    select_market_price,
    storage_expiry_date,
)


def record(**changes) -> OptionRecord:
    item = OptionRecord(
        symbol="NIFTY",
        observed_at=datetime(2026, 9, 4, 9, 59, tzinfo=UTC),
        expiry=datetime(2026, 9, 10, 10, tzinfo=UTC),
        strike=25_100,
        option_type="call",
        underlying_spot=25_123.4,
        last_price=180.5,
        bid=180.1,
        ask=180.9,
        volume=12_500,
        open_interest=45_000,
    )
    return replace(item, **changes)


def test_midpoint_is_preferred_and_invalid_spread_falls_back_to_ltp() -> None:
    assert select_market_price(record()).value == pytest.approx(180.5)
    assert select_market_price(record()).source == "midpoint"
    fallback = select_market_price(record(bid=181, ask=180))
    assert fallback.value == 180.5
    assert fallback.source == "last_traded_price"

def test_expiry_date_is_taken_in_exchange_timezone() -> None:
    instant = datetime(2026, 9, 9, 20, 0, tzinfo=UTC)
    assert storage_expiry_date(instant).isoformat() == "2026-09-10"
    with pytest.raises(ValueError, match="timezone-aware"):
        storage_expiry_date(datetime(2026, 9, 10))
