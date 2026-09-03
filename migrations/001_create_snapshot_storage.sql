BEGIN;

CREATE TABLE collection_runs (
    snapshot_id uuid PRIMARY KEY,
    collected_at timestamptz NOT NULL,
    completed_at timestamptz,
    status text NOT NULL CHECK (status IN ('in_progress', 'completed', 'failed')),
    spot double precision NOT NULL CHECK (spot > 0),
    risk_free_rate double precision NOT NULL,
    dividend_yield double precision NOT NULL,
    model_name text NOT NULL,
    assumptions jsonb NOT NULL,
    thresholds jsonb NOT NULL,
    failure_diagnostics text,
    CHECK (
        (status = 'completed' AND completed_at IS NOT NULL)
        OR (status <> 'completed' AND completed_at IS NULL)
    )
);

CREATE TABLE option_observations (
    snapshot_id uuid NOT NULL REFERENCES collection_runs(snapshot_id) ON DELETE RESTRICT,
    expiry date NOT NULL,
    strike double precision NOT NULL CHECK (strike > 0),
    option_type text NOT NULL CHECK (option_type IN ('call', 'put')),
    last_traded_price double precision,
    bid double precision,
    ask double precision,
    volume bigint,
    open_interest bigint,
    selected_price double precision,
    price_source text CHECK (price_source IN ('midpoint', 'last_traded_price')),
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
    time_to_expiry double precision,
    PRIMARY KEY (snapshot_id, expiry, strike, option_type),
    CHECK (
        (calculation_status = 'calculated'
            AND exclusion_reason IS NULL
            AND implied_volatility IS NOT NULL
            AND delta IS NOT NULL AND gamma IS NOT NULL
            AND vega IS NOT NULL AND theta IS NOT NULL
            AND time_to_expiry IS NOT NULL)
        OR
        (calculation_status <> 'calculated' AND exclusion_reason IS NOT NULL)
    )
);

CREATE INDEX option_observations_snapshot_idx
    ON option_observations (snapshot_id);
CREATE INDEX option_observations_expiry_idx
    ON option_observations (expiry);
CREATE INDEX option_observations_strike_idx
    ON option_observations (strike);
CREATE INDEX option_observations_curve_idx
    ON option_observations (snapshot_id, expiry, strike, option_type);
CREATE INDEX collection_runs_latest_complete_idx
    ON collection_runs (collected_at DESC)
    WHERE status = 'completed';

-- Completed snapshots are immutable. Rows belonging to an in-progress run can
-- only be created by the writer's transaction and disappear if it rolls back.
CREATE FUNCTION reject_completed_snapshot_mutation() RETURNS trigger
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

CREATE TRIGGER option_observations_completed_immutable
BEFORE INSERT OR UPDATE OR DELETE ON option_observations
FOR EACH ROW EXECUTE FUNCTION reject_completed_snapshot_mutation();

CREATE FUNCTION reject_completed_run_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'completed' THEN
        RAISE EXCEPTION 'completed snapshots are immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER collection_runs_completed_immutable
BEFORE UPDATE OR DELETE ON collection_runs
FOR EACH ROW EXECUTE FUNCTION reject_completed_run_mutation();

COMMIT;
