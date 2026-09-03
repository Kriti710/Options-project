from datetime import UTC, date, datetime, timedelta, timezone
from unittest import TestCase

from nifty_vol.storage import CollectionRun, ContractIdentity, OptionObservation


def calculated(strike: float = 22000) -> OptionObservation:
    return OptionObservation(
        identity=ContractIdentity(date(2026, 9, 24), strike, "call"),
        last_traded_price=100,
        bid=99,
        ask=101,
        volume=10,
        open_interest=20,
        selected_price=100,
        price_source="midpoint",
        calculation_status="calculated",
        implied_volatility=0.2,
        delta=0.5,
        gamma=0.001,
        vega=10,
        theta=-5,
        time_to_expiry=0.05,
    )


class StorageModelTests(TestCase):
    def test_run_normalizes_aware_timestamp_to_utc(self) -> None:
        run = CollectionRun(
            collected_at=datetime(
                2026, 9, 4, 15, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))
            ),
            spot=22000,
            risk_free_rate=0.065,
            dividend_yield=0,
            model_name="black_scholes_merton",
            assumptions={"day_count": "ACT/365F"},
            thresholds={"solver_tolerance": 1e-6},
            observations=(calculated(),),
        )
        self.assertEqual(run.collected_at.isoformat(), "2026-09-04T10:00:00+00:00")

    def test_run_rejects_naive_timestamp(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            CollectionRun(
                collected_at=datetime(2026, 9, 4),
                spot=22000,
                risk_free_rate=0.065,
                dividend_yield=0,
                model_name="black_scholes_merton",
                assumptions={},
                thresholds={},
                observations=(),
            )

    def test_run_rejects_duplicate_contract_identity(self) -> None:
        item = calculated()
        with self.assertRaisesRegex(ValueError, "unique"):
            CollectionRun(
                collected_at=datetime.now(UTC),
                spot=22000,
                risk_free_rate=0.065,
                dividend_yield=0,
                model_name="black_scholes_merton",
                assumptions={},
                thresholds={},
                observations=(item, item),
            )

    def test_unknown_status_is_not_coerced(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown calculation_status"):
            OptionObservation(
                identity=ContractIdentity(date(2026, 9, 24), 22000, "put"),
                last_traded_price=None,
                bid=None,
                ask=None,
                volume=None,
                open_interest=None,
                selected_price=None,
                price_source=None,
                calculation_status="mystery",
                exclusion_reason="bad",
            )

    def test_exclusion_requires_reason(self) -> None:
        with self.assertRaisesRegex(ValueError, "exclusion_reason"):
            OptionObservation(
                identity=ContractIdentity(date(2026, 9, 24), 22000, "put"),
                last_traded_price=0,
                bid=0,
                ask=0,
                volume=0,
                open_interest=1,
                selected_price=0,
                price_source="midpoint",
                calculation_status="excluded_zero_volume",
            )
