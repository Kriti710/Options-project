"""Environment-based composition settings with explicit parsing failures."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .collector import CollectorConfig
from .pipeline import PipelineConfig


class MissingConfigurationError(ValueError):
    """A required setting is absent without exposing any configured value."""


def _float(values: Mapping[str, str], name: str, default: float | None = None) -> float:
    raw = values.get(name)
    if raw is None or raw == "":
        if default is None:
            raise ValueError(f"{name} is required")
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc


def _int(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


@dataclass(frozen=True, slots=True)
class EnvironmentConfig:
    """Runtime configuration shared by one-shot collector composition."""

    collector_database_url: str
    pricer_database_url: str
    collector: CollectorConfig
    pipeline: PipelineConfig

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> EnvironmentConfig:
        values = os.environ if environ is None else environ
        database_url = values.get("COLLECTOR_DATABASE_URL", "").strip()
        if not database_url:
            raise MissingConfigurationError("COLLECTOR_DATABASE_URL is required")
        pricer_database_url = values.get("PRICER_DATABASE_URL", "").strip()
        if not pricer_database_url:
            raise MissingConfigurationError("PRICER_DATABASE_URL is required")
        collector = CollectorConfig(
            base_url=values.get("NSE_BASE_URL", "https://www.nseindia.com"),
            symbol=values.get("NSE_SYMBOL", "NIFTY"),
            timeout_seconds=_float(values, "NSE_TIMEOUT_SECONDS", 10.0),
            min_request_interval_seconds=_float(
                values, "COLLECTION_REQUEST_DELAY_SECONDS", 1.0
            ),
            max_retries=_int(values, "NSE_MAX_RETRIES", 2),
            retry_backoff_seconds=_float(
                values, "NSE_RETRY_BACKOFF_SECONDS", 1.0
            ),
        )
        pipeline = PipelineConfig(
            risk_free_rate=_float(values, "RISK_FREE_RATE_DECIMAL"),
            dividend_yield=_float(values, "DIVIDEND_YIELD_DECIMAL"),
            minimum_premium=_float(values, "MIN_OPTION_PREMIUM", 0.05),
            maximum_strike_distance=_float(
                values, "MAX_STRIKE_DISTANCE_FROM_SPOT_DECIMAL", 0.20
            ),
            price_tolerance=_float(values, "IV_SOLVER_PRICE_TOLERANCE", 1e-6),
            volatility_tolerance=_float(
                values, "IV_SOLVER_VOLATILITY_TOLERANCE", 1e-8
            ),
            minimum_volatility=_float(
                values, "IV_SOLVER_MIN_VOLATILITY_DECIMAL", 1e-6
            ),
            maximum_volatility=_float(
                values, "IV_SOLVER_MAX_VOLATILITY_DECIMAL", 5.0
            ),
            maximum_iterations=_int(values, "IV_SOLVER_MAX_ITERATIONS", 200),
        )
        return cls(database_url, pricer_database_url, collector, pipeline)


@dataclass(frozen=True, slots=True)
class ReaderEnvironmentConfig:
    """Read-only reader configuration from local env or Streamlit secrets."""

    reader_database_url: str

    @classmethod
    def from_sources(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        secrets: Mapping[str, object] | None = None,
    ) -> ReaderEnvironmentConfig:
        values = os.environ if environ is None else environ
        database_url = values.get("READER_DATABASE_URL", "").strip()
        if not database_url and secrets is not None:
            try:
                secret_value = secrets.get("READER_DATABASE_URL", "")
            except Exception:
                secret_value = ""
            if isinstance(secret_value, str):
                database_url = secret_value.strip()
        if not database_url:
            raise MissingConfigurationError("READER_DATABASE_URL is required")
        return cls(database_url)
