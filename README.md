# NIFTY implied-volatility explorer

Initial Python foundation for an analytical application that will collect NIFTY
option-chain snapshots, derive implied volatility and Greeks, persist atomic
snapshots, and present them through a separate reader.

This repository is currently a scaffold only. It contains no market-data,
pricing, persistence, or user-interface implementation.

## Structure

```text
src/nifty_vol/
  domain/       Pricing and volatility domain boundary
  collector/    NSE collection boundary
  storage/      Snapshot persistence boundary
app/            Reader application entry points (future)
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

## Validation

```powershell
python -m compileall -q src
python -m pytest
python -m ruff check .
```

An empty test suite is expected at this foundation stage; tests will be added
alongside application behavior.
