from pathlib import Path
from unittest import TestCase

MIGRATION = (
    Path(__file__).parents[2]
    / "migrations"
    / "004_drop_legacy_pricing_columns.sql"
).read_text(encoding="utf-8")


class DropLegacyPricingColumnsTests(TestCase):
    def test_runs_in_one_transaction(self) -> None:
        self.assertIn("BEGIN;", MIGRATION)
        self.assertIn("COMMIT;", MIGRATION)

    def test_backfills_legacy_runs_before_dropping_columns(self) -> None:
        run_backfill = MIGRATION.index("INSERT INTO pricing_runs")
        analytics_backfill = MIGRATION.index("INSERT INTO option_analytics")
        run_drop = MIGRATION.index("ALTER TABLE collection_runs")
        observation_drop = MIGRATION.index("ALTER TABLE option_observations")
        self.assertLess(run_backfill, analytics_backfill)
        self.assertLess(analytics_backfill, run_drop)
        self.assertLess(run_drop, observation_drop)
        self.assertIn("ON CONFLICT (snapshot_id) DO NOTHING", MIGRATION)
        self.assertIn(
            "ON CONFLICT (snapshot_id, expiry, strike, option_type) DO NOTHING",
            MIGRATION,
        )

    def test_derives_the_forward_for_legacy_analytics(self) -> None:
        self.assertIn(
            "(p.risk_free_rate - p.dividend_yield) * o.time_to_expiry",
            MIGRATION,
        )

    def test_drops_pricing_configuration_from_collection_runs(self) -> None:
        section = MIGRATION[
            MIGRATION.index("ALTER TABLE collection_runs") :
            MIGRATION.index("ALTER TABLE option_observations")
        ]
        for column in (
            "risk_free_rate",
            "dividend_yield",
            "model_name",
            "assumptions",
            "thresholds",
        ):
            self.assertIn(f"DROP COLUMN {column}", section)

    def test_drops_computed_values_from_option_observations(self) -> None:
        section = MIGRATION[MIGRATION.index("ALTER TABLE option_observations") :]
        for column in (
            "selected_price",
            "price_source",
            "calculation_status",
            "exclusion_reason",
            "implied_volatility",
            "delta",
            "gamma",
            "vega",
            "theta",
            "time_to_expiry",
        ):
            self.assertIn(f"DROP COLUMN {column}", section)

    def test_keeps_raw_collector_columns(self) -> None:
        for column in ("spot", "attempt_count", "nse_iv"):
            self.assertNotIn(f"DROP COLUMN {column}", MIGRATION)
