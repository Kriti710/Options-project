from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from nifty_vol.domain import expiry_instant, time_to_expiry


def test_expiry_is_1530_asia_kolkata_expressed_in_utc() -> None:
    assert expiry_instant(date(2026, 9, 10)) == datetime(
        2026, 9, 10, 10, 0, tzinfo=UTC
    )


def test_act_365f_uses_exact_elapsed_seconds_across_timezones() -> None:
    kolkata = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")
    as_of = datetime(2026, 9, 9, 15, 29, 30, tzinfo=kolkata)
    expected_seconds = 24 * 60 * 60 + 30

    assert time_to_expiry(expiry_date=date(2026, 9, 10), as_of=as_of) == pytest.approx(
        expected_seconds / (365 * 24 * 60 * 60)
    )


def test_time_to_expiry_is_zero_at_expiry_and_negative_afterwards() -> None:
    expiry = expiry_instant(date(2026, 9, 10))
    assert time_to_expiry(expiry_date=date(2026, 9, 10), as_of=expiry) == 0.0
    assert time_to_expiry(
        expiry_date=date(2026, 9, 10), as_of=expiry + timedelta(seconds=1)
    ) < 0.0


def test_time_to_expiry_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        time_to_expiry(
            expiry_date=date(2026, 9, 10), as_of=datetime(2026, 9, 9, 15, 30)
        )
