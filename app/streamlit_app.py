"""Streamlit entry point for the NIFTY implied-volatility reader."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import pandas as pd
import psycopg
import streamlit as st

from app.charts import plotly_smile_figure
from app.demo_data import build_demo_repository
from app.models import RepositoryUnavailable, SnapshotRepository
from app.presentation import (
    GREEK_EXPLANATIONS,
    STATUS_LABELS,
    available_expiries,
    build_smile_chart,
    format_age,
    format_timestamp,
    freshness,
    included_excluded,
    require_aware_utc,
    selectable_contracts,
    status_counts,
)
from app.storage_adapter import StorageReaderAdapter
from nifty_vol.settings import MissingConfigurationError, ReaderEnvironmentConfig
from nifty_vol.storage import SnapshotRepository as DatabaseSnapshotRepository

DISCLAIMER = (
    "Educational analytics only — not investment advice, a trading signal, "
    "or a live market-data terminal. Verify data independently before making "
    "financial decisions."
)

VALUATION_LABELS = ("Cheap", "Fair", "Expensive", "Unscored")
CONTRACT_COLUMNS = (
    "Strike",
    "Type",
    "Market price",
    "Approx fitted price",
    "Richness (₹)",
    "Implied vol",
    "Fitted vol",
    "IV residual",
    "Richness z",
    "Valuation",
    "Delta (₹ / point)",
    "Gamma (Δ / point)",
    "Vega (₹ / vol point)",
    "Theta (₹ / day)",
    "Price source",
)


class MissingReaderConfiguration(RepositoryUnavailable):
    """Reader credentials were not supplied by either supported source."""


class ReaderDatabaseUnavailable(RepositoryUnavailable):
    """The configured reader database could not be opened or queried."""


class UnconfiguredRepository:
    """Safe default: explicit failure instead of fetching from NSE."""

    def list_completed_snapshots(self) -> Sequence:
        raise MissingReaderConfiguration

    def get_completed_snapshot(self, snapshot_id: str):
        raise MissingReaderConfiguration


class UnavailableRepository:
    """Safe failure boundary that never carries connection diagnostics."""

    def list_completed_snapshots(self) -> Sequence:
        raise ReaderDatabaseUnavailable

    def get_completed_snapshot(self, snapshot_id: str):
        raise ReaderDatabaseUnavailable


def _connection_is_open(connection) -> bool:
    return not connection.closed


def _close_connection(connection) -> None:
    connection.close()


def _open_reader_connection(database_url: str):
    return psycopg.connect(
        database_url,
        autocommit=True,
        options="-c default_transaction_read_only=on",
    )


@st.cache_resource(
    show_spinner=False,
    validate=_connection_is_open,
    scope="session",
    on_release=_close_connection,
)
def _cached_reader_connection(_database_url: str):
    """Create a cached read-only, autocommit PostgreSQL connection."""

    return _open_reader_connection(_database_url)


def configured_repository(*, st_module=st) -> SnapshotRepository:
    """Compose the reader from local env or Streamlit-managed secrets."""

    settings = ReaderEnvironmentConfig.from_sources(secrets=st_module.secrets)
    connection = _cached_reader_connection(settings.reader_database_url)
    return StorageReaderAdapter(DatabaseSnapshotRepository(connection))


def _snapshot_label(summary) -> str:
    return f"{format_timestamp(summary.captured_at)} · {summary.snapshot_id}"


def _render_freshness(st, captured_at: datetime) -> None:
    state = freshness(captured_at)
    colors = {"success": "green", "warning": "orange", "error": "red"}
    st.badge(
        f"{state.label} · {format_age(state.age)}",
        icon=":material/schedule:",
        color=colors[state.tone],
    )
    if state.tone == "error":
        st.caption(
            "This snapshot is over one hour old. It remains available for "
            "analysis, but should not be treated as current."
        )


def _render_counts(st, snapshot) -> None:
    included, excluded = included_excluded(snapshot)
    with st.expander(
        "Data quality and model assumptions", icon=":material/fact_check:"
    ):
        with st.container(horizontal=True):
            st.metric("Included contracts", included, border=True)
            st.metric("Excluded or failed", excluded, border=True)
        st.caption(
            "Only contracts with status ‘calculated’ appear in charts. Midpoint "
            "is preferred when valid bid and ask exist; otherwise last traded "
            "price is used. Rates and IV are stored as decimals. Expiry is 15:30 "
            "Asia/Kolkata and time uses ACT/365F."
        )
        if snapshot.thresholds:
            st.markdown("**Effective snapshot thresholds**")
            for key, value in sorted(snapshot.thresholds.items()):
                st.write(f"{key.replace('_', ' ').title()}: {value}")
        else:
            st.warning(
                "Effective filtering thresholds were not supplied by the repository."
            )
        counts = status_counts(snapshot)
        st.dataframe(
            [
                {"Outcome": STATUS_LABELS[status], "Contracts": count}
                for status, count in counts.items()
            ],
            hide_index=True,
            width="stretch",
        )


def _valuation_label(contract) -> str:
    return "Unscored" if contract.valuation is None else contract.valuation.title()


def _filtered_contracts(
    snapshot,
    expiry: date,
    option_filter: str,
    valuation_filter: Sequence[str] | None = None,
):
    contracts = selectable_contracts(snapshot, expiry)
    if option_filter == "Calls":
        contracts = tuple(item for item in contracts if item.option_type == "call")
    elif option_filter == "Puts":
        contracts = tuple(item for item in contracts if item.option_type == "put")
    if valuation_filter is not None:
        selected = set(valuation_filter)
        contracts = tuple(
            item for item in contracts if _valuation_label(item) in selected
        )
    return contracts


def _contract_frame(contracts) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Strike": item.strike,
                "Type": item.option_type.title(),
                "Market price": item.market_price,
                "Approx fitted price": (
                    None
                    if item.market_price is None or item.richness_price is None
                    else item.market_price - item.richness_price
                ),
                "Richness (₹)": item.richness_price,
                "Implied vol": item.implied_volatility,
                "Fitted vol": item.fitted_iv,
                "IV residual": item.iv_residual,
                "Richness z": item.richness_z,
                "Valuation": [_valuation_label(item)],
                "Delta (₹ / point)": item.delta,
                "Gamma (Δ / point)": item.gamma,
                "Vega (₹ / vol point)": item.vega,
                "Theta (₹ / day)": item.theta,
                "Price source": item.price_source or "Not recorded",
            }
            for item in contracts
        ],
        columns=CONTRACT_COLUMNS,
    )


def _number_columns(st) -> dict:
    return {
        "Strike": st.column_config.NumberColumn(format="%,.0f", pinned=True),
        "Market price": st.column_config.NumberColumn(format="₹%,.2f"),
        "Approx fitted price": st.column_config.NumberColumn(format="₹%,.2f"),
        "Richness (₹)": st.column_config.NumberColumn(format="₹%+.2f"),
        "Implied vol": st.column_config.NumberColumn(format="percent"),
        "Fitted vol": st.column_config.NumberColumn(format="percent"),
        "IV residual": st.column_config.NumberColumn(format="%+.2%"),
        "Richness z": st.column_config.NumberColumn(format="%+.2f"),
        "Delta (₹ / point)": st.column_config.NumberColumn(format="%+.4f"),
        "Gamma (Δ / point)": st.column_config.NumberColumn(format="%.6f"),
        "Vega (₹ / vol point)": st.column_config.NumberColumn(format="%.3f"),
        "Theta (₹ / day)": st.column_config.NumberColumn(format="%+.3f"),
        "Valuation": st.column_config.MultiselectColumn(
            options=VALUATION_LABELS,
            color=("#2563eb", "#64748b", "#dc2626", "#94a3b8"),
        ),
    }


def _render_contract_summary(st, selected) -> None:
    st.subheader("Focused contract", icon=":material/center_focus_strong:")
    valuation = _valuation_label(selected)
    st.badge(
        valuation,
        icon=":material/price_check:",
        color={
            "Cheap": "blue",
            "Fair": "gray",
            "Expensive": "red",
            "Unscored": "gray",
        }[valuation],
    )
    with st.container(horizontal=True):
        st.metric(
            "Market price",
            "—" if selected.market_price is None else f"₹{selected.market_price:,.2f}",
            border=True,
        )
        st.metric(
            "Approx fitted price",
            "—"
            if selected.market_price is None or selected.richness_price is None
            else f"₹{selected.market_price - selected.richness_price:,.2f}",
            border=True,
            help="Market price minus the vega-linearized richness estimate.",
        )
        st.metric(
            "Implied volatility",
            "—"
            if selected.implied_volatility is None
            else f"{selected.implied_volatility:.2%}",
            delta=(
                None
                if selected.iv_residual is None
                else f"{selected.iv_residual:+.2%} vs fitted"
            ),
            border=True,
        )
        st.metric(
            "Relative value",
            "Unscored"
            if selected.valuation is None
            else selected.valuation.title(),
            delta=(
                None
                if selected.richness_z is None or selected.richness_price is None
                else f"₹{selected.richness_price:+.2f} · z = {selected.richness_z:+.2f}"
            ),
            delta_color="off",
            border=True,
        )
    st.caption(
        "Richness is the signed vega-linearized ₹ distance from the fitted smile: "
        "positive is rich/expensive and negative is cheap. It is descriptive, "
        "not a trading recommendation."
    )


def _render_risk_metrics(st, selected) -> None:
    with st.container(horizontal=True):
        for name, label, value in (
            ("Delta", "Delta · ₹ / NIFTY point", selected.delta),
            ("Gamma", "Gamma · Δ / NIFTY point", selected.gamma),
            ("Vega", "Vega · ₹ / 1 vol point", selected.vega),
            ("Theta", "Theta · ₹ / calendar day", selected.theta),
        ):
            st.metric(
                label,
                "—" if value is None else f"{value:+.4f}",
                border=True,
                help=GREEK_EXPLANATIONS[name],
            )


def render(repository: SnapshotRepository, *, st_module=None) -> None:
    """Render the application against an injected read-only repository."""
    if st_module is None:  # pragma: no cover - exercised by Streamlit itself
        import streamlit as st_module
    st = st_module
    st.set_page_config(
        page_title="NIFTY Volatility Explorer",
        page_icon=":material/query_stats:",
        layout="wide",
    )
    st.title(":material/query_stats: NIFTY volatility & pricing explorer")
    st.caption(DISCLAIMER)

    is_demo = bool(getattr(repository, "is_demo", False))
    if is_demo:
        st.warning(
            "Sample-data preview — the reader database is not configured on this "
            "computer. Every dashboard feature is available below, but the values "
            "are illustrative and no request was made to NSE.",
            icon=":material/science:",
        )

    try:
        summaries = sorted(
            repository.list_completed_snapshots(),
            key=lambda item: require_aware_utc(item.captured_at),
            reverse=True,
        )
    except MissingReaderConfiguration:
        st.error("Reader database is not configured.")
        st.info(
            "Set READER_DATABASE_URL locally or in Streamlit secrets. "
            "No request was made to NSE."
        )
        return
    except Exception:
        st.error("Data could not be loaded. The snapshot database is unavailable.")
        st.info(
            "No request was made to NSE. Try again after the snapshot repository "
            "recovers."
        )
        return
    if not summaries:
        st.info("No completed snapshots are available yet.")
        st.caption(
            "The reader displays only atomically published, completed snapshots."
        )
        return

    with st.sidebar:
        st.header("Dashboard filters", icon=":material/tune:")
        selected_summary = st.selectbox(
            "Snapshot",
            summaries,
            format_func=_snapshot_label,
            help="Choose the exact historical snapshot to inspect.",
        )
        comparison_options = [
            None,
            *[
                item
                for item in summaries
                if require_aware_utc(item.captured_at)
                < require_aware_utc(selected_summary.captured_at)
            ],
        ]
        comparison_summary = st.selectbox(
            "Compare with earlier snapshot",
            comparison_options,
            format_func=lambda item: (
                "No comparison" if item is None else _snapshot_label(item)
            ),
        )
    try:
        snapshot = repository.get_completed_snapshot(selected_summary.snapshot_id)
        comparison = (
            repository.get_completed_snapshot(comparison_summary.snapshot_id)
            if comparison_summary is not None
            else None
        )
    except Exception:
        st.error("The selected snapshot could not be loaded from the database.")
        st.info("Select another completed snapshot or try again later.")
        return

    expiries = available_expiries(snapshot)
    if not expiries:
        st.info("This completed snapshot contains no option contracts.")
        return

    with st.sidebar:
        primary_expiry = st.selectbox(
            "Primary expiry",
            expiries,
            format_func=lambda item: item.strftime("%d %b %Y"),
        )
        overlay_expiries = st.multiselect(
            "Volatility overlays",
            [item for item in expiries if item != primary_expiry],
            format_func=lambda item: item.strftime("%d %b %Y"),
            help="Compare smile shape and term structure across expiries.",
        )
        option_filter = st.segmented_control(
            "Option type", ("All", "Calls", "Puts"), default="All"
        )
        valuation_filter = st.pills(
            "Relative value",
            VALUATION_LABELS,
            default=VALUATION_LABELS,
            selection_mode="multi",
            help="Filter every chart and table by persisted valuation label.",
        )
        focused_contracts = _filtered_contracts(
            snapshot,
            primary_expiry,
            option_filter or "All",
            valuation_filter,
        )
        if focused_contracts:
            forward = snapshot.forward_for(primary_expiry)
            focus_index = min(
                range(len(focused_contracts)),
                key=lambda index: abs(focused_contracts[index].strike - forward),
            )
            selected_contract = st.selectbox(
                "Focused contract",
                focused_contracts,
                index=focus_index,
                format_func=lambda item: (
                    f"{item.strike:,.0f} · {item.option_type.title()}"
                ),
            )
        else:
            selected_contract = None
        st.caption(
            "Reader mode: sample preview" if is_demo else "Reader mode: database"
        )

    selected_expiries = (primary_expiry, *overlay_expiries)
    selected_option_types = {
        "Calls": ("call",),
        "Puts": ("put",),
    }.get(option_filter or "All", ("call", "put"))
    selected_valuations = tuple(item.lower() for item in valuation_filter)
    chart = build_smile_chart(
        snapshot,
        selected_expiries,
        primary_expiry,
        historical=comparison,
        option_types=selected_option_types,
        valuations=selected_valuations,
    )

    included, excluded = included_excluded(snapshot)
    with st.container(horizontal=True):
        st.metric("NIFTY spot", f"{snapshot.spot:,.2f}", border=True)
        st.metric(
            "Expiry forward",
            f"{snapshot.forward_for(primary_expiry):,.2f}",
            border=True,
        )
        st.metric(
            "Near-forward ATM IV",
            "—" if chart.atm_iv is None else f"{chart.atm_iv:.2%}",
            border=True,
        )
        st.metric(
            "Contracts",
            f"{included} priced",
            delta=f"{excluded} excluded",
            delta_color="off",
            border=True,
        )

    with st.container(horizontal=True, vertical_alignment="center"):
        _render_freshness(st, snapshot.captured_at)
        st.caption(f"Captured {format_timestamp(snapshot.captured_at)}")

    contracts = _filtered_contracts(
        snapshot,
        primary_expiry,
        option_filter or "All",
        valuation_filter,
    )
    frame = _contract_frame(contracts)
    smile = snapshot.smile_for(primary_expiry)
    curve_contracts = _filtered_contracts(
        snapshot, primary_expiry, option_filter or "All"
    )
    curve_strikes = sorted({item.strike for item in curve_contracts})
    if len(curve_strikes) >= 2:
        curve_step = (curve_strikes[-1] - curve_strikes[0]) / 120
        fitted_strikes = tuple(
            curve_strikes[0] + curve_step * index for index in range(121)
        )
    else:
        fitted_strikes = tuple(curve_strikes)
    fitted_curve = (
        tuple(
            (strike, smile.evaluate(strike))
            for strike in fitted_strikes
        )
        if smile is not None
        else ()
    )

    volatility_tab, pricing_tab, risk_tab, chain_tab = st.tabs(
        (
            ":material/show_chart: Volatility",
            ":material/payments: Pricing",
            ":material/shield: Risk",
            ":material/table_chart: Option chain",
        )
    )
    with volatility_tab:
        with st.container(border=True):
            st.subheader("Observed and fitted volatility smile")
            if not chart.series:
                st.info("No calculated implied-volatility points match this selection.")
            else:
                st.plotly_chart(
                    plotly_smile_figure(
                        chart,
                        fitted_curve=fitted_curve,
                        valuation_points=tuple(
                            (
                                item.strike,
                                item.implied_volatility,
                                _valuation_label(item),
                                item.option_type,
                            )
                            for item in contracts
                            if item.implied_volatility is not None
                        ),
                    ),
                    width="stretch",
                    theme="streamlit",
                )
                if chart.atm_iv is None:
                    st.warning(
                        "A near-forward ATM-IV reference could not be calculated."
                    )
                else:
                    anchor = snapshot.forward_for(primary_expiry)
                    st.caption(
                        f"ATM reference uses strike {chart.atm_strike:,.0f}, nearest "
                        f"the expiry forward ({anchor:,.2f}). The grey line is the "
                        "persisted fitted smile used for relative-value scoring."
                    )

    with pricing_tab:
        if frame.empty:
            st.info("No calculated contracts match the selected filters.")
        else:
            if selected_contract is not None:
                _render_contract_summary(st, selected_contract)
            with st.container(border=True):
                st.subheader("Relative-value surface")
                st.caption(
                    "Richness is vega × IV residual / 0.01. Positive values are "
                    "rich; negative values are cheap. IV values are stored as "
                    "decimals and formatted here as percentages."
                )
                st.dataframe(
                    frame[
                        [
                            "Strike",
                            "Type",
                            "Market price",
                            "Approx fitted price",
                            "Richness (₹)",
                            "Implied vol",
                            "Fitted vol",
                            "IV residual",
                            "Richness z",
                            "Valuation",
                        ]
                    ],
                    column_config=_number_columns(st),
                    hide_index=True,
                    width="stretch",
                    height=430,
                )

    with risk_tab:
        if frame.empty:
            st.info("No calculated contracts match the selected filters.")
        else:
            if selected_contract is not None:
                st.subheader("Focused contract sensitivities")
                _render_risk_metrics(st, selected_contract)
            left, right = st.columns(2)
            with left.container(border=True):
                st.subheader("Directional risk")
                st.line_chart(
                    frame, x="Strike", y="Delta (₹ / point)", color="Type"
                )
            with right.container(border=True):
                st.subheader("Convexity")
                st.line_chart(
                    frame, x="Strike", y="Gamma (Δ / point)", color="Type"
                )
            with st.container(border=True):
                st.subheader("Greeks by contract")
                st.dataframe(
                    frame[
                        [
                            "Strike",
                            "Type",
                            "Delta (₹ / point)",
                            "Gamma (Δ / point)",
                            "Vega (₹ / vol point)",
                            "Theta (₹ / day)",
                        ]
                    ],
                    column_config=_number_columns(st),
                    hide_index=True,
                    width="stretch",
                    height=400,
                )

    with chain_tab:
        if frame.empty:
            st.info("No calculated contracts match the selected filters.")
        else:
            with st.container(border=True):
                st.subheader("Calculated option chain")
                st.dataframe(
                    frame,
                    column_config=_number_columns(st),
                    hide_index=True,
                    width="stretch",
                    height=520,
                )
        _render_counts(st, snapshot)

    st.caption(DISCLAIMER)


def main(
    repository: SnapshotRepository | None = None,
    *,
    st_module=st,
) -> None:
    """Application composition hook for Streamlit deployments."""

    if repository is None:
        try:
            repository = configured_repository(st_module=st_module)
        except MissingConfigurationError:
            repository = (
                build_demo_repository()
                if st_module is st
                else UnconfiguredRepository()
            )
        except Exception:
            repository = UnavailableRepository()
    render(repository, st_module=st_module)


if __name__ == "__main__":  # pragma: no cover
    main()
