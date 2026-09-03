"""Configurable, paced and resilient NSE HTTP client."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message
from http.cookiejar import CookieJar
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from .config import CollectorConfig
from .errors import (
    AuthorizationError,
    HTTPStatusError,
    MalformedJSONError,
    RateLimitError,
    ResponseContentTypeError,
    TransportError,
)
from .models import OptionRecord
from .parser import parse_option_chain


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class Transport(Protocol):
    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> HTTPResponse:
        """Return an HTTP response; status failures remain response values."""


class UrllibTransport:
    """Cookie-preserving standard-library transport."""

    def __init__(self) -> None:
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> HTTPResponse:
        request = Request(url, headers=dict(headers), method="GET")
        try:
            response = self._opener.open(request, timeout=timeout)
            with response:
                return HTTPResponse(
                    response.status, dict(response.headers), response.read()
                )
        except HTTPError as exc:
            with exc:
                return HTTPResponse(exc.code, dict(exc.headers), exc.read())
        except (URLError, TimeoutError, OSError) as exc:
            raise TransportError(f"NSE request failed: {exc}") from exc


class NSEClient:
    """Own an NSE browser session and retrieve normalized option records."""

    def __init__(
        self,
        config: CollectorConfig | None = None,
        *,
        transport_factory: Callable[[], Transport] = UrllibTransport,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.config = config or CollectorConfig()
        self._transport_factory = transport_factory
        self._transport = transport_factory()
        self._sleep = sleep
        self._monotonic = monotonic
        self._now = now
        self._last_request_at: float | None = None
        self._bootstrapped = False

    def _pace(self) -> None:
        if self._last_request_at is not None:
            elapsed = self._monotonic() - self._last_request_at
            delay = self.config.min_request_interval_seconds - elapsed
            if delay > 0:
                self._sleep(delay)

    def _get(self, url: str) -> HTTPResponse:
        self._pace()
        try:
            return self._transport.get(
                url, self.config.headers, self.config.timeout_seconds
            )
        finally:
            self._last_request_at = self._monotonic()

    def _rebuild_session(self) -> None:
        self._transport = self._transport_factory()
        self._bootstrapped = False
        self._bootstrap()

    def _bootstrap(self) -> None:
        response = self._request_with_retries(self.config.bootstrap_url)
        if response.status in (401, 403):
            raise AuthorizationError(
                f"NSE session bootstrap was rejected with HTTP {response.status}"
            )
        if not 200 <= response.status < 300:
            raise HTTPStatusError(
                response.status,
                f"NSE session bootstrap failed with HTTP {response.status}",
            )
        self._bootstrapped = True

    def _retry_delay(self, response: HTTPResponse | None, retry: int) -> float:
        if response is not None and response.status == 429:
            value = response.headers.get("Retry-After")
            try:
                if value is not None:
                    return min(float(value), self.config.max_retry_after_seconds)
            except ValueError:
                pass
        return self.config.retry_backoff_seconds * (2**retry)

    def _request_with_retries(self, url: str) -> HTTPResponse:
        last_transport_error: TransportError | None = None
        response: HTTPResponse | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self._get(url)
                last_transport_error = None
            except TransportError as exc:
                last_transport_error = exc
                response = None
            if last_transport_error is None and response is not None:
                if response.status != 429 and not 500 <= response.status < 600:
                    return response
            if attempt < self.config.max_retries:
                self._sleep(self._retry_delay(response, attempt))

        if last_transport_error is not None:
            raise TransportError(
                f"NSE request failed after {self.config.max_retries + 1} attempts"
            ) from last_transport_error
        assert response is not None
        if response.status == 429:
            raise RateLimitError(
                f"NSE rate limit persisted after {self.config.max_retries + 1} attempts"
            )
        return response

    def fetch_option_chain(self) -> list[OptionRecord]:
        """Fetch and validate NIFTY's chain, rebuilding auth at most once."""

        if not self._bootstrapped:
            self._bootstrap()

        response = self._request_with_retries(self.config.option_chain_url)
        if response.status in (401, 403):
            self._rebuild_session()
            response = self._request_with_retries(self.config.option_chain_url)
            if response.status in (401, 403):
                raise AuthorizationError(
                    "NSE rejected the option-chain request after one session rebuild "
                    f"(HTTP {response.status})"
                )
        if not 200 <= response.status < 300:
            raise HTTPStatusError(response.status)

        content_type = response.headers.get("Content-Type", "")
        message = Message()
        message["content-type"] = content_type
        if message.get_content_type().lower() != "application/json":
            raise ResponseContentTypeError(
                "NSE option-chain response must be application/json; got "
                f"{content_type or 'no Content-Type'}"
            )
        try:
            payload = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MalformedJSONError(
                "NSE option-chain response contained malformed JSON"
            ) from exc
        return parse_option_chain(
            payload, fetched_at=self._now(), symbol=self.config.symbol
        )
