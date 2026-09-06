from pathlib import Path
from unittest import TestCase

MIGRATION = (
    Path(__file__).parents[2]
    / "migrations"
    / "002_split_raw_and_analytics.sql"
).read_text(encoding="utf-8")


class SplitMigrationContractTests(TestCase):
    def test_runs_in_one_transaction(self) -> None:
        self.assertIn("BEGIN;", MIGRATION)
        self.assertIn("COMMIT;", MIGRATION)

    def test_adds_collector_owned_columns(self) -> None:
        self.assertRegex(
            MIGRATION, r"ALTER TABLE collection_runs\s+ADD COLUMN attempt_count"
        )
        self.assertRegex(
            MIGRATION, r"ALTER TABLE option_observations\s+ADD COLUMN nse_iv"
        )

    def test_creates_pricer_owned_tables_keyed_the_tall_way(self) -> None:
        self.assertIn("CREATE TABLE pricing_runs", MIGRATION)
        self.assertIn("CREATE TABLE option_analytics", MIGRATION)
        self.assertIn(
            "PRIMARY KEY (snapshot_id, expiry, strike, option_type)", MIGRATION
        )
        self.assertRegex(MIGRATION, r"assumptions\s+jsonb\s+NOT NULL")
        self.assertRegex(MIGRATION, r"thresholds\s+jsonb\s+NOT NULL")

    def test_analytics_carries_the_frozen_seven_value_status(self) -> None:
        for status in (
            "calculated",
            "excluded_zero_volume",
            "excluded_low_premium",
            "excluded_outside_strike_range",
            "invalid_market_data",
            "invalid_model_input",
            "solver_did_not_converge",
        ):
            self.assertIn(f"'{status}'", MIGRATION)
        self.assertIn("exclusion_reason text", MIGRATION)

    def test_analytics_forward_present_iff_time_to_expiry_present(self) -> None:
        self.assertIn(
            "CHECK ((forward IS NULL) = (time_to_expiry IS NULL))", MIGRATION
        )

    def test_analytics_references_observed_contracts_and_a_pricing_run(self) -> None:
        self.assertIn(
            "REFERENCES pricing_runs(snapshot_id) ON DELETE RESTRICT", MIGRATION
        )
        self.assertRegex(
            MIGRATION,
            r"FOREIGN KEY \(snapshot_id, expiry, strike, option_type\)\s+"
            r"REFERENCES option_observations",
        )

    def test_priced_rows_are_insert_once_never_updated_or_deleted(self) -> None:
        self.assertIn("reject_priced_row_mutation", MIGRATION)
        self.assertIn(
            "BEFORE UPDATE OR DELETE ON pricing_runs", MIGRATION
        )
        self.assertIn(
            "BEFORE UPDATE OR DELETE ON option_analytics", MIGRATION
        )
        self.assertNotIn(
            "BEFORE INSERT OR UPDATE OR DELETE ON option_analytics", MIGRATION
        )

    def test_defines_required_indexes(self) -> None:
        for index in (
            "option_analytics_snapshot_idx",
            "option_analytics_expiry_idx",
            "option_analytics_curve_idx",
        ):
            self.assertIn(index, MIGRATION)

    def test_provisions_three_non_overlapping_write_roles(self) -> None:
        for role in ("collector", "pricer", "reader"):
            self.assertIn(f"WHERE rolname = '{role}'", MIGRATION)
        # collector writes raw only
        self.assertIn(
            "GRANT SELECT, INSERT ON collection_runs, option_observations TO collector",
            MIGRATION,
        )
        # pricer writes analytics only, reads raw
        self.assertIn(
            "GRANT SELECT ON collection_runs, option_observations TO pricer",
            MIGRATION,
        )
        self.assertIn(
            "GRANT SELECT, INSERT ON pricing_runs, option_analytics TO pricer",
            MIGRATION,
        )
        # reader writes nothing
        self.assertNotRegex(MIGRATION, r"GRANT[^;]*INSERT[^;]*TO reader")
        self.assertNotRegex(MIGRATION, r"GRANT[^;]*UPDATE[^;]*TO reader")
