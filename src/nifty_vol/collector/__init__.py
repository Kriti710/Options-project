"""NSE market-data collection boundary."""

from .client import NSEClient, UrllibTransport
from .config import CollectorConfig
from .errors import (
    AuthorizationError,
    CollectionError,
    EmptyChainError,
    HTTPStatusError,
    MalformedJSONError,
    RateLimitError,
    ResponseContentTypeError,
    SchemaError,
    TransportError,
)
from .models import OptionRecord
from .parser import parse_option_chain

__all__ = [
    "AuthorizationError",
    "CollectionError",
    "CollectorConfig",
    "EmptyChainError",
    "HTTPStatusError",
    "MalformedJSONError",
    "NSEClient",
    "OptionRecord",
    "RateLimitError",
    "ResponseContentTypeError",
    "SchemaError",
    "TransportError",
    "UrllibTransport",
    "parse_option_chain",
]
