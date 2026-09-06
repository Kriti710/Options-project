from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.demo_data import build_demo_repository
from app.streamlit_app import CONTRACT_COLUMNS, _contract_frame


def test_demo_repository_exposes_pricing_volatility_and_risk_fields() -> None:
    repository = build_demo_repository()
    summary = repository.list_completed_snapshots()[0]
    snapshot = repository.get_completed_snapshot(summary.snapshot_id)
    calculated = [item for item in snapshot.contracts if item.status == "calculated"]

    assert calculated
    assert snapshot.smiles
    assert {item.valuation for item in calculated} >= {"cheap", "fair", "expensive"}
    assert all(
        item.richness_price == item.vega * item.iv_residual / 0.01
        for item in calculated
    )
    assert all(item.delta is not None for item in calculated)


def test_empty_contract_frame_keeps_dashboard_schema() -> None:
    assert tuple(_contract_frame(()).columns) == CONTRACT_COLUMNS


def test_unconfigured_local_app_renders_complete_sample_dashboard() -> None:
    app = AppTest.from_file(
        Path(__file__).parents[2] / "app" / "streamlit_app.py",
        default_timeout=15,
    ).run()

    assert not app.exception
    assert len(app.tabs) == 4
    assert len(app.metric) >= 8
    assert len(app.dataframe) >= 4
    assert any("Sample-data preview" in warning.value for warning in app.warning)

    app.pills[0].set_value([]).run()
    assert not app.exception
    assert len(app.info) >= 4

    app.pills[0].set_value(["Cheap"]).run()
    app.segmented_control[0].set_value("Puts").run()
    assert not app.exception
    assert len(app.dataframe) >= 4
