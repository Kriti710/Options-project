"""Plotly adapter for reader chart data."""

from __future__ import annotations

from app.presentation import SmileChart


def plotly_smile_figure(
    chart: SmileChart,
    *,
    fitted_curve: tuple[tuple[float, float], ...] = (),
    valuation_points: tuple[tuple[float, float, str, str], ...] = (),
):
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
                mode="lines",
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
    valuation_colors = {
        "Cheap": "#2563eb",
        "Fair": "#64748b",
        "Expensive": "#dc2626",
        "Unscored": "#94a3b8",
    }
    option_symbols = {"call": "circle", "put": "diamond"}
    for valuation in valuation_colors:
        for option_type in option_symbols:
            points = [
                point
                for point in valuation_points
                if point[2] == valuation and point[3] == option_type
            ]
            if not points:
                continue
            figure.add_trace(
                go.Scatter(
                    x=[point[0] for point in points],
                    y=[point[1] * 100 for point in points],
                    name=f"{valuation} · {option_type.title()}",
                    mode="markers",
                    marker={
                        "color": valuation_colors[valuation],
                        "symbol": option_symbols[option_type],
                        "size": 9,
                        "line": {"color": "white", "width": 0.6},
                    },
                    hovertemplate=(
                        "Strike %{x:,.0f}<br>IV %{y:.2f}%"
                        f"<extra>{valuation} · {option_type.title()}</extra>"
                    ),
                )
            )
    if fitted_curve:
        figure.add_trace(
            go.Scatter(
                x=[point[0] for point in fitted_curve],
                y=[point[1] * 100 for point in fitted_curve],
                name="Fitted smile · reference",
                mode="lines",
                line={"color": "#64748b", "dash": "dash", "width": 3},
                hovertemplate=(
                    "Strike %{x:,.0f}<br>Fitted IV %{y:.2f}%"
                    "<extra>Reference smile</extra>"
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
