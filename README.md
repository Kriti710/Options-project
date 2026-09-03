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

Copy `.env.example` to `.env` and provide local values when implementation
begins. Never commit the populated file.

## One-shot collection

After applying the migrations and configuring `.env` values in the process
environment, run one collection with:

```powershell
nifty-vol-collect
```

Scheduling is deliberately external to the command. It performs one fetch and
one atomic snapshot write, then exits. The reader uses
`app.storage_adapter.StorageReaderAdapter` to project completed storage
snapshots; it never contacts NSE.

## Validation

```powershell
python -m compileall -q src
python -m pytest
python -m ruff check .
```
