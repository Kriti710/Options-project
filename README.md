# NIFTY implied-volatility explorer

An analytical application that collects NIFTY option-chain snapshots, derives
implied volatility and Greeks, persists atomic snapshots, and presents them
through a separate reader.

## Structure

```text
src/nifty_vol/
  domain/       Pricing and volatility domain boundary
  collector/    NSE collection boundary
  storage/      Snapshot persistence boundary
  pipeline.py   Offline quote-to-observation application pipeline
app/            Streamlit reader and storage adapter
tests/
  domain/
  collector/fixtures/
  storage/
migrations/     Database migrations (future)
docs/           Cross-component contracts
```

The product context is captured in `nifty-volatility-explorer-prd.md`. The
implementation rules that components must share are frozen in
[`docs/contracts.md`](docs/contracts.md).

## Development setup

Python 3.11 or newer is required.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Use `.env.example` as the configuration checklist. Never commit populated
environment or secrets files.

The three database URLs must use separate PostgreSQL roles:

- `COLLECTOR_DATABASE_URL`: a local collector role allowed to insert and publish
  raw snapshots.
- `PRICER_DATABASE_URL`: a pricer role allowed to read raw snapshots and publish
  pricing runs, analytics, and fitted smiles.
- `READER_DATABASE_URL`: a SELECT-only role used by Streamlit. The reader also
  requests a read-only PostgreSQL session.

`.env.example` is a reference file; the application reads process environment
variables directly. For a local PowerShell session:

```powershell
$env:COLLECTOR_DATABASE_URL = "postgresql://collector-role:password@host/database"
$env:PRICER_DATABASE_URL = "postgresql://pricer-role:password@host/database"
$env:READER_DATABASE_URL = "postgresql://reader-role:password@host/database"
$env:RISK_FREE_RATE_DECIMAL = "0.065"
$env:DIVIDEND_YIELD_DECIMAL = "0.00"
```

## One-shot collection

After applying the migrations separately and configuring the collector and
pricer environment, run one collection-and-pricing cycle with:

```powershell
nifty-vol-collect
```

Scheduling is deliberately external to the command. It writes the raw snapshot
through the collector role, then atomically publishes pricing through the
pricer role, and exits. The reader uses
`app.storage_adapter.StorageReaderAdapter` to project completed storage
snapshots; it never contacts NSE.

## Local reader

Either set `READER_DATABASE_URL` in the process environment as above, or copy
the ignored Streamlit secrets template:

```powershell
New-Item -ItemType Directory -Force .streamlit
Copy-Item .streamlit/secrets.toml.example .streamlit/secrets.toml
streamlit run app/streamlit_app.py
```

Run Streamlit from the repository root so local and Community Cloud paths are
consistent. Never commit `.streamlit/secrets.toml`.

## Streamlit Community Cloud

The root `requirements.txt` installs this project and the runtime dependencies
declared in `pyproject.toml`. In Community Cloud:

1. Select `app/streamlit_app.py` as the entrypoint and Python 3.12.
2. Add `READER_DATABASE_URL = "..."` in the app's Secrets editor, using only the
   SELECT-only reader role.
3. Deploy. Do not add `COLLECTOR_DATABASE_URL` to Streamlit; collection remains
   a separate local process.

See Streamlit's official documentation for
[dependencies](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)
and [secrets](https://docs.streamlit.io/develop/concepts/connections/secrets-management).

## Validation

```powershell
python -m compileall -q src
python -m pytest
python -m ruff check .
```
