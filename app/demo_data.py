"""Deterministic, clearly labelled sample data for the local UI preview."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from app.models import Contract, FittedSmile, Snapshot, SnapshotSummary
from nifty_vol.domain.black_scholes import black_scholes_greeks, black_scholes_price
from nifty_vol.domain.time import time_to_expiry


def _next_thursdays(today: date, count: int = 2) -> tuple[date, ...]:
    days_until_thursday = (3 - today.weekday()) % 7
    first = today + timedelta(days=days_until_thursday or 7)
    return tuple(first + timedelta(days=7 * offset) for offset in range(count))


def _valuation(z_score: float, residual: float) -> str:
    if abs(z_score) < 1.0 or abs(residual) < 0.0015:
        return "fair"
    if z_score <= -0.75:
        return "cheap"
    return "expensive"


def _sample_snapshot(
    *,
    snapshot_id: str,
    captured_at: datetime,
    spot: float,
    volatility_shift: float,
) -> Snapshot:
    expiries = _next_thursdays(captured_at.date())
    strikes = tuple(range(24_400, 25_601, 100))
    residual_pattern = (-0.012, -0.007, -0.003, 0.0, 0.003, 0.007, 0.012)
    contracts: list[Contract] = []
    forwards: dict[date, float] = {}
    smiles: dict[date, FittedSmile] = {}

    for expiry_index, expiry in enumerate(expiries):
        years = time_to_expiry(expiry_date=expiry, as_of=captured_at)
        forward = spot * math.exp(0.065 * years)
        forwards[expiry] = forward
        base_iv = 0.137 + volatility_shift + 0.008 * expiry_index
        smiles[expiry] = FittedSmile(
            expiry=expiry,
            forward=forward,
            c0=base_iv,
            c1=-0.055,
            c2=0.75,
            sample_size=len(strikes) * 2,
            residual_scale=0.007,
        )
        for strike_index, strike in enumerate(strikes):
            log_moneyness = math.log(strike / forward)
            fitted_iv = base_iv - 0.055 * log_moneyness + 0.75 * log_moneyness**2
            for option_type in ("call", "put"):
                direction = 1 if option_type == "call" else -1
                residual = residual_pattern[
                    (strike_index + direction + expiry_index) % len(residual_pattern)
                ]
                observed_iv = max(0.05, fitted_iv + residual)
                market_price = black_scholes_price(
                    S=spot,
                    K=float(strike),
                    T=years,
                    r=0.065,
                    q=0.0,
                    sigma=observed_iv,
                    option_type=option_type,
                )
                greeks = black_scholes_greeks(
                    S=spot,
                    K=float(strike),
                    T=years,
                    r=0.065,
                    q=0.0,
                    sigma=observed_iv,
                    option_type=option_type,
                )
                z_score = residual / 0.007
                richness_price = greeks.vega * residual / 0.01
                contracts.append(
                    Contract(
                        expiry=expiry,
                        strike=float(strike),
                        option_type=option_type,
                        status="calculated",
                        market_price=market_price,
                        price_source="midpoint",
                        implied_volatility=observed_iv,
                        delta=greeks.delta,
                        gamma=greeks.gamma,
                        vega=greeks.vega,
                        theta=greeks.theta,
                        fitted_iv=fitted_iv,
                        iv_residual=residual,
                        richness_price=richness_price,
                        richness_z=z_score,
                        valuation=_valuation(z_score, residual),
                    )
                )

    for offset, status in enumerate(
        (
            "excluded_zero_volume",
            "excluded_low_premium",
            "excluded_outside_strike_range",
            "solver_did_not_converge",
        )
    ):
        contracts.append(
            Contract(
                expiry=expiries[0],
                strike=float(23_900 - offset * 100),
                option_type="call" if offset % 2 == 0 else "put",
                status=status,
            )
        )

    return Snapshot(
        snapshot_id=snapshot_id,
        captured_at=captured_at,
        spot=spot,
        contracts=tuple(contracts),
        forwards=forwards,
        thresholds={
            "minimum premium": "₹0.05",
            "maximum strike distance": "20%",
            "model": "Black–Scholes–Merton",
        },
        smiles=smiles,
    )


class DemoRepository:
    """In-memory sample repository used only when local DB settings are absent."""

    is_demo = True

    def __init__(self, snapshots: Sequence[Snapshot]):
        self._snapshots = {snapshot.snapshot_id: snapshot for snapshot in snapshots}

    def list_completed_snapshots(self) -> list[SnapshotSummary]:
        return [
            SnapshotSummary(snapshot.snapshot_id, snapshot.captured_at)
            for snapshot in self._snapshots.values()
        ]

    def get_completed_snapshot(self, snapshot_id: str) -> Snapshot:
        return self._snapshots[snapshot_id]


def build_demo_repository(*, now: datetime | None = None) -> DemoRepository:
    """Return two snapshots so every dashboard view and comparison is usable."""

    current = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    return DemoRepository(
        (
            _sample_snapshot(
                snapshot_id="demo-current",
                captured_at=current - timedelta(minutes=2),
                spot=24_980.0,
                volatility_shift=0.0,
            ),
            _sample_snapshot(
                snapshot_id="demo-previous",
                captured_at=current - timedelta(minutes=32),
                spot=24_925.0,
                volatility_shift=0.006,
            ),
        )
    )
