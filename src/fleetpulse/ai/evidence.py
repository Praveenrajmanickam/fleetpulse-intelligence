"""Deterministic evidence retrieval: compact, structured context built from Gold.

Never raw CAN frames, never Silver telemetry tables. Each package carries the facts
(with fixed display strings), interpretation statements, limitations, suggested
investigations, confidence and lineage for one scope. A few figures are *derived by
FleetPulse* from Gold rows (sums/shares over pre-classified events); they are labelled
as such and computed here, never by the LLM.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from fleetpulse.api import serving
from fleetpulse.api.repository import GoldRepository, records
from fleetpulse.gold import evidence as gold_evidence
from fleetpulse.gold.config import IdleRuleConfig
from fleetpulse.gold.scenario import idle_scenario

NEIGHBOUR_TEXT = {"driving": "driving", "engine_off": "engine off", "signal_unavailable": "signal unavailable",
                  "session_start": "start of recording", "session_end": "end of recording"}
TAG_TEXT = {"cold_engine_at_start": "cold engine at start", "raised_engine_speed": "raised engine speed",
            "over_15_min": "over 15 minutes", "at_session_start": "at start of recording"}

BASE_LIMITATIONS = [
    "The available telemetry cannot identify why the vehicle was stationary: there is no GPS/location, job, "
    "loading or dispatch context.",
    "One vehicle (Turku Renault T520), Part 3 observation window only; this is not a fleet-wide measurement.",
    "Timestamps are logger wall clock with unknown timezone.",
    "Fuel is the FMS fuel-rate indication integrated over time (reconciled with the fuel counter per session); "
    "it is not a contractual fuel measurement.",
    "PTO, cab heating and idle-increment switch states are not decoded.",
]
CONTEXT_INVESTIGATIONS = [
    "Location (GPS) at the time of the event, to see whether the vehicle was at a depot, customer site or in traffic.",
    "Job / dispatch state (loading, unloading, waiting for a slot).",
    "PTO and auxiliary equipment state, and cab heating use (ambient temperature was not reliable in this data).",
    "Driver or operations log for the period.",
]


def fmt_duration(s: float | None) -> str:
    if s is None:
        return "unknown"
    if s < 60:
        return f"{s:.1f} s"
    t = int(round(s))
    h, m, sec = t // 3600, (t % 3600) // 60, t % 60
    return f"{h}h {m:02d}m" if h else f"{m}m {sec:02d}s"


def _n(v, d=1):
    return None if v is None else round(float(v), d)


@dataclass
class Fact:
    id: str
    label: str
    value: object
    unit: str | None
    display: str
    source: str  # "gold" | "derived_from_gold" | "gold_scenario_engine"


@dataclass
class EvidencePackage:
    scope: str
    subject_id: str | None
    evidence_ids: list[str]
    facts: list[Fact]
    interpretation: list[str]
    limitations: list[str]
    investigations: list[str]
    confidence: dict
    gold_build_id: str
    rule_version: str
    context: dict = field(default_factory=dict)  # compact structured payload sent to the provider
    scenario: dict | None = None

    def fact(self, fid: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fid), None)

    def public(self) -> dict:
        d = asdict(self)
        d["facts"] = [asdict(f) for f in self.facts]
        return d


class EvidenceNotFound(LookupError):
    pass


class EvidenceRetriever:
    def __init__(self, repo: GoldRepository):
        self.repo = repo

    # --- helpers ---------------------------------------------------------------
    @property
    def _build(self) -> dict:
        return self.repo.build_info()

    @property
    def _cfg(self) -> IdleRuleConfig:
        return IdleRuleConfig(**self._build["rule_config"])

    def _rule(self) -> dict:
        c = self._cfg
        return {"version": c.rule_version,
                "definition": f"engine speed >= {c.engine_on_min_rpm:g} rpm AND wheel speed < {c.stationary_max_kph:g} km/h, "
                              f"both fresh and valid, for at least {c.min_event_s:g} s within one recording session",
                "extended_threshold_min": 5, "highlight_threshold_min": 15,
                "cold_engine_below_c": c.cold_coolant_c, "raised_engine_speed_above_rpm": c.raised_idle_rpm,
                "thresholds_are_development_defaults": True}

    def _constants(self) -> dict:
        return {"vehicles": 1, "observation_window": "Part 3", "duration_class_bounds_min": [1, 5, 15]}

    def _event_brief(self, e: dict) -> dict:
        return {"event_id": e["event_id"], "start": e["event_start"], "duration_s": _n(e["duration_seconds"]),
                "duration": fmt_duration(e["duration_seconds"]), "fuel_l": _n(e["fuel_consumed_l"], 3),
                "average_engine_rpm": _n(e["average_engine_rpm"]), "duration_class": e["duration_class"],
                "preceded_by": e["preceded_by"], "followed_by": e["followed_by"]}

    # --- event -------------------------------------------------------------------
    def event(self, event_id: str) -> EvidencePackage:
        e = self.repo.event(event_id)
        if e is None:
            raise EvidenceNotFound(f"idle event {event_id!r} not found")
        s = self.repo.session(e["recording_session_id"])
        ev_obj = self.repo.evidence.get(event_id) or gold_evidence.evidence_object(e, self._cfg)
        group = serving.attention_group(e["duration_class"])
        tags = [t for t in (e["descriptive_tags"] or [])]
        dur = e["duration_seconds"]
        facts = [
            Fact("duration_seconds", "Stationary engine-on duration", _n(dur), "s", f"{fmt_duration(dur)} ({dur:.1f} s)", "gold"),
            Fact("fuel_consumed_l", "Fuel consumed during the event", _n(e["fuel_consumed_l"], 3), "L", f"{e['fuel_consumed_l']:.3f} L", "gold"),
            Fact("average_fuel_rate_lph", "Average fuel rate", _n(e["average_fuel_rate_lph"], 2), "L/h", f"{e['average_fuel_rate_lph']:.2f} L/h", "gold"),
            Fact("average_engine_rpm", "Average engine speed", _n(e["average_engine_rpm"]), "rpm", f"{e['average_engine_rpm']:.1f} rpm", "gold"),
            Fact("average_engine_load_pct", "Average engine load", _n(e["average_engine_load_pct"]), "%", f"{e['average_engine_load_pct']:.1f} %", "gold"),
            Fact("coolant_c", "Coolant temperature start → end", [e["coolant_start_c"], e["coolant_end_c"]], "°C",
                 f"{e['coolant_start_c']:.0f} °C → {e['coolant_end_c']:.0f} °C", "gold"),
            Fact("neighbours", "Before → after", [e["preceded_by"], e["followed_by"]], None,
                 f"{NEIGHBOUR_TEXT.get(e['preceded_by'], e['preceded_by'])} before, "
                 f"{NEIGHBOUR_TEXT.get(e['followed_by'], e['followed_by'])} after", "gold"),
            Fact("time_range", "Time range (logger wall clock)", [e["event_start"], e["event_end"]], None,
                 f"{e['event_start'].replace('T', ' ')[:19]} to {e['event_end'].replace('T', ' ')[:19]}", "gold"),
            Fact("descriptive_tags", "Descriptive tags", tags, None,
                 ", ".join(TAG_TEXT.get(t, t) for t in tags) or "none", "gold"),
        ]
        c = self._cfg
        interp = [f"FleetPulse detected an engine-on stationary event: engine speed at or above {c.engine_on_min_rpm:g} rpm "
                  f"with wheel speed below {c.stationary_max_kph:g} km/h (rule {c.rule_version})."]
        if group == "extended":
            interp.append("FleetPulse classifies this as an extended engine-on stationary event (5 minutes or longer); "
                          "investigation with operational context is recommended.")
        else:
            interp.append("FleetPulse classifies this as a routine / short observation (under 5 minutes).")
        if e["duration_class"] == "over_15_min":
            interp.append("It is one of the events over 15 minutes highlighted by FleetPulse.")
        if "cold_engine_at_start" in tags:
            interp.append(f"Coolant was below {c.cold_coolant_c:g} °C at the start (cold engine tag, a measured fact, not a cause).")
        if "raised_engine_speed" in tags:
            interp.append(f"Average engine speed was above {c.raised_idle_rpm:g} rpm (raised engine speed tag, measured).")
        interp.append(serving.DETECTION_STATEMENT[e["detection_confidence"]] + " " + serving.INTERPRETATION_STATEMENT)

        session_ctx = None
        if s:
            session_ctx = {"recording_session_id": s["recording_session_id"], "session_start": s["session_start"],
                           "session_end": s["session_end"], "engine_on_s": _n(s["engine_on_duration_s"]),
                           "stationary_engine_on_s": _n(s["stationary_engine_on_duration_s"]),
                           "stationary_events": s["stationary_events"],
                           "longest_stationary_event_s": _n(s["longest_stationary_event_s"])}
        context = {
            "scope": "event",
            "event": {**self._event_brief(e), "end": e["event_end"], "measured": ev_obj["measured"],
                      "attention_group": group, "fuel_coverage_pct": _n(e["fuel_coverage_pct"])},
            "rule": self._rule(),
            "confidence": {"detection": e["detection_confidence"], "interpretation": e["interpretation_status"]},
            "session": session_ctx,
            "constants": self._constants(),
        }
        return EvidencePackage(
            scope="event", subject_id=event_id,
            evidence_ids=[f"event:{event_id}", f"evidence:{ev_obj['evidence_id']}", f"session:{e['recording_session_id']}"],
            facts=facts, interpretation=interp, limitations=BASE_LIMITATIONS,
            investigations=CONTEXT_INVESTIGATIONS,
            confidence={"detection": e["detection_confidence"], "interpretation": e["interpretation_status"]},
            gold_build_id=self._build["gold_build_id"], rule_version=self._build["rule_version"], context=context)

    # --- session ------------------------------------------------------------------
    def session(self, session_id: str) -> EvidencePackage:
        s = self.repo.session(session_id)
        if s is None:
            raise EvidenceNotFound(f"recording session {session_id!r} not found")
        ev = self.repo.events[self.repo.events["recording_session_id"] == session_id]
        ext = ev[ev["duration_class"].isin(serving.ATTENTION_GROUPS["extended"]["duration_classes"])]
        top_dur = records(ev.nlargest(5, "duration_seconds"))
        top_fuel = records(ev.nlargest(5, "fuel_consumed_l"))
        ext_fuel = float(ext["fuel_consumed_l"].sum())
        stat_fuel = s["fuel_while_stationary_l"] or 0.0
        ext_share = round(100 * ext_fuel / stat_fuel, 1) if stat_fuel > 0 else None
        facts = [
            Fact("session_range", "Recording session (logger wall clock)", [s["session_start"], s["session_end"]], None,
                 f"{s['session_start'].replace('T', ' ')[:19]} to {s['session_end'].replace('T', ' ')[:19]}", "gold"),
            Fact("engine_on_s", "Engine-on time", _n(s["engine_on_duration_s"]), "s", fmt_duration(s["engine_on_duration_s"]), "gold"),
            Fact("moving_s", "Moving time", _n(s["moving_duration_s"]), "s", fmt_duration(s["moving_duration_s"]), "gold"),
            Fact("stationary_engine_on_s", "Stationary engine-on time", _n(s["stationary_engine_on_duration_s"]), "s",
                 fmt_duration(s["stationary_engine_on_duration_s"]), "gold"),
            Fact("stationary_pct", "Stationary share of engine-on time", _n(s["stationary_pct_of_engine_on_time"]), "%",
                 f"{s['stationary_pct_of_engine_on_time']:.1f} %", "gold"),
            Fact("distance_km", "Distance", _n(s["distance_km"]), "km", f"{s['distance_km']:.1f} km", "gold"),
            Fact("fuel_l", "Fuel consumed (session)", _n(s["fuel_consumed_l"], 2), "L", f"{s['fuel_consumed_l']:.2f} L", "gold"),
            Fact("stationary_fuel_l", "Fuel consumed while stationary", _n(stat_fuel, 2), "L", f"{stat_fuel:.2f} L", "gold"),
            Fact("stationary_events", "Stationary engine-on events", int(s["stationary_events"]), None, str(int(s["stationary_events"])), "gold"),
            Fact("extended_events", "Extended events (5 minutes or longer)", int(len(ext)), None, str(int(len(ext))), "derived_from_gold"),
            Fact("longest_event_s", "Longest stationary event", _n(s["longest_stationary_event_s"]), "s",
                 fmt_duration(s["longest_stationary_event_s"]), "gold"),
            Fact("extended_fuel_l", "Fuel in extended events", round(ext_fuel, 3), "L",
                 f"{ext_fuel:.3f} L" + (f" ({ext_share:.1f} % of the session's stationary fuel)" if ext_share is not None else ""),
                 "derived_from_gold"),
        ]
        interp = [f"FleetPulse measured {fmt_duration(s['stationary_engine_on_duration_s'])} of stationary engine-on "
                  f"operation in this recording session ({s['stationary_pct_of_engine_on_time']:.1f} % of engine-on time)."]
        if len(ext):
            interp.append(f"{len(ext)} extended event(s) of 5 minutes or longer are recommended for investigation with "
                          "operational context.")
        else:
            interp.append("No extended events (5 minutes or longer) were detected in this session.")
        interp.append("A recording session is a continuous stretch of logged data; it is not a trip or a shift.")
        context = {
            "scope": "session",
            "session": {k: (_n(v, 3) if isinstance(v, float) else v) for k, v in s.items()
                        if k in ("recording_session_id", "session_start", "session_end", "engine_on_duration_s",
                                 "moving_duration_s", "stationary_engine_on_duration_s", "stationary_pct_of_engine_on_time",
                                 "distance_km", "fuel_consumed_l", "fuel_while_stationary_l", "stationary_events",
                                 "longest_stationary_event_s", "stationary_events_over_15_min", "fuel_coverage_pct")},
            "derived_from_gold": {"extended_events": int(len(ext)), "extended_events_fuel_l": round(ext_fuel, 3),
                                  "extended_share_of_stationary_fuel_pct": ext_share},
            "top_events_by_duration": [self._event_brief(e) for e in top_dur],
            "top_events_by_fuel": [self._event_brief(e) for e in top_fuel],
            "rule": self._rule(), "constants": self._constants(),
        }
        ids = [f"session:{session_id}"] + [f"event:{e['event_id']}" for e in
                                           {e["event_id"]: e for e in top_dur + top_fuel}.values()]
        return EvidencePackage(
            scope="session", subject_id=session_id, evidence_ids=ids, facts=facts, interpretation=interp,
            limitations=BASE_LIMITATIONS, investigations=CONTEXT_INVESTIGATIONS,
            confidence={"detection": "high" if (s["fuel_coverage_pct"] or 0) >= 95 else "medium",
                        "interpretation": "context_unknown"},
            gold_build_id=self._build["gold_build_id"], rule_version=self._build["rule_version"], context=context)

    # --- overall ------------------------------------------------------------------
    def overall(self) -> EvidencePackage:
        op = self.repo.operating
        ev = self.repo.events
        ses = self.repo.sessions
        eng = ses[ses["engine_on_duration_s"] >= serving.ENGINE_SESSION_MIN_ENGINE_ON_S]
        counts = ev["duration_class"].value_counts()
        dist = {c: int(counts.get(c, 0)) for c in serving.DURATION_CLASS_ORDER}
        ext = ev[ev["duration_class"].isin(serving.ATTENTION_GROUPS["extended"]["duration_classes"])]
        n_ext, n_all = int(len(ext)), int(len(ev))
        ext_time_s = float(ext["duration_seconds"].sum())
        ext_fuel = float(ext["fuel_consumed_l"].sum())
        stat_s = op["stationary_engine_on_hours"] * 3600
        ext_time_share = round(100 * ext_time_s / stat_s, 1)
        ext_fuel_share = round(100 * ext_fuel / op["stationary_fuel_consumed_l"], 1)
        ext_event_share = round(100 * n_ext / n_all, 1)
        short_share = round(100 * dist["under_1_min"] / n_all, 1)
        hl = records(ev[ev["duration_class"] == "over_15_min"].sort_values("duration_seconds", ascending=False))
        top_sessions = records(eng.nlargest(3, "stationary_engine_on_duration_s"))
        facts = [
            Fact("engine_on_hours", "Engine-on time", _n(op["engine_on_hours"], 2), "h", f"{op['engine_on_hours']:.2f} h", "gold"),
            Fact("moving_hours", "Moving time", _n(op["moving_hours"], 2), "h", f"{op['moving_hours']:.2f} h", "gold"),
            Fact("stationary_engine_on_hours", "Stationary engine-on time", _n(op["stationary_engine_on_hours"], 2), "h",
                 f"{op['stationary_engine_on_hours']:.2f} h", "gold"),
            Fact("stationary_pct", "Stationary share of engine-on time", _n(op["stationary_pct_of_engine_on_time"]), "%",
                 f"{op['stationary_pct_of_engine_on_time']:.1f} %", "gold"),
            Fact("distance_km", "Distance", _n(op["distance_km"]), "km", f"{op['distance_km']:.1f} km", "gold"),
            Fact("fuel_l", "Observed fuel", _n(op["fuel_consumed_l"]), "L", f"{op['fuel_consumed_l']:.1f} L", "gold"),
            Fact("stationary_fuel_l", "Fuel consumed while stationary", _n(op["stationary_fuel_consumed_l"], 2), "L",
                 f"{op['stationary_fuel_consumed_l']:.2f} L ({op['stationary_fuel_pct']:.2f} % of observed fuel)", "gold"),
            Fact("stationary_events", "Stationary engine-on events", n_all, None, str(n_all), "gold"),
            Fact("duration_distribution", "Events by duration", dist, None,
                 f"{dist['under_1_min']} under 1 min, {dist['1_to_5_min']} of 1–5 min, {dist['5_to_15_min']} of 5–15 min, "
                 f"{dist['over_15_min']} over 15 min", "gold"),
            Fact("extended_events", "Extended events (5 minutes or longer)", n_ext, None,
                 f"{n_ext} of {n_all} events ({ext_event_share:.1f} %)", "derived_from_gold"),
            Fact("extended_share", "Extended events' share of stationary time and fuel", [ext_time_share, ext_fuel_share], "%",
                 f"{ext_time_share:.1f} % of stationary engine-on time and {ext_fuel_share:.1f} % of stationary fuel",
                 "derived_from_gold"),
            Fact("engine_sessions", "Engine-operation sessions", int(len(eng)), None,
                 f"{len(eng)} sessions with at least 0.1 h engine-on", "gold"),
            Fact("sessions_over_15_min", "Sessions with more than 15 min accumulated stationary engine-on time",
                 int(op["sessions_with_over_15_min_stationary"]), None,
                 f"{op['sessions_with_over_15_min_stationary']} of {len(eng)}", "gold"),
        ]
        interp = [
            f"Most stationary engine-on events are short: {dist['under_1_min']} of {n_all} ({short_share:.1f} %) lasted under "
            "1 minute, typically between driving periods.",
            f"The strongest measured pattern is the share of engine-on time spent stationary: "
            f"{op['stationary_pct_of_engine_on_time']:.1f} % of engine-on time, using {op['stationary_fuel_pct']:.2f} % of "
            "observed fuel.",
            f"{n_ext} extended events (5 minutes or longer) account for {ext_time_share:.1f} % of stationary engine-on time; "
            f"the {dist['over_15_min']} events over 15 minutes are the first candidates for investigation.",
            "FleetPulse measures the state; it does not judge it. The reason for each stop is not in the telemetry.",
        ]
        context = {
            "scope": "overall",
            "operating_summary": {k: (_n(v, 3) if isinstance(v, float) else v) for k, v in op.items()
                                  if k not in ("gold_build_id",)},
            "sessions": {"engine_operation_sessions": int(len(eng)), "recording_sessions_total": op["recording_sessions"],
                         "sessions_with_any_engine_on": op["sessions_with_engine_on"],
                         "sessions_with_over_15_min_stationary": op["sessions_with_over_15_min_stationary"]},
            "duration_distribution": dist,
            "attention_groups": {"routine_short": n_all - n_ext, "extended": n_ext},
            "derived_from_gold": {"extended_share_of_events_pct": ext_event_share,
                                  "extended_share_of_stationary_time_pct": ext_time_share,
                                  "extended_share_of_stationary_fuel_pct": ext_fuel_share,
                                  "extended_events_fuel_l": round(ext_fuel, 3),
                                  "under_1_min_share_of_events_pct": short_share},
            "events_over_15_min": [self._event_brief(e) for e in hl],
            "top_sessions_by_stationary_time": [
                {"recording_session_id": s["recording_session_id"], "session_start": s["session_start"],
                 "stationary_engine_on_s": _n(s["stationary_engine_on_duration_s"]),
                 "stationary_duration": fmt_duration(s["stationary_engine_on_duration_s"]),
                 "stationary_pct": _n(s["stationary_pct_of_engine_on_time"]), "stationary_events": s["stationary_events"]}
                for s in top_sessions],
            "rule": self._rule(), "constants": self._constants(),
        }
        ids = ([f"operating_summary:{self._build['gold_build_id']}"] + [f"event:{e['event_id']}" for e in hl]
               + [f"session:{s['recording_session_id']}" for s in top_sessions])
        return EvidencePackage(
            scope="overall", subject_id=None, evidence_ids=ids, facts=facts, interpretation=interp,
            limitations=BASE_LIMITATIONS, investigations=[
                f"Start with the {dist['over_15_min']} events over 15 minutes, then the other extended events, "
                "with location and job context.", *CONTEXT_INVESTIGATIONS[1:]],
            confidence={"detection": "high", "interpretation": "context_unknown"},
            gold_build_id=self._build["gold_build_id"], rule_version=self._build["rule_version"], context=context)

    # --- scenario -----------------------------------------------------------------
    def scenario(self, avoidable_pct: float, fuel_price_per_l: float, currency: str = "EUR",
                 event_ids: list[str] | None = None) -> EvidencePackage:
        """Runs the Gold scenario engine FIRST; the provider only receives its result."""
        if event_ids:
            ev = self.repo.events[self.repo.events["event_id"].isin(event_ids)]
            missing = sorted(set(event_ids) - set(ev["event_id"]))
            if missing:
                raise EvidenceNotFound(f"unknown event_ids {missing}")
            observed = float(ev["fuel_consumed_l"].sum())
            basis = f"observed fuel of {len(event_ids)} selected Gold idle events"
            ids = [f"event:{i}" for i in event_ids]
        else:
            observed = self.repo.operating["stationary_fuel_consumed_l"]
            basis = "all stationary engine-on fuel in the Part 3 observation window (Gold operating summary)"
            ids = [f"operating_summary:{self._build['gold_build_id']}"]
        result = idle_scenario(observed, avoidable_pct, fuel_price_per_l, currency)  # ValueError on invalid input
        facts = [
            Fact("observed_stationary_fuel_l", "Observed stationary fuel (basis)", round(observed, 3), "L", f"{observed:.2f} L", "gold"),
            Fact("assumed_pct", "Operator-assumed share", avoidable_pct, "%", f"{avoidable_pct:g} %", "gold_scenario_engine"),
            Fact("fuel_price", "Operator fuel price", fuel_price_per_l, f"{currency}/L", f"{fuel_price_per_l:g} {currency}/L",
                 "gold_scenario_engine"),
            Fact("scenario_fuel_l", "Scenario fuel (estimate)", result["scenario_avoidable_fuel_l"], "L",
                 f"{result['scenario_avoidable_fuel_l']:.2f} L", "gold_scenario_engine"),
            Fact("scenario_cost", "Scenario cost exposure (estimate)", result["scenario_cost_exposure"], currency,
                 f"{result['scenario_cost_exposure']:.2f} {currency}", "gold_scenario_engine"),
        ]
        context = {"scope": "scenario", "basis": basis, "scenario_result": result, "constants": self._constants()}
        return EvidencePackage(
            scope="scenario", subject_id=None, evidence_ids=ids + ["scenario_engine:fleetpulse.gold.scenario.idle_scenario"],
            facts=facts, interpretation=[
                f"{result['label']}: the share and the price are operator assumptions; only the basis fuel is observed.",
                "The scenario does not show that any stationary running could have been avoided."],
            limitations=["The telemetry cannot tell which stationary engine-on time, if any, was avoidable.",
                         *BASE_LIMITATIONS[1:]],
            investigations=["Validate the assumed share against operational context (job, location, PTO use) "
                            "before using the estimate in a decision."],
            confidence={"detection": "high", "interpretation": "scenario_assumption"},
            gold_build_id=self._build["gold_build_id"], rule_version=self._build["rule_version"],
            context=context, scenario=result)
