"""Streamlit entry point for the NIFTY implied-volatility reader."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import psycopg
import streamlit as st

from app.charts import plotly_smile_figure
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
    message = f"{state.label} · {format_age(state.age)}"
    if state.tone == "success":
        st.success(message)
    elif state.tone == "warning":
        st.warning(message)
    else:
        st.error(
            f"{message}. This snapshot is over one hour old; the last completed "
            "data remains available, but should not be treated as current."
        )


def _render_counts(st, snapshot) -> None:
    included, excluded = included_excluded(snapshot)
    left, right = st.columns(2)
    left.metric("Included contracts", included)
    right.metric("Excluded / failed", excluded)
    with st.expander("Filtering assumptions and exclusion detail"):
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
            use_container_width=True,
        )


def _render_contract(st, snapshot, expiry: date) -> None:
    choices = selectable_contracts(snapshot, expiry)
    st.subheader("Contract details")
    if not choices:
        st.info("No calculated contracts are available for this expiry.")
        return
    selected = st.selectbox(
        "Strike and option type",
        choices,
        format_func=lambda item: f"{item.strike:,.0f} · {item.option_type.title()}",
    )
    price = (
        "—"
        if selected.market_price is None
        else f"₹{selected.market_price:,.2f}"
    )
    iv = (
        "—"
        if selected.implied_volatility is None
        else f"{selected.implied_volatility:.2%}"
    )
    price_col, iv_col, source_col = st.columns(3)
    price_col.metric("Selected market price", price)
    iv_col.metric("Implied volatility", iv)
    source_col.metric("Price source", selected.price_source or "Not recorded")
    greek_values = {
        "Delta": selected.delta,
        "Gamma": selected.gamma,
        "Vega": selected.vega,
        "Theta": selected.theta,
    }
    columns = st.columns(4)
    for column, (name, value) in zip(columns, greek_values.items(), strict=True):
        column.metric(name, "—" if value is None else f"{value:.4f}")
        column.caption(GREEK_EXPLANATIONS[name])


def render(repository: SnapshotRepository, *, st_module=None) -> None:
    """Render the application against an injected read-only repository."""
    if st_module is None:  # pragma: no cover - exercised by Streamlit itself
        import streamlit as st_module
    st = st_module
    st.set_page_config(page_title="NIFTY Volatility Explorer", layout="wide")
    st.title("NIFTY implied-volatility explorer")
    st.caption(DISCLAIMER)

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
        "Compare with earlier snapshot (optional)",
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

    captured_label = format_timestamp(snapshot.captured_at)
    st.write(f"Snapshot captured: **{captured_label}**")
    _render_freshness(st, snapshot.captured_at)
    _render_counts(st, snapshot)
    expiries = available_expiries(snapshot)
    if not expiries:
        st.info("This completed snapshot contains no option contracts.")
        return

    primary_expiry = st.selectbox("Primary expiry", expiries)
    overlay_expiries = st.multiselect(
        "Additional expiry overlays",
        [item for item in expiries if item != primary_expiry],
        help="Overlay multiple expiries to compare smile shape and term structure.",
    )
    selected_expiries = (primary_expiry, *overlay_expiries)
    chart = build_smile_chart(
        snapshot,
        selected_expiries,
        primary_expiry,
        historical=comparison,
    )
    st.subheader("Call and put volatility smile")
    if not chart.series:
        st.info("No calculated implied-volatility points match this selection.")
    else:
        st.plotly_chart(
            plotly_smile_figure(chart), use_container_width=True, theme="streamlit"
        )
        if chart.atm_iv is None:
            st.warning("A near-forward ATM-IV reference could not be calculated.")
        else:
            anchor = snapshot.forward_for(primary_expiry)
            st.caption(
                f"Reference: average call/put IV at strike {chart.atm_strike:,.0f}, "
                f"the listed strike nearest the forward ({anchor:,.2f})."
            )
    _render_contract(st, snapshot, primary_expiry)
    st.divider()
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
            repository = UnconfiguredRepository()
        except Exception:
            repository = UnavailableRepository()
    render(repository, st_module=st_module)


if __name__ == "__main__":  # pragma: no cover
    main()
