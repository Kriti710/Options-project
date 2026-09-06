from pathlib import Path
from unittest import TestCase

MIGRATION = (
    Path(__file__).parents[2]
    / "migrations"
    / "003_relax_legacy_pricing_columns.sql"
).read_text(encoding="utf-8")


class RelaxMigrationContractTests(TestCase):
    def test_runs_in_one_transaction(self) -> None:
        self.assertIn("BEGIN;", MIGRATION)
        self.assertIn("COMMIT;", MIGRATION)

    def test_relaxes_collector_owned_pricing_columns(self) -> None:
        for column in (
            "risk_free_rate",
            "dividend_yield",
            "model_name",
            "assumptions",
            "thresholds",
            "spot",
        ):
            self.assertRegex(
                MIGRATION, rf"ALTER COLUMN {column}\s+DROP NOT NULL"
            )

    def test_relaxes_calculation_status_on_observations(self) -> None:
        self.assertRegex(
            MIGRATION, r"ALTER COLUMN calculation_status DROP NOT NULL"
        )
        self.assertIn("ALTER TABLE option_observations", MIGRATION)

    def test_drops_no_columns(self) -> None:
        self.assertNotIn("DROP COLUMN", MIGRATION)
