import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

from nifty_vol.collector import EmptyChainError, SchemaError, parse_option_chain

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class OptionChainParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fetched_at = datetime(2026, 9, 4, 10, tzinfo=UTC)

    def test_normalizes_calls_and_puts(self) -> None:
        records = parse_option_chain(
            fixture("option_chain.json"), fetched_at=self.fetched_at
        )

        self.assertEqual(len(records), 3)
        call, put, zero_volume_call = records
        self.assertEqual(call.symbol, "NIFTY")
        self.assertEqual(call.option_type, "call")
        self.assertEqual(put.option_type, "put")
        self.assertEqual(call.strike, 25100.0)
        self.assertEqual(call.underlying_spot, 25123.4)
        self.assertEqual(call.last_price, 180.5)
        self.assertEqual(call.bid, 180.1)
        self.assertEqual(call.ask, 180.9)
        self.assertEqual(call.volume, 12500)
        self.assertEqual(call.open_interest, 45000)
        self.assertEqual(
            call.expiry, datetime(2026, 9, 10, 10, tzinfo=UTC)
        )
        self.assertEqual(
            call.observed_at, datetime(2026, 9, 4, 9, 59, tzinfo=UTC)
        )
        self.assertIsNone(zero_volume_call.last_price)
        self.assertEqual(zero_volume_call.volume, 0)

    def test_uses_fetch_time_when_nse_timestamp_is_absent(self) -> None:
        payload = fixture("option_chain.json")
        assert isinstance(payload, dict)
        del payload["records"]["timestamp"]

        records = parse_option_chain(payload, fetched_at=self.fetched_at)

        self.assertEqual(records[0].observed_at, self.fetched_at)

    def test_rejects_missing_schema(self) -> None:
        with self.assertRaisesRegex(SchemaError, "records.data"):
            parse_option_chain(
                {"records": {"underlyingValue": 1}}, fetched_at=self.fetched_at
            )

    def test_rejects_malformed_contract_field(self) -> None:
        payload = fixture("option_chain.json")
        assert isinstance(payload, dict)
        payload["records"]["data"][0]["CE"]["openInterest"] = "lots"

        with self.assertRaisesRegex(SchemaError, "openInterest"):
            parse_option_chain(payload, fetched_at=self.fetched_at)

    def test_rejects_empty_chain(self) -> None:
        with self.assertRaises(EmptyChainError):
            parse_option_chain(
                fixture("empty_chain.json"), fetched_at=self.fetched_at
            )

    def test_requires_aware_fetch_timestamp(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            parse_option_chain(
                fixture("option_chain.json"), fetched_at=datetime(2026, 9, 4)
            )


if __name__ == "__main__":
    unittest.main()
