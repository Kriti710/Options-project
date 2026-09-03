"""Normalized records emitted by the NSE parser."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class OptionRecord:
    """One unpriced option quote, normalized independently of NSE field names."""

    symbol: str
    observed_at: datetime
    expiry: datetime
    strike: float
    option_type: Literal["call", "put"]
    underlying_spot: float
    last_price: float | None
    bid: float | None
    ask: float | None
    volume: int
    open_interest: int

    def __post_init__(self) -> None:
        if self.observed_at.utcoffset() is None or self.expiry.utcoffset() is None:
            raise ValueError("record timestamps must be timezone-aware")
