"""Idle-rule configuration. Every threshold is configurable; defaults are development
defaults derived from observations in this dataset (NOT industry standards)."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class IdleRuleConfig:
    rule_version: str = "idle-v1"
    # Engine running: EEC1 SPN 190 >= this. Observed in part_3: running idle is 566-640 rpm
    # (median 600), cranking 130-220 rpm, engine off 0 rpm. 300 separates cranking/off from running.
    engine_on_min_rpm: float = 300.0
    # Stationary: CCVS SPN 84 < this. Stopped reads exactly 0.0 km/h (1/256 km/h resolution);
    # creeping is ~1-2.5 km/h. Same value as the Stage 2/Discovery definitions (reconcilable).
    stationary_max_kph: float = 0.5
    # Minimum event duration. 2 s = Discovery V1 episode minimum (for reconciliation) and
    # 20 grid points, so a single-sample blip never forms an event. Shorter stationary runs
    # still count in session stationary time; they are just not emitted as events.
    min_event_s: float = 2.0
    # Event-size classes (reporting only)
    long_event_s: float = 900.0  # "> 15 min" KPI requested by the business question
    evidence_min_event_s: float = 300.0  # evidence objects for events >= 5 min ...
    evidence_top_n_by_fuel: int = 10  # ... plus the top N events by fuel
    # Descriptive tags (facts about the event, not causes)
    cold_coolant_c: float = 60.0  # coolant below this at event start -> "cold_engine_at_start"
    raised_idle_rpm: float = 700.0  # mean rpm above the observed idle band -> "raised_engine_speed"
    grid_period_s: float = 0.1  # Silver telemetry grid

    def to_dict(self) -> dict:
        return asdict(self)
