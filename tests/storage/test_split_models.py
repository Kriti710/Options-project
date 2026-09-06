from datetime import date, datetime, timedelta, timezone
from unittest import TestCase
from uuid import uuid4

from nifty_vol.storage import ContractIdentity, OptionAnalytics, PricingRun


def calculated() -> OptionAnalytics:
    return OptionAnalytics(
        identity=ContractIdentity(date(2026, 9, 24), 22000, "call"),
        calculation_status="calculated",
        selected_price=100.0,
        price_source="midpoint",
        forward=22050.0,
        time_to_expiry=0.05,
        implied_volatility=0.2,
        delta=0.5,
        gamma=0.001,
        vega=10.0,
        theta=-5.0,
    )


class PricingRunTests(TestCase):
    def test_normalizes_priced_at_to_utc(self) -> None:
        run = PricingRun(
            snapshot_id=uuid4(),
            priced_at=datetime(
                2026, 9, 4, 15, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))
            ),
            risk_free_rate=0.065,
            dividend_yield=0.0,
            model_name="black_scholes_merton",
            assumptions={"day_count": "ACT/365F"},
            thresholds={"solver_tolerance": 1e-6},
        )
        self.assertEqual(run.priced_at.isoformat(), "2026-09-04T10:00:00+00:00")

    def test_rejects_naive_priced_at(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            PricingRun(
                snapshot_id=uuid4(),
                priced_at=datetime(2026, 9, 4),
                risk_free_rate=0.065,
                dividend_yield=0.0,
                model_name="black_scholes_merton",
                assumptions={},
                thresholds={},
            )


class OptionAnalyticsTests(TestCase):
    def test_calculated_row_round_trips(self) -> None:
        self.assertEqual(calculated().implied_volatility, 0.2)

    def test_unknown_status_is_not_coerced(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown calculation_status"):
            OptionAnalytics(
                identity=ContractIdentity(date(2026, 9, 24), 22000, "put"),
                calculation_status="mystery",
                exclusion_reason="bad",
            )

    def test_excluded_row_requires_reason(self) -> None:
        with self.assertRaisesRegex(ValueError, "exclusion_reason"):
            OptionAnalytics(
                identity=ContractIdentity(date(2026, 9, 24), 22000, "put"),
                calculation_status="excluded_zero_volume",
            )

    def test_calculated_row_rejects_missing_greek(self) -> None:
        with self.assertRaisesRegex(ValueError, "IV, Greeks"):
            OptionAnalytics(
                identity=ContractIdentity(date(2026, 9, 24), 22000, "call"),
                calculation_status="calculated",
                forward=22050.0,
                time_to_expiry=0.05,
                implied_volatility=0.2,
                delta=0.5,
                gamma=0.001,
                vega=10.0,
                theta=None,
            )

    def test_forward_present_iff_time_to_expiry_present(self) -> None:
        with self.assertRaisesRegex(ValueError, "forward is defined exactly when"):
            OptionAnalytics(
                identity=ContractIdentity(date(2026, 9, 24), 22000, "put"),
                calculation_status="excluded_low_premium",
                exclusion_reason="selected price is below floor",
                forward=22050.0,
                time_to_expiry=None,
            )

    def test_excluded_row_may_keep_its_selected_mark(self) -> None:
        row = OptionAnalytics(
            identity=ContractIdentity(date(2026, 9, 24), 30000, "call"),
            calculation_status="excluded_outside_strike_range",
            exclusion_reason="strike distance exceeds limit",
            selected_price=1.2,
            price_source="last_traded_price",
        )
        self.assertEqual(row.selected_price, 1.2)
        self.assertIsNone(row.forward)


if __name__ == "__main__":  # pragma: no cover
    from unittest import main

    main()
