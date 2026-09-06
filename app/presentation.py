"""Pure presentation logic shared by Streamlit and tests."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone

from app.models import CALCULATED, KNOWN_STATUSES, Contract, Snapshot

# India has observed UTC+05:30 without daylight-saving transitions since 1945.
# A fixed offset keeps the reader portable to minimal Windows/Python images that
# do not bundle the optional IANA ``tzdata`` package.
KOLKATA = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")

STATUS_LABELS = {
    CALCULATED: "Included (calculated)",
    "excluded_zero_volume": "Excluded: zero volume",
    "excluded_low_premium": "Excluded: low premium",
    "excluded_outside_strike_range": "Excluded: outside strike range",
    "invalid_market_data": "Excluded: invalid market data",
    "invalid_model_input": "Excluded: invalid model input",
    "solver_did_not_converge": "Excluded: solver did not converge",
}

GREEK_EXPLANATIONS = {
    "Delta": "Estimated option-price change for a one-point move in NIFTY.",
    "Gamma": "Estimated change in delta for a one-point move in NIFTY.",
    "Vega": "Estimated price change for a one-percentage-point rise in volatility.",
    "Theta": (
        "Estimated option-price change as one calendar day passes, all else equal."
    ),
}


@dataclass(frozen=True, slots=True)
class Freshness:
    label: str
    tone: str
    age: timedelta


@dataclass(frozen=True, slots=True)
class SmileSeries:
    name: str
    expiry: date
    option_type: str
    strikes: tuple[float, ...]
    volatilities: tuple[float, ...]
    historical: bool = False


@dataclass(frozen=True, slots=True)
class SmileChart:
    series: tuple[SmileSeries, ...]
    atm_iv: float | None
    atm_strike: float | None
    reference_expiry: date


def require_aware_utc(value: datetime) -> datetime:
    """Validate the frozen timestamp contract and normalize to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("snapshot timestamp must be timezone-aware")
    return value.astimezone(UTC)


def format_timestamp(value: datetime) -> str:
    local = require_aware_utc(value).astimezone(KOLKATA)
    return local.strftime("%d %b %Y, %H:%M:%S IST")


def freshness(
    captured_at: datetime,
    *,
    now: datetime | None = None,
    fresh_for: timedelta = timedelta(minutes=15),
    stale_after: timedelta = timedelta(hours=1),
) -> Freshness:
    captured = require_aware_utc(captured_at)
    current = require_aware_utc(now or datetime.now(UTC))
    age = max(current - captured, timedelta())
    if age <= fresh_for:
        return Freshness("Fresh", "success", age)
    if age <= stale_after:
        return Freshness("Aging", "warning", age)
    return Freshness("Stale", "error", age)


def format_age(age: timedelta) -> str:
    seconds = max(0, int(age.total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def available_expiries(snapshot: Snapshot) -> tuple[date, ...]:
    return tuple(sorted({contract.expiry for contract in snapshot.contracts}))


def status_counts(snapshot: Snapshot) -> dict[str, int]:
    counts = Counter(contract.status for contract in snapshot.contracts)
    return {status: counts.get(status, 0) for status in KNOWN_STATUSES}


def included_excluded(snapshot: Snapshot) -> tuple[int, int]:
    counts = status_counts(snapshot)
    included = counts[CALCULATED]
    return included, sum(counts.values()) - included


def build_smile_chart(
    snapshot: Snapshot,
    expiries: tuple[date, ...],
    reference_expiry: date,
    historical: Snapshot | None = None,
    *,
    option_types: tuple[str, ...] | None = None,
    valuations: tuple[str, ...] | None = None,
) -> SmileChart:
    """Build chart-ready data and a near-forward ATM-IV reference."""
    selected_option_types = set(option_types or ("call", "put"))
    selected_valuations = None if valuations is None else set(valuations)

    def is_selected(contract: Contract) -> bool:
        valuation = contract.valuation or "unscored"
        return contract.option_type in selected_option_types and (
            selected_valuations is None or valuation in selected_valuations
        )

    series: list[SmileSeries] = []
    for source, is_historical in ((snapshot, False), (historical, True)):
        if source is None:
            continue
        for expiry in expiries:
            for option_type in ("call", "put"):
                contracts = sorted(
                    (
                        contract
                        for contract in source.contracts
                        if contract.expiry == expiry
                        and contract.option_type == option_type
                        and is_selected(contract)
                        and contract.status == CALCULATED
                        and contract.implied_volatility is not None
                    ),
                    key=lambda item: item.strike,
                )
                if contracts:
                    history_label = " · comparison" if is_historical else ""
                    series.append(
                        SmileSeries(
                            name=(
                                f"{expiry.isoformat()} · {option_type.title()}"
                                f"{history_label}"
                            ),
                            expiry=expiry,
                            option_type=option_type,
                            strikes=tuple(item.strike for item in contracts),
                            volatilities=tuple(
                                item.implied_volatility for item in contracts
                            ),
                            historical=is_historical,
                        )
                    )

    reference_contracts = [
        contract
        for contract in snapshot.contracts
        if contract.expiry == reference_expiry
        and is_selected(contract)
        and contract.status == CALCULATED
        and contract.implied_volatility is not None
    ]
    anchor = snapshot.forward_for(reference_expiry)
    if not reference_contracts:
        return SmileChart(tuple(series), None, None, reference_expiry)
    atm_strike = min(
        {item.strike for item in reference_contracts},
        key=lambda strike: abs(strike - anchor),
    )
    atm_values = [
        item.implied_volatility
        for item in reference_contracts
        if item.strike == atm_strike and item.implied_volatility is not None
    ]
    return SmileChart(
        tuple(series),
        sum(atm_values) / len(atm_values),
        atm_strike,
        reference_expiry,
    )


def selectable_contracts(snapshot: Snapshot, expiry: date) -> tuple[Contract, ...]:
    return tuple(
        sorted(
            (
                contract
                for contract in snapshot.contracts
                if contract.expiry == expiry and contract.status == CALCULATED
            ),
            key=lambda item: (item.strike, item.option_type),
        )
    )
