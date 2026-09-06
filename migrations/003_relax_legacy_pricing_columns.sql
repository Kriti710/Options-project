-- 003_relax_legacy_pricing_columns.sql
--
-- Purpose: unblock the raw-only collector write path. Migration 002 moved
-- computed pricing into `pricing_runs` / `option_analytics`, but the pricing
-- columns still on the collector-owned tables were left `NOT NULL` (from 001),
-- so a `collector`-role INSERT that does not populate them fails.
--
-- This migration only relaxes constraints; it drops no columns and loses no
-- data. Migration 004 drops the now-dead columns once nothing writes or reads
-- them (the pricer writes `option_analytics`, the reader reads it).
--
-- The legacy combined writer (`write_snapshot_atomic` / `CollectionRun`) keeps
-- working across this migration: it still populates every column, they are
-- simply no longer mandatory.

BEGIN;

-- Pricing configuration now lives on `pricing_runs` (written by the pricer).
ALTER TABLE collection_runs
    ALTER COLUMN risk_free_rate DROP NOT NULL,
    ALTER COLUMN dividend_yield DROP NOT NULL,
    ALTER COLUMN model_name     DROP NOT NULL,
    ALTER COLUMN assumptions    DROP NOT NULL,
    ALTER COLUMN thresholds     DROP NOT NULL;

-- A failed collection has no spot when parsing fails before the underlying is
-- read. The existing `CHECK (spot > 0)` already tolerates NULL. A completed
-- run always carries a spot (enforced by the writer).
ALTER TABLE collection_runs
    ALTER COLUMN spot DROP NOT NULL;

-- The per-contract outcome is recorded on `option_analytics` (written by the
-- pricer). A raw observation leaves it NULL; the existing row CHECK passes
-- because `NULL = 'calculated'` and `NULL <> 'calculated'` are both NULL.
ALTER TABLE option_observations
    ALTER COLUMN calculation_status DROP NOT NULL;

COMMIT;
