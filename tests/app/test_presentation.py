from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from app.models import Contract, Snapshot
from app.presentation import (
    available_expiries,
    build_smile_chart,
    format_timestamp,
    freshness,
    included_excluded,
    status_counts,
)


EXPIRY = date(2026, 9, 10)
LATER_EXPIRY = date(2026, 9, 17)


def contract(
    strike: float,
    option_type: str,
    iv: float | None,
    *,
    expiry: date = EXPIRY,
    status: str = "calculated",
) -> Contract:
    return Contract(
        expiry=expiry,
        strike=strike,
        option_type=option_type,
        status=status,
        market_price=100.0,
        price_source="midpoint",
        implied_volatility=iv,
        delta=0.5,
        gamma=0.001,
        vega=10.0,
        theta=-5.0,
    )


def snapshot(*contracts: Contract) -> Snapshot:
    return Snapshot(
        snapshot_id="snap-1",
        captured_at=datetime(2026, 9, 4, 4, 30, tzinfo=UTC),
        spot=24_980.0,
        forward=25_025.0,
        contracts=contracts,
        thresholds={"minimum_premium": "₹0.50"},
    )


class PresentationTest(unittest.TestCase):
    def test_timestamp_is_displayed_in_kolkata(self) -> None:
        self.assertEqual(
            format_timestamp(datetime(2026, 9, 4, 4, 30, tzinfo=UTC)),
            "04 Sep 2026, 10:00:00 IST",
        )

    def test_naive_timestamp_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            format_timestamp(datetime(2026, 9, 4, 4, 30))

    def test_fresh_aging_and_stale_states(self) -> None:
        captured = datetime(2026, 9, 4, 4, 30, tzinfo=UTC)
        self.assertEqual(freshness(captured, now=captured).label, "Fresh")
        self.assertEqual(
            freshness(captured, now=captured + timedelta(minutes=30)).label,
            "Aging",
        )
        self.assertEqual(
            freshness(captured, now=captured + timedelta(hours=2)).label,
            "Stale",
        )

    def test_counts_every_contract_status(self) -> None:
        data = snapshot(
            contract(25_000, "call", 0.15),
            contract(
                25_100,
                "put",
                None,
                status="excluded_zero_volume",
            ),
            contract(
                25_200,
                "call",
                None,
                status="solver_did_not_converge",
            ),
        )
        self.assertEqual(included_excluded(data), (1, 2))
        counts = status_counts(data)
        self.assertEqual(counts["excluded_zero_volume"], 1)
        self.assertEqual(counts["excluded_low_premium"], 0)
        self.assertEqual(counts["solver_did_not_converge"], 1)

    def test_expiries_are_unique_and_sorted(self) -> None:
        data = snapshot(
            contract(25_000, "call", 0.15, expiry=LATER_EXPIRY),
            contract(25_000, "put", 0.16),
        )
        self.assertEqual(available_expiries(data), (EXPIRY, LATER_EXPIRY))

    def test_chart_has_call_put_expiry_overlay_and_atm_reference(self) -> None:
        data = snapshot(
            contract(25_000, "call", 0.18),
            contract(25_000, "put", 0.20),
            contract(25_100, "call", 0.21),
            contract(25_000, "call", 0.22, expiry=LATER_EXPIRY),
        )
        chart = build_smile_chart(data, (EXPIRY, LATER_EXPIRY), EXPIRY)
        self.assertEqual(len(chart.series), 3)
        self.assertEqual(chart.atm_strike, 25_000)
        self.assertAlmostEqual(chart.atm_iv or 0, 0.19)
        self.assertEqual(
            {item.option_type for item in chart.series}, {"call", "put"}
        )

    def test_historical_series_are_marked_for_dashed_rendering(self) -> None:
        current = snapshot(contract(25_000, "call", 0.18))
        old = Snapshot(
            snapshot_id="old",
            captured_at=datetime(2026, 9, 3, 4, 30, tzinfo=UTC),
            spot=24_900,
            forward=24_950,
            contracts=(contract(25_000, "call", 0.25),),
        )
        chart = build_smile_chart(current, (EXPIRY,), EXPIRY, historical=old)
        self.assertEqual([item.historical for item in chart.series], [False, True])

    def test_empty_calculated_data_has_no_reference(self) -> None:
        data = snapshot(
            contract(25_000, "call", None, status="excluded_low_premium")
        )
        chart = build_smile_chart(data, (EXPIRY,), EXPIRY)
        self.assertEqual(chart.series, ())
        self.assertIsNone(chart.atm_iv)

    def test_unknown_status_and_missing_calculated_iv_fail_loudly(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown calculation status"):
            contract(25_000, "call", None, status="new_status")
        with self.assertRaisesRegex(ValueError, "must have implied volatility"):
            contract(25_000, "call", None)


if __name__ == "__main__":
    unittest.main()
