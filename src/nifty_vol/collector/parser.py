"""Strict conversion of NSE option-chain JSON to normalized raw records."""

from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import EmptyChainError, SchemaError
from .models import OptionRecord

try:
    _INDIA = ZoneInfo("Asia/Kolkata")
except ZoneInfoNotFoundError:
    # India has observed UTC+05:30 without daylight saving since 1945. This
    # keeps collection usable on Windows installations without the tzdata wheel.
    _INDIA = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")


def _number(value: Any, field: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaError(f"NSE field {field!r} must be numeric")
    return float(value)


def _integer(value: Any, field: str) -> int:
    number = _number(value, field)
    assert number is not None
    if number < 0 or not number.is_integer():
        raise SchemaError(f"NSE field {field!r} must be a non-negative integer")
    return int(number)


def _expiry(value: Any) -> datetime:
    if not isinstance(value, str):
        raise SchemaError("NSE field 'expiryDate' must be a date string")
    try:
        day = datetime.strptime(value, "%d-%b-%Y").date()
    except ValueError as exc:
        raise SchemaError(f"invalid NSE expiryDate {value!r}") from exc
    local_expiry = datetime.combine(day, time(15, 30), tzinfo=_INDIA)
    return local_expiry.astimezone(timezone.utc)


def _observed_at(payload: dict[str, Any], fallback: datetime) -> datetime:
    stamp = payload.get("records", {}).get("timestamp")
    if not isinstance(stamp, str):
        return fallback
    for pattern in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            local = datetime.strptime(stamp, pattern).replace(tzinfo=_INDIA)
            return local.astimezone(timezone.utc)
        except ValueError:
            continue
    raise SchemaError(f"invalid NSE records.timestamp {stamp!r}")


def parse_option_chain(
    payload: Any,
    *,
    fetched_at: datetime,
    symbol: str = "NIFTY",
) -> list[OptionRecord]:
    """Parse a decoded NSE response, failing loudly on upstream schema drift."""

    if fetched_at.utcoffset() is None:
        raise ValueError("fetched_at must be timezone-aware")
    fetched_at = fetched_at.astimezone(timezone.utc)
    if not isinstance(payload, dict):
        raise SchemaError("NSE response root must be an object")
    records = payload.get("records")
    if not isinstance(records, dict):
        raise SchemaError("NSE response is missing object 'records'")
    data = records.get("data")
    if not isinstance(data, list):
        raise SchemaError("NSE response is missing array 'records.data'")
    spot = _number(records.get("underlyingValue"), "records.underlyingValue")
    assert spot is not None
    observed_at = _observed_at(payload, fetched_at)

    result: list[OptionRecord] = []
    for index, row in enumerate(data):
        if not isinstance(row, dict):
            raise SchemaError(f"records.data[{index}] must be an object")
        strike = _number(row.get("strikePrice"), f"records.data[{index}].strikePrice")
        assert strike is not None
        expiry_value = row.get("expiryDate")

        for nse_key, option_type in (("CE", "call"), ("PE", "put")):
            quote = row.get(nse_key)
            if quote is None:
                continue
            if not isinstance(quote, dict):
                raise SchemaError(f"records.data[{index}].{nse_key} must be an object")
            expiry = _expiry(quote.get("expiryDate", expiry_value))
            result.append(
                OptionRecord(
                    symbol=symbol,
                    observed_at=observed_at,
                    expiry=expiry,
                    strike=strike,
                    option_type=option_type,  # type: ignore[arg-type]
                    underlying_spot=spot,
                    last_price=_number(
                        quote.get("lastPrice"), f"{nse_key}.lastPrice", optional=True
                    ),
                    bid=_number(
                        quote.get("bidprice"), f"{nse_key}.bidprice", optional=True
                    ),
                    ask=_number(
                        quote.get("askPrice"), f"{nse_key}.askPrice", optional=True
                    ),
                    volume=_integer(
                        quote.get("totalTradedVolume"),
                        f"{nse_key}.totalTradedVolume",
                    ),
                    open_interest=_integer(
                        quote.get("openInterest"), f"{nse_key}.openInterest"
                    ),
                )
            )

    if not result:
        raise EmptyChainError("NSE option chain contained no call or put contracts")
    return result
