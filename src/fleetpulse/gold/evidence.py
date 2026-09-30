"""Structured evidence objects for a future FleetPulse AI Analyst (no LLM is called).

The analyst may only explain these objects: every number is pre-computed by
Gold, and each object states what may and may not be claimed.
"""

from __future__ import annotations

from fleetpulse.gold.config import IdleRuleConfig

ALLOWED_CLAIMS = [
    "the engine was running while the vehicle was stationary for the stated duration",
    "the stated fuel was consumed during this period (observed, from the FMS fuel-rate signal)",
    "the descriptive tags (e.g. cold engine at start, raised engine speed) were measured",
]
PROHIBITED_CLAIMS = [
    "fuel_waste", "avoidable_idling", "cost_savings", "driver_fault", "cause_of_stop",
    "location_or_activity", "comparison_to_other_vehicles_or_fleets",
]
LIMITATIONS = [
    "no GPS/location, job or loading context: the reason for the stop is unknown",
    "single vehicle (Turku Renault T520, part_3); timestamps are logger wall clock, timezone unknown",
    "fuel rate is an FMS indication (not contractual); reconciled with the fuel counter per session",
    "PTO, cab heating and idle-increment switch states are not decoded",
]


def evidence_object(e: dict, cfg: IdleRuleConfig) -> dict:
    return {
        "evidence_id": e["event_id"],
        "event_type": "engine_on_stationary",
        "rule": {
            "version": cfg.rule_version,
            "definition": (f"engine_speed_rpm >= {cfg.engine_on_min_rpm:g} AND wheel_speed_kph < {cfg.stationary_max_kph:g}, "
                           "both fresh and valid, contiguous within one recording session, "
                           f">= {cfg.min_event_s:g} s; broken by movement, engine off, unknown signals or session end"),
            "thresholds_are_development_defaults": True,
        },
        "vehicle_id": e["vehicle_id"],
        "recording_session_id": e["recording_session_id"],
        "time_range": {"start": str(e["event_start"]), "end": str(e["event_end"]),
                       "timezone": "unknown (logger wall clock)"},
        "measured": {
            "duration_seconds": round(e["duration_seconds"], 1),
            "fuel_consumed_l": round(e["fuel_consumed_l"] or 0.0, 3),
            "fuel_method": e["fuel_method"],
            "average_fuel_rate_lph": _r(e["average_fuel_rate_lph"]),
            "median_fuel_rate_lph": _r(e["median_fuel_rate_lph"]),
            "maximum_fuel_rate_lph": _r(e["maximum_fuel_rate_lph"]),
            "average_engine_rpm": _r(e["average_engine_rpm"], 1),
            "average_engine_load_pct": _r(e["average_engine_load_pct"], 1),
            "coolant_start_c": e["coolant_start_c"], "coolant_end_c": e["coolant_end_c"],
            "fuel_counter_start_l": e["fuel_start_l"], "fuel_counter_end_l": e["fuel_end_l"],
            "preceded_by": e["preceded_by"], "followed_by": e["followed_by"],
            "descriptive_tags": list(e["descriptive_tags"]),
        },
        "supporting_signals": [
            {"signal": "engine_speed_rpm", "pgn": 61444, "spn": 190, "requirement": "fresh and valid on every row"},
            {"signal": "wheel_speed_kph", "pgn": 65265, "spn": 84, "requirement": "fresh and valid on every row"},
            {"signal": "fuel_rate_lph", "pgn": 65266, "spn": 183,
             "freshness": f"fresh+valid on {round(e['fuel_coverage_pct'], 2)} % of rows"},
            {"signal": "coolant_temp_c", "pgn": 65262, "spn": 110, "use": "context tag only"},
        ],
        "confidence": {
            "detection": e["detection_confidence"],
            "detection_basis": "verified FMS signals, freshness-checked; fuel coverage drives the level",
            "interpretation": e["interpretation_status"],
            "interpretation_basis": "no operational context available to classify the stop",
        },
        "limitations": LIMITATIONS,
        "allowed_claims": ALLOWED_CLAIMS,
        "prohibited_claims": PROHIBITED_CLAIMS,
        "lineage": {
            "gold_build_id": e["gold_build_id"], "silver_build_id": e["silver_build_id"],
            "table": "gold_idle_events", "event_id": e["event_id"],
            "session_source_files": [e["session_first_source_file"], e["session_last_source_file"]],
        },
    }


def _r(v, n=3):
    return None if v is None else round(v, n)


def select_important(events: list[dict], cfg: IdleRuleConfig) -> list[dict]:
    """Events >= evidence_min_event_s, plus the top-N by fuel (deduplicated, sorted by start)."""
    long_ = [e for e in events if e["duration_seconds"] >= cfg.evidence_min_event_s]
    top = sorted(events, key=lambda e: -(e["fuel_consumed_l"] or 0.0))[: cfg.evidence_top_n_by_fuel]
    seen, out = set(), []
    for e in sorted(long_ + top, key=lambda e: e["event_start"]):
        if e["event_id"] not in seen:
            seen.add(e["event_id"])
            out.append(e)
    return out
