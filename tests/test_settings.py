import pytest

from nifty_vol.settings import (
    EnvironmentConfig,
    MissingConfigurationError,
    ReaderEnvironmentConfig,
)


def test_environment_configuration_builds_collector_and_pipeline() -> None:
    settings = EnvironmentConfig.from_env(
        {
            "COLLECTOR_DATABASE_URL": "postgresql://writer.example.invalid/db",
            "RISK_FREE_RATE_DECIMAL": "0.065",
            "DIVIDEND_YIELD_DECIMAL": "0.012",
            "MIN_OPTION_PREMIUM": "0.5",
            "IV_SOLVER_VOLATILITY_TOLERANCE": "1e-9",
        }
    )
    assert settings.pipeline.risk_free_rate == 0.065
    assert settings.pipeline.minimum_premium == 0.5
    assert settings.pipeline.volatility_tolerance == 1e-9
    assert settings.collector.symbol == "NIFTY"
    assert "writer.example.invalid" in settings.collector_database_url


def test_environment_configuration_requires_secrets_and_rates() -> None:
    with pytest.raises(MissingConfigurationError, match="COLLECTOR_DATABASE_URL"):
        EnvironmentConfig.from_env({})


def test_reader_configuration_supports_env_and_streamlit_secrets() -> None:
    local = ReaderEnvironmentConfig.from_sources(
        environ={"READER_DATABASE_URL": "postgresql://local-reader/db"},
        secrets={"READER_DATABASE_URL": "postgresql://cloud-reader/db"},
    )
    cloud = ReaderEnvironmentConfig.from_sources(
        environ={},
        secrets={"READER_DATABASE_URL": "postgresql://cloud-reader/db"},
    )
    assert local.reader_database_url == "postgresql://local-reader/db"
    assert cloud.reader_database_url == "postgresql://cloud-reader/db"


def test_reader_configuration_reports_missing_value_without_a_secret() -> None:
    with pytest.raises(MissingConfigurationError, match="READER_DATABASE_URL"):
        ReaderEnvironmentConfig.from_sources(environ={}, secrets={})


def test_legacy_single_database_url_is_not_accepted() -> None:
    legacy = {"DATABASE_URL": "postgresql://over-privileged/db"}
    with pytest.raises(MissingConfigurationError):
        EnvironmentConfig.from_env(legacy)
    with pytest.raises(MissingConfigurationError):
        ReaderEnvironmentConfig.from_sources(environ=legacy, secrets={})
