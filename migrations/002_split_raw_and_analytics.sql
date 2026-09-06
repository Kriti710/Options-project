-- 002_split_raw_and_analytics.sql
--
-- Purpose: separate raw collection from computed pricing so that the collector,
-- the pricer, and the reader are distinct database roles with non-overlapping
-- write scope.
--
--   collection_runs / option_observations           -> `collector` role
--   pricing_runs / option_analytics / pricing_smiles -> `pricer` role
--   everything                                       -> read by `reader`
--
-- This migration is ADDITIVE. It creates the two pricer-owned tables, adds two
-- columns to the collector-owned tables, and provisions the three roles. The
-- now-redundant pricing columns still present on `collection_runs` and
-- `option_observations` (risk_free_rate, dividend_yield, model_name,
-- assumptions, thresholds, implied_volatility, delta, gamma, vega, theta,
-- time_to_expiry, selected_price, price_source, calculation_status,
-- exclusion_reason) are left in place and will be dropped by migration 003
-- once the collector and pricer code paths have cut over. Keeping them here
-- means `main` stays deployable at every step.
--
-- NSE timestamp parsing note (frozen convention, see docs/contracts.md):
-- NSE returns its option-chain timestamp as `dd-Mon-yyyy HH:MM:SS` with no
-- timezone. It is interpreted as Asia/Kolkata and converted to UTC before it
-- reaches any column defined here. Every timestamptz below is stored in UTC.

BEGIN;

-- --------------------------------------------------------------------------
-- Collector-owned additions
-- --------------------------------------------------------------------------

-- Number of fetch attempts (including the successful one) behind this snapshot.
-- Lets the collector surface retry pressure without a separate log table.
ALTER TABLE collection_runs
    ADD COLUMN attempt_count integer NOT NULL DEFAULT 1 CHECK (attempt_count >= 1);

-- NSE's own published implied volatility for the contract, stored raw. It is a
-- reference / solver-seed value only and is never the authoritative IV, which
-- the pricer computes into option_analytics.
ALTER TABLE option_observations
    ADD COLUMN nse_iv double precision;

-- --------------------------------------------------------------------------
-- Pricer-owned: one pricing pass per snapshot
-- --------------------------------------------------------------------------

-- Exactly one row per collection snapshot: one pricing pass, one rate set, one
-- threshold set. This is what docs/contracts.md requires for reproducibility.
CREATE TABLE pricing_runs (
    snapshot_id uuid PRIMARY KEY
        REFERENCES collection_runs(snapshot_id) ON DELETE RESTRICT,
    priced_at timestamptz NOT NULL,
    risk_free_rate double precision NOT NULL,
    dividend_yield double precision NOT NULL,
    model_name text NOT NULL,
    assumptions jsonb NOT NULL,
    thresholds jsonb NOT NULL
);

-- --------------------------------------------------------------------------
-- Pricer-owned: computed implied volatility and Greeks, one row per contract
-- --------------------------------------------------------------------------

CREATE TABLE option_analytics (
    snapshot_id uuid NOT NULL
        REFERENCES pricing_runs(snapshot_id) ON DELETE RESTRICT,
    expiry date NOT NULL,
    strike double precision NOT NULL CHECK (strike > 0),
    option_type text NOT NULL CHECK (option_type IN ('call', 'put')),

    -- Mark selection is recorded even for rows excluded before pricing.
    selected_price double precision,
    price_source text CHECK (price_source IN ('midpoint', 'last_traded_price')),

    -- Forward used for the ATM anchor: F = S * exp((r - q) * T). Null whenever
    -- the row was excluded before a time to expiry was computed.
    forward double precision,
    time_to_expiry double precision,

    -- Exactly one frozen outcome per attempted contract (docs/contracts.md
    -- "Calculation status"). Identical value set to option_observations so the
    -- reader renders one enum regardless of which table it reads.
    calculation_status text NOT NULL CHECK (calculation_status IN (
        'calculated',
        'excluded_zero_volume',
        'excluded_low_premium',
        'excluded_outside_strike_range',
        'invalid_market_data',
        'invalid_model_input',
        'solver_did_not_converge'
    )),
    exclusion_reason text,

    implied_volatility double precision,
    delta double precision,
    gamma double precision,
    vega double precision,
    theta double precision,

    -- Richness scoring (task #1): how this contract's market IV compares with a
    -- robust reference smile fitted to its expiry. Advisory and fully nullable
    -- - populated only for 'calculated' rows whose expiry had enough priced
    -- contracts to fit a curve; otherwise valuation is 'unscored' and the rest
    -- are null. Not coupled to calculation_status by CHECK. Owned by the pricer
    -- (nifty_vol.domain.richness).
    fitted_iv double precision,
    iv_residual double precision,
    richness_price double precision,
    richness_z double precision,
    valuation text CHECK (valuation IN ('cheap', 'fair', 'expensive', 'unscored')),

    PRIMARY KEY (snapshot_id, expiry, strike, option_type),

    -- Analytics rows exist only for contracts that were actually observed.
    FOREIGN KEY (snapshot_id, expiry, strike, option_type)
        REFERENCES option_observations (snapshot_id, expiry, strike, option_type)
        ON DELETE RESTRICT,

    -- A converged calculation carries IV and every Greek and no exclusion
    -- reason; anything else carries an explanation and no partial Greeks.
    CHECK (
        (calculation_status = 'calculated'
            AND exclusion_reason IS NULL
            AND implied_volatility IS NOT NULL
            AND delta IS NOT NULL AND gamma IS NOT NULL
            AND vega IS NOT NULL AND theta IS NOT NULL
            AND time_to_expiry IS NOT NULL)
        OR
        (calculation_status <> 'calculated' AND exclusion_reason IS NOT NULL)
    ),

    -- The forward is defined exactly when a positive time to expiry exists.
    CHECK ((forward IS NULL) = (time_to_expiry IS NULL))
);

CREATE INDEX option_analytics_snapshot_idx
    ON option_analytics (snapshot_id);
CREATE INDEX option_analytics_expiry_idx
    ON option_analytics (expiry);
CREATE INDEX option_analytics_curve_idx
    ON option_analytics (snapshot_id, expiry, strike, option_type);

-- --------------------------------------------------------------------------
-- Pricer-owned: the fitted reference smile per expiry
-- --------------------------------------------------------------------------

-- One row per expiry that had enough calculated contracts to fit a smile. The
-- reader samples this quadratic densely to draw a smooth reference curve:
--     iv = c0 + c1*k + c2*k^2,   k = ln(strike / forward)   (natural log)
-- Expiries with too few calculated contracts get no row here and every
-- contract in that expiry carries option_analytics.valuation = 'unscored'.
CREATE TABLE pricing_smiles (
    snapshot_id uuid NOT NULL
        REFERENCES pricing_runs(snapshot_id) ON DELETE RESTRICT,
    expiry date NOT NULL,
    forward double precision NOT NULL CHECK (forward > 0),
    c0 double precision NOT NULL,
    c1 double precision NOT NULL,
    c2 double precision NOT NULL,
    sample_size integer NOT NULL CHECK (sample_size >= 3),
    residual_scale double precision NOT NULL CHECK (residual_scale >= 0),
    PRIMARY KEY (snapshot_id, expiry)
);

-- --------------------------------------------------------------------------
-- Immutability: pricing output is insert-once and never rewritten
-- --------------------------------------------------------------------------

-- The pricer writes pricing_runs / option_analytics AFTER the collection run is
-- already 'completed', so INSERT must be allowed at that point. What must never
-- happen is a later UPDATE or DELETE: corrections are new snapshots, never
-- in-place edits (docs/contracts.md "Snapshot atomicity"). These triggers fire
-- on UPDATE/DELETE only and reuse the run-status lookup helper from 001.
CREATE FUNCTION reject_priced_row_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE run_status text;
BEGIN
    SELECT status INTO run_status
      FROM collection_runs
     WHERE snapshot_id = COALESCE(OLD.snapshot_id, NEW.snapshot_id);
    IF run_status = 'completed' THEN
        RAISE EXCEPTION 'completed snapshots are immutable';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE TRIGGER pricing_runs_completed_immutable
BEFORE UPDATE OR DELETE ON pricing_runs
FOR EACH ROW EXECUTE FUNCTION reject_priced_row_mutation();

CREATE TRIGGER option_analytics_completed_immutable
BEFORE UPDATE OR DELETE ON option_analytics
FOR EACH ROW EXECUTE FUNCTION reject_priced_row_mutation();

CREATE TRIGGER pricing_smiles_completed_immutable
BEFORE UPDATE OR DELETE ON pricing_smiles
FOR EACH ROW EXECUTE FUNCTION reject_priced_row_mutation();

-- --------------------------------------------------------------------------
-- Role separation
-- --------------------------------------------------------------------------
-- Roles are cluster-wide and may already exist (e.g. created by an operator or
-- an earlier environment). Create them only when absent. They are NOLOGIN
-- group roles; deployment grants a concrete login user membership in the role
-- appropriate to that component.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'collector') THEN
        CREATE ROLE collector NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pricer') THEN
        CREATE ROLE pricer NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reader') THEN
        CREATE ROLE reader NOLOGIN;
    END IF;
END
$$;

-- collector: writes raw collection, promotes its own run to completed.
GRANT SELECT, INSERT ON collection_runs, option_observations TO collector;
GRANT UPDATE (status, completed_at, failure_diagnostics) ON collection_runs TO collector;

-- pricer: reads raw collection, writes computed analytics.
GRANT SELECT ON collection_runs, option_observations TO pricer;
GRANT SELECT, INSERT ON pricing_runs, option_analytics, pricing_smiles TO pricer;

-- reader: reads everything, writes nothing.
GRANT SELECT ON
    collection_runs, option_observations,
    pricing_runs, option_analytics, pricing_smiles
    TO reader;

COMMIT;
