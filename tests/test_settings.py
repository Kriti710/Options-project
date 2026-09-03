import pytest

from nifty_vol.settings import EnvironmentConfig


def test_environment_configuration_builds_collector_and_pipeline() -> None:
    settings = EnvironmentConfig.from_env(
        {
            "DATABASE_URL": "postgresql://example.invalid/db",
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


def test_environment_configuration_requires_secrets_and_rates() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        EnvironmentConfig.from_env({})
