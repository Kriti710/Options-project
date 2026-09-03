# Frozen contracts

These conventions are shared by the collector, domain code, storage layer, and
reader. They are frozen for the initial implementation: changing one requires a
documented migration and coordinated updates to every component that consumes
it.

## Time and expiry

- Persist every timestamp as a timezone-aware UTC value. Naive datetimes are
  invalid at system boundaries.
- Display user-facing dates and times in `Asia/Kolkata` unless the interface
  explicitly labels another timezone.
- Treat a NIFTY expiry date as expiring at **15:30:00 Asia/Kolkata**. Construct
  that zoned instant first and convert it to UTC for storage or comparison.
- Calculate time to expiry using **ACT/365F**: actual elapsed seconds divided by
  `365 * 24 * 60 * 60`. Do not substitute trading days or round to whole days.
- A contract at or after its expiry instant has no positive time value and is
  not eligible for implied-volatility inversion.

## Units and pricing inputs

- Store and pass rates and volatility as decimals. For example, 6.5% is
  `0.065`, and 20% volatility is `0.20`. Percentage conversion belongs only at
  input and display boundaries.
- Black-Scholes-Merton calculations use the named inputs `S`, `K`, `T`, `r`,
  `q`, and `sigma`:

  | Input | Meaning | Internal unit |
  | --- | --- | --- |
  | `S` | Underlying spot | index points |
  | `K` | Strike | index points |
  | `T` | ACT/365F time to expiry | years |
  | `r` | Continuously compounded risk-free rate | decimal annual rate |
  | `q` | Continuously compounded dividend yield | decimal annual rate |
  | `sigma` | Annualized volatility | decimal |

- Option type (`call` or `put`) is explicit; it is never inferred from the sign
  of another value. Prefer keyword arguments at calculation boundaries to avoid
  positional ambiguity.

## Calculation status

Every attempted contract calculation records exactly one status. A missing
implied volatility never stands alone as the explanation.

| Status | Meaning |
| --- | --- |
| `calculated` | Implied volatility converged and dependent values are valid. |
| `excluded_zero_volume` | The contract had no traded volume. |
| `excluded_low_premium` | Its selected price was below the configured floor. |
| `excluded_outside_strike_range` | Its strike was outside the configured distance from spot. |
| `invalid_market_data` | Required market data was missing, malformed, or internally inconsistent. |
| `invalid_model_input` | A model precondition, including positive time or arbitrage bounds, failed. |
| `solver_did_not_converge` | The numerical solver exhausted its configured bounds or iterations. |

New statuses may be added only by updating storage constraints, readers, tests,
and this document together. Do not silently coerce an unknown status to another
value.

## Market price selection and thresholds

- Prefer the bid/ask midpoint when both quotes are present and valid; otherwise
  use the last traded price. Record which source was selected.
- Data-quality and numerical thresholds are configuration, not constants buried
  in calculation code. This includes minimum premium, maximum strike distance,
  solver tolerance, volatility bounds, iteration limits, and collection pacing.
- Persist or otherwise identify the effective threshold set with a snapshot so
  exclusions and results can be reproduced. Count exclusions by status.
- A solver success must satisfy both the configured repricing-error tolerance
  and the configured remaining volatility-interval tolerance. The initial
  targets are `1e-6` price units and `1e-8` decimal volatility respectively.

## Snapshot atomicity

- One collection run creates one immutable snapshot identity and timestamp.
- Publish a snapshot atomically: all intended rows and run metadata become
  visible together only after collection, validation, and persistence succeed.
- Readers query completed snapshots only. They must never observe a partial or
  in-progress snapshot.
- A failed collection records diagnostics where possible but does not publish
  any of its option rows and never alters the last completed snapshot.
- Corrections are new snapshots or explicit migrations; completed snapshots are
  not updated in place.

The storage implementation may use a single database transaction or a staged
snapshot promoted to `completed`, provided those observable guarantees hold.

## Configuration and secrets

- Supply deployment-specific settings and credentials through environment
  configuration. `.env.example` documents names using blank or non-secret
  example values.
- Never commit database URLs containing credentials, API keys, cookies, session
  tokens, request captures with sensitive headers, or populated `.env` files.
- Logs and stored failure diagnostics must redact secrets and authentication
  material.
