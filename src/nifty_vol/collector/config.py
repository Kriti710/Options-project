"""Configuration for polite access to NSE's public option-chain endpoint."""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


def _default_headers() -> Mapping[str, str]:
    return MappingProxyType(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-IN,en;q=0.9",
            "Referer": "https://www.nseindia.com/option-chain",
            "Connection": "keep-alive",
        }
    )


@dataclass(frozen=True, slots=True)
class CollectorConfig:
    """HTTP behaviour; every network policy value is caller-configurable."""

    base_url: str = "https://www.nseindia.com"
    option_chain_path: str = "/api/option-chain-indices"
    symbol: str = "NIFTY"
    timeout_seconds: float = 10.0
    min_request_interval_seconds: float = 1.0
    max_retries: int = 2
    retry_backoff_seconds: float = 1.0
    max_retry_after_seconds: float = 30.0
    headers: Mapping[str, str] = field(default_factory=_default_headers)

    def __post_init__(self) -> None:
        if not self.base_url.startswith("https://"):
            raise ValueError("base_url must use HTTPS")
        if not self.option_chain_path.startswith("/"):
            raise ValueError("option_chain_path must start with '/'")
        if not self.symbol:
            raise ValueError("symbol must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.min_request_interval_seconds < 0:
            raise ValueError("min_request_interval_seconds cannot be negative")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds cannot be negative")
        if self.max_retry_after_seconds < 0:
            raise ValueError("max_retry_after_seconds cannot be negative")

    @property
    def bootstrap_url(self) -> str:
        return self.base_url.rstrip("/") + "/"

    @property
    def option_chain_url(self) -> str:
        from urllib.parse import urlencode

        query = urlencode({"symbol": self.symbol})
        return self.base_url.rstrip("/") + self.option_chain_path + "?" + query
