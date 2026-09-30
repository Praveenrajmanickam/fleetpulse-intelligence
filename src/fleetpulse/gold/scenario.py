"""Impact SCENARIO calculator. Deterministic arithmetic on operator-supplied assumptions.

These outputs are SCENARIO / ESTIMATE values, never observed savings: FleetPulse
measures stationary fuel, but whether any of it was avoidable is an operator
assumption (``avoidable_pct``), and the price is an operator input too.
Projections to other periods are only produced when the caller explicitly
supplies an extrapolation assumption.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

LABEL = "SCENARIO / ESTIMATE - not observed savings"


@dataclass(frozen=True)
class Extrapolation:
    observed_period_days: float  # length of the period the observed fuel covers (operator-stated)
    projection_days: float  # e.g. 30 or 365
    assumption_note: str  # required: why the observed period is taken as representative

    def __post_init__(self):
        if self.observed_period_days <= 0 or self.projection_days <= 0:
            raise ValueError("extrapolation periods must be > 0")
        if not self.assumption_note.strip():
            raise ValueError("an extrapolation needs an explicit assumption note")


def idle_scenario(
    observed_stationary_fuel_l: float,
    avoidable_pct: float,
    fuel_price_per_l: float,
    currency: str = "EUR",
    extrapolation: Extrapolation | None = None,
) -> dict:
    if observed_stationary_fuel_l < 0:
        raise ValueError("observed_stationary_fuel_l must be >= 0")
    if not 0 <= avoidable_pct <= 100:
        raise ValueError("avoidable_pct must be between 0 and 100 (operator assumption)")
    if fuel_price_per_l < 0:
        raise ValueError("fuel_price_per_l must be >= 0")
    fuel = observed_stationary_fuel_l * avoidable_pct / 100
    out = {
        "label": LABEL,
        "inputs": {
            "observed_stationary_fuel_l": observed_stationary_fuel_l,
            "operator_selected_avoidable_percentage": avoidable_pct,
            "operator_selected_fuel_price_per_l": fuel_price_per_l,
            "currency": currency,
        },
        "scenario_avoidable_fuel_l": round(fuel, 6),
        "scenario_cost_exposure": round(fuel * fuel_price_per_l, 6),
        "observed_values": ["observed_stationary_fuel_l"],
        "assumed_values": ["operator_selected_avoidable_percentage", "operator_selected_fuel_price_per_l"],
        "projection": None,
    }
    if extrapolation is not None:
        factor = extrapolation.projection_days / extrapolation.observed_period_days
        out["projection"] = {
            "label": LABEL + " (extrapolated)",
            **asdict(extrapolation),
            "scale_factor": round(factor, 6),
            "projected_avoidable_fuel_l": round(fuel * factor, 6),
            "projected_cost_exposure": round(fuel * fuel_price_per_l * factor, 6),
        }
    return out
