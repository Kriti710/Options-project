import unittest
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from nifty_vol.collector import (
    AuthorizationError,
    CollectorConfig,
    HTTPStatusError,
    MalformedJSONError,
    NSEClient,
    RateLimitError,
    ResponseContentTypeError,
    TransportError,
)
from nifty_vol.collector.client import HTTPResponse

FIXTURES = Path(__file__).parent / "fixtures"
CHAIN = (FIXTURES / "option_chain.json").read_bytes()
HTML = HTTPResponse(200, {"Content-Type": "text/html"}, b"home")
JSON = HTTPResponse(200, {"Content-Type": "application/json; charset=utf-8"}, CHAIN)


class ScriptedTransport:
    def __init__(self, events: list[HTTPResponse | Exception]) -> None:
        self.events = deque(events)
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def get(self, url: str, headers: object, timeout: float) -> HTTPResponse:
        copied_headers = dict(headers)  # type: ignore[arg-type]
        self.calls.append((url, copied_headers, timeout))
        event = self.events.popleft()
        if isinstance(event, Exception):
            raise event
        return event


class Factory:
    def __init__(self, scripts: list[list[HTTPResponse | Exception]]) -> None:
        self.scripts = deque(scripts)
        self.transports: list[ScriptedTransport] = []

    def __call__(self) -> ScriptedTransport:
        transport = ScriptedTransport(self.scripts.popleft())
        self.transports.append(transport)
        return transport


def config(**changes: object) -> CollectorConfig:
    values = {
        "timeout_seconds": 4.5,
        "min_request_interval_seconds": 0,
        "max_retries": 0,
        **changes,
    }
    return CollectorConfig(**values)  # type: ignore[arg-type]


class NSEClientTest(unittest.TestCase):
    def test_bootstraps_session_and_returns_records(self) -> None:
        factory = Factory([[HTML, JSON]])
        client = NSEClient(
            config(),
            transport_factory=factory,
            now=lambda: datetime(2026, 9, 4, tzinfo=UTC),
        )

        records = client.fetch_option_chain()

        self.assertEqual(len(records), 3)
        calls = factory.transports[0].calls
        self.assertEqual(calls[0][0], "https://www.nseindia.com/")
        self.assertIn("symbol=NIFTY", calls[1][0])
        self.assertEqual(calls[1][2], 4.5)
        self.assertIn("Mozilla/5.0", calls[1][1]["User-Agent"])
        self.assertNotIn("Cookie", calls[1][1])

    def test_paces_every_request(self) -> None:
        clock = iter([0.0, 0.0, 0.2, 1.0])
        sleeps: list[float] = []
        client = NSEClient(
            config(min_request_interval_seconds=1.0),
            transport_factory=Factory([[HTML, JSON]]),
            monotonic=lambda: next(clock),
            sleep=sleeps.append,
        )

        client.fetch_option_chain()

        self.assertEqual(sleeps, [1.0])

    def test_retries_transport_failure_with_bounded_backoff(self) -> None:
        sleeps: list[float] = []
        factory = Factory(
            [[HTML, TransportError("offline"), TransportError("offline"), JSON]]
        )
        client = NSEClient(
            config(max_retries=2, retry_backoff_seconds=0.25),
            transport_factory=factory,
            sleep=sleeps.append,
        )

        self.assertEqual(len(client.fetch_option_chain()), 3)
        self.assertEqual(sleeps, [0.25, 0.5])

    def test_retries_transient_server_status(self) -> None:
        unavailable = HTTPResponse(503, {"Content-Type": "text/plain"}, b"later")
        client = NSEClient(
            config(max_retries=1),
            transport_factory=Factory([[HTML, unavailable, JSON]]),
            sleep=lambda _: None,
        )

        self.assertEqual(len(client.fetch_option_chain()), 3)

    def test_transport_failure_exhaustion_has_clear_error(self) -> None:
        client = NSEClient(
            config(max_retries=1),
            transport_factory=Factory(
                [[HTML, TransportError("offline"), TransportError("offline")]]
            ),
            sleep=lambda _: None,
        )

        with self.assertRaisesRegex(TransportError, "after 2 attempts"):
            client.fetch_option_chain()

    def test_rate_limit_honors_capped_retry_after_then_fails(self) -> None:
        limited = HTTPResponse(429, {"Retry-After": "120"}, b"")
        sleeps: list[float] = []
        client = NSEClient(
            config(max_retries=1, max_retry_after_seconds=3),
            transport_factory=Factory([[HTML, limited, limited]]),
            sleep=sleeps.append,
        )

        with self.assertRaisesRegex(RateLimitError, "after 2 attempts"):
            client.fetch_option_chain()
        self.assertEqual(sleeps, [3.0])

    def test_rebuilds_session_once_after_authorization_failure(self) -> None:
        forbidden = HTTPResponse(403, {"Content-Type": "text/html"}, b"blocked")
        factory = Factory([[HTML, forbidden], [HTML, JSON]])
        client = NSEClient(config(), transport_factory=factory)

        self.assertEqual(len(client.fetch_option_chain()), 3)
        self.assertEqual(len(factory.transports), 2)

    def test_fails_after_one_session_rebuild(self) -> None:
        unauthorized = HTTPResponse(401, {}, b"")
        factory = Factory([[HTML, unauthorized], [HTML, unauthorized]])
        client = NSEClient(config(), transport_factory=factory)

        with self.assertRaisesRegex(AuthorizationError, "one session rebuild"):
            client.fetch_option_chain()
        self.assertEqual(len(factory.transports), 2)

    def test_validates_status_before_content(self) -> None:
        client = NSEClient(
            config(),
            transport_factory=Factory([[HTML, HTTPResponse(404, {}, b"missing")]]),
        )
        with self.assertRaisesRegex(HTTPStatusError, "404"):
            client.fetch_option_chain()

    def test_rejects_non_json_content_type(self) -> None:
        client = NSEClient(
            config(),
            transport_factory=Factory(
                [[HTML, HTTPResponse(200, {"Content-Type": "text/html"}, b"no")]]
            ),
        )
        with self.assertRaises(ResponseContentTypeError):
            client.fetch_option_chain()

    def test_rejects_malformed_json(self) -> None:
        client = NSEClient(
            config(),
            transport_factory=Factory(
                [[HTML, HTTPResponse(200, {"Content-Type": "application/json"}, b"{")]]
            ),
        )
        with self.assertRaises(MalformedJSONError):
            client.fetch_option_chain()

    def test_custom_headers_and_symbol_are_used_without_secret_handling(self) -> None:
        factory = Factory([[HTML, JSON]])
        custom = config(symbol="BANKNIFTY", headers={"User-Agent": "test-browser"})

        NSEClient(custom, transport_factory=factory).fetch_option_chain()

        _, headers, _ = factory.transports[0].calls[1]
        self.assertEqual(headers, {"User-Agent": "test-browser"})
        self.assertIn("symbol=BANKNIFTY", factory.transports[0].calls[1][0])


if __name__ == "__main__":
    unittest.main()
