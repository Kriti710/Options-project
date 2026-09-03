from pathlib import Path
from unittest import TestCase

MIGRATION = (
    Path(__file__).parents[2] / "migrations" / "001_create_snapshot_storage.sql"
).read_text(encoding="utf-8")


class MigrationContractTests(TestCase):
    def test_schema_has_atomic_snapshot_tables_and_utc_capable_timestamps(self) -> None:
        self.assertIn("CREATE TABLE collection_runs", MIGRATION)
        self.assertIn("CREATE TABLE option_observations", MIGRATION)
        self.assertRegex(MIGRATION, r"collected_at\s+timestamptz\s+NOT NULL")
        self.assertRegex(MIGRATION, r"completed_at\s+timestamptz")
        self.assertIn("BEGIN;", MIGRATION)
        self.assertIn("COMMIT;", MIGRATION)

    def test_schema_enforces_contract_identity_and_frozen_statuses(self) -> None:
        self.assertIn(
            "PRIMARY KEY (snapshot_id, expiry, strike, option_type)", MIGRATION
        )
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

    def test_schema_persists_configuration_and_defines_required_indexes(self) -> None:
        self.assertRegex(MIGRATION, r"assumptions\s+jsonb\s+NOT NULL")
        self.assertRegex(MIGRATION, r"thresholds\s+jsonb\s+NOT NULL")
        for index in (
            "option_observations_snapshot_idx",
            "option_observations_expiry_idx",
            "option_observations_strike_idx",
            "collection_runs_latest_complete_idx",
        ):
            self.assertIn(index, MIGRATION)
        self.assertIn("WHERE status = 'completed'", MIGRATION)

    def test_completed_snapshots_are_database_immutable(self) -> None:
        self.assertIn("reject_completed_snapshot_mutation", MIGRATION)
        self.assertIn("reject_completed_run_mutation", MIGRATION)
        self.assertIn("BEFORE INSERT OR UPDATE OR DELETE", MIGRATION)
