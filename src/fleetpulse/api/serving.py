"""Presentation rules and fixed wording for the API. Everything here is documented in
docs/dashboard_v1.md; nothing re-derives a vehicle metric.

* Engine-operation sessions: recording sessions with >= 0.1 h engine-on time - the
  scope definition of the Gold Part 3 build (17 sessions). Short fragments remain in
  every Gold total and are listed explicitly.
* Attention groups: grouping of Gold's pre-computed ``duration_class`` values.
"""

from __future__ import annotations

import re

ENGINE_SESSION_MIN_ENGINE_ON_S = 360.0  # Gold scope definition (>= 0.1 h engine-on)

ATTENTION_GROUPS = {
    "routine_short": {
        "label": "Routine / short observations",
        "duration_classes": ["under_1_min", "1_to_5_min"],
        "description": "Stationary engine-on periods under 5 minutes. Typically between driving periods; "
                       "reported for completeness, not flagged.",
    },
    "extended": {
        "label": "Extended events for investigation",
        "duration_classes": ["5_to_15_min", "over_15_min"],
        "description": "Stationary engine-on periods of 5 minutes or more. Worth reviewing with operational "
                       "context; the telemetry does not identify their reason.",
    },
}
DURATION_CLASS_ORDER = ["under_1_min", "1_to_5_min", "5_to_15_min", "over_15_min"]
DURATION_CLASS_LABELS = {"under_1_min": "< 1 min", "1_to_5_min": "1–5 min", "5_to_15_min": "5–15 min",
                         "over_15_min": "> 15 min"}


def attention_group(duration_class: str) -> str:
    return "extended" if duration_class in ATTENTION_GROUPS["extended"]["duration_classes"] else "routine_short"


# --- fixed wording (checked by tests for prohibited terminology) -----------------
DETECTION_STATEMENT = {
    "high": "FleetPulse detected the vehicle state with high confidence.",
    "medium": "FleetPulse detected the vehicle state; fuel data was missing for part of the event.",
    "low": "FleetPulse detected the vehicle state; fuel data was missing for a large part of the event.",
}
INTERPRETATION_STATEMENT = ("The available telemetry does not identify why the vehicle remained stationary.")
MODULE_STATEMENT = ("Stationary engine-on time is measured, not judged: the reason (loading, queue, traffic, "
                    "warm-up, break or other) is not in the telemetry.")
SCOPE_STATEMENT = ("One vehicle (Turku Renault T520). Part 3 observation window. Not a fleet-wide measurement.")

PROHIBITED_TERMS = re.compile(r"\b(waste|wasted|wasting|saving|savings|avoidable|driver fault|fault|cause of (the )?stop)\b",
                              re.IGNORECASE)
