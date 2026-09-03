"""Explicit errors exposed by the collection boundary."""


class CollectionError(RuntimeError):
    """Base class for failures safe for callers to handle as collection errors."""


class TransportError(CollectionError):
    """The server could not be reached after bounded retries."""


class AuthorizationError(CollectionError):
    """NSE rejected both the original and rebuilt browser session."""


class RateLimitError(CollectionError):
    """NSE continued to rate-limit requests after bounded retries."""


class HTTPStatusError(CollectionError):
    """NSE returned an unexpected non-success HTTP status."""

    def __init__(self, status: int, message: str | None = None) -> None:
        self.status = status
        super().__init__(message or f"NSE returned unexpected HTTP status {status}")


class ResponseContentTypeError(CollectionError):
    """The option-chain endpoint did not return JSON content."""


class MalformedJSONError(CollectionError):
    """The option-chain response declared JSON but could not be decoded."""


class SchemaError(CollectionError):
    """The decoded response did not contain NSE's required chain schema."""


class EmptyChainError(CollectionError):
    """The response schema was valid but contained no option contracts."""
