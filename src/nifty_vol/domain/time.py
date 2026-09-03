"""NIFTY expiry instants and ACT/365F year fractions."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone

EXPIRY_TIME = time(15, 30)
_KOLKATA = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")
_SECONDS_PER_365_DAY_YEAR = 365 * 24 * 60 * 60


def expiry_instant(expiry_date: date) -> datetime:
    """Construct the NIFTY expiry instant and return it in UTC."""

    if not isinstance(expiry_date, date) or isinstance(expiry_date, datetime):
        raise TypeError("expiry_date must be a date")
    local_expiry = datetime.combine(expiry_date, EXPIRY_TIME, tzinfo=_KOLKATA)
    return local_expiry.astimezone(UTC)


def time_to_expiry(*, expiry_date: date, as_of: datetime) -> float:
    """Return exact ACT/365F time from a timezone-aware instant to expiry."""

    if not isinstance(as_of, datetime):
        raise TypeError("as_of must be a datetime")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    elapsed_seconds = (expiry_instant(expiry_date) - as_of).total_seconds()
    return elapsed_seconds / _SECONDS_PER_365_DAY_YEAR
