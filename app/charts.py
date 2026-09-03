"""Plotly adapter for reader chart data."""

from __future__ import annotations

from app.presentation import SmileChart


def plotly_smile_figure(chart: SmileChart):
    """Return a Plotly figure; import lazily so pure tests need no UI extras."""
    try:
        import plotly.graph_objects as go
    except ImportError as exc:  # pragma: no cover - depends on deployment
        raise RuntimeError("Plotly is required to render the smile chart") from exc

    figure = go.Figure()
    colors = {"call": "#2563eb", "put": "#dc2626"}
    current_expiries = tuple(
        dict.fromkeys(item.expiry for item in chart.series if not item.historical)
    )
    expiry_dashes = ("solid", "dash", "longdash", "dashdot")
    for item in chart.series:
        expiry_index = (
            current_expiries.index(item.expiry)
            if item.expiry in current_expiries
            else 0
        )
        figure.add_trace(
            go.Scatter(
                x=item.strikes,
                y=[value * 100 for value in item.volatilities],
                name=item.name,
                mode="lines+markers",
                line={
                    "color": colors[item.option_type],
                    "dash": (
                        "dot"
                        if item.historical
                        else expiry_dashes[expiry_index % len(expiry_dashes)]
                    ),
                },
                marker={
                    "symbol": "circle" if item.option_type == "call" else "diamond"
                },
                opacity=0.55 if item.historical else 0.95,
                hovertemplate=(
                    "Strike %{x:,.0f}<br>IV %{y:.2f}%"
                    "<extra>%{fullData.name}</extra>"
                ),
            )
        )
    if chart.atm_iv is not None:
        figure.add_hline(
            y=chart.atm_iv * 100,
            line_dash="dash",
            line_color="#64748b",
            annotation_text=(
                f"Near-forward ATM IV {chart.atm_iv:.2%} "
                f"(K={chart.atm_strike:,.0f})"
            ),
        )
    figure.update_layout(
        xaxis_title="Strike (index points)",
        yaxis_title="Implied volatility (annualized %)",
        hovermode="x unified",
        legend_title="Expiry · option type",
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
    )
    return figure
