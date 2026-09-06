-- 004_drop_legacy_pricing_columns.sql
--
-- Purpose: finish the raw/pricing split introduced by migration 002. The
-- collector now writes only collection_runs + option_observations, while the
-- pricer writes pricing_runs + option_analytics + pricing_smiles.
--
-- Before removing the combined columns, preserve any completed snapshots
-- written by the legacy single-writer path. Legacy rows have no fitted-smile
-- or richness values, but their pricing inputs, selected mark, IV, Greeks, and
-- explicit outcome remain readable through the split schema.

BEGIN;

INSERT INTO pricing_runs (
    snapshot_id,
    priced_at,
    risk_free_rate,
    dividend_yield,
    model_name,
    assumptions,
    thresholds
)
SELECT
    r.snapshot_id,
    r.completed_at,
    r.risk_free_rate,
    r.dividend_yield,
    r.model_name,
    r.assumptions,
    r.thresholds
FROM collection_runs r
WHERE r.status = 'completed'
  AND r.risk_free_rate IS NOT NULL
  AND r.dividend_yield IS NOT NULL
  AND r.model_name IS NOT NULL
  AND r.assumptions IS NOT NULL
  AND r.thresholds IS NOT NULL
ON CONFLICT (snapshot_id) DO NOTHING;

INSERT INTO option_analytics (
    snapshot_id,
    expiry,
    strike,
    option_type,
    selected_price,
    price_source,
    forward,
    time_to_expiry,
    calculation_status,
    exclusion_reason,
    implied_volatility,
    delta,
    gamma,
    vega,
    theta
)
SELECT
    o.snapshot_id,
    o.expiry,
    o.strike,
    o.option_type,
    o.selected_price,
    o.price_source,
    CASE
        WHEN o.time_to_expiry IS NULL THEN NULL
        ELSE r.spot * exp(
            (p.risk_free_rate - p.dividend_yield) * o.time_to_expiry
        )
    END,
    o.time_to_expiry,
    o.calculation_status,
    o.exclusion_reason,
    o.implied_volatility,
    o.delta,
    o.gamma,
    o.vega,
    o.theta
FROM option_observations o
JOIN collection_runs r ON r.snapshot_id = o.snapshot_id
JOIN pricing_runs p ON p.snapshot_id = o.snapshot_id
WHERE r.status = 'completed'
  AND o.calculation_status IS NOT NULL
ON CONFLICT (snapshot_id, expiry, strike, option_type) DO NOTHING;

ALTER TABLE collection_runs
    DROP COLUMN risk_free_rate,
    DROP COLUMN dividend_yield,
    DROP COLUMN model_name,
    DROP COLUMN assumptions,
    DROP COLUMN thresholds;

ALTER TABLE option_observations
    DROP COLUMN selected_price,
    DROP COLUMN price_source,
    DROP COLUMN calculation_status,
    DROP COLUMN exclusion_reason,
    DROP COLUMN implied_volatility,
    DROP COLUMN delta,
    DROP COLUMN gamma,
    DROP COLUMN vega,
    DROP COLUMN theta,
    DROP COLUMN time_to_expiry;

COMMIT;
