"""Deterministic answer composer (template text from an EvidencePackage).

Used by MockAIProvider (development) and as the deterministic safe fallback when a
provider fails or an answer fails validation. Every number comes from fact display
strings, so the composer output always validates."""

from __future__ import annotations

from fleetpulse.ai.evidence import EvidencePackage

BOUNDARY = ("I can only answer from FleetPulse evidence and cannot change values, ignore the evidence or reveal "
            "configuration.")


def _d(p: EvidencePackage, fid: str) -> str:
    f = p.fact(fid)
    return f.display if f else "not available"


def compose_answer(p: EvidencePackage, intent: str, flags: tuple[str, ...] | list[str] = ()) -> str:
    body = _COMPOSERS.get(p.scope, _summary)(p, intent)
    return f"{BOUNDARY} {body}" if flags else body


def _event(p: EvidencePackage, intent: str) -> str:
    measured = (f"Measured: the vehicle was stationary with the engine running for {_d(p, 'duration_seconds')}, "
                f"from {_d(p, 'time_range')}; {_d(p, 'fuel_consumed_l')} of fuel was consumed during this state.")
    interp = p.interpretation[1] if len(p.interpretation) > 1 else p.interpretation[0]
    limit = "Limitation: the available telemetry cannot identify why the vehicle was stationary."
    nxt = ("Investigation: operational context such as location, job state or PTO state would help explain it.")
    if intent == "fuel":
        return (f"Measured: {_d(p, 'fuel_consumed_l')} of fuel was consumed during the {_d(p, 'duration_seconds')} "
                f"stationary engine-on period, at an average of {_d(p, 'average_fuel_rate_lph')} "
                f"and {_d(p, 'average_engine_rpm')}. This is observed consumption from the FMS fuel-rate signal. {limit}")
    if intent == "why_flagged":
        return (f"Interpretation: {p.interpretation[0]} {interp} "
                f"Measured: {_d(p, 'duration_seconds')}, {_d(p, 'average_engine_rpm')}, coolant {_d(p, 'coolant_c')}. "
                "Being flagged means the event merits review with context; it is not a judgement about the event.")
    if intent == "prohibited_waste":
        return (f"The evidence cannot support calling this fuel waste or avoidable. {measured} "
                f"{limit} Whether the running was needed (for example loading, PTO work, cab heating or waiting in a queue) "
                f"is not recorded. {nxt}")
    if intent == "prohibited_driver":
        return (f"The telemetry does not identify who or what kept the vehicle stationary, so it cannot attribute "
                f"responsibility to the driver. {measured} {limit} {nxt}")
    if intent == "prohibited_cause":
        return f"{limit} {measured} {nxt}"
    if intent == "additional_data":
        return ("Data that would help explain this event: " + " ".join(f"({i + 1}) {x}" for i, x in enumerate(p.investigations))
                + f" {measured}")
    extra = f" Tags: {_d(p, 'descriptive_tags')}." if _d(p, "descriptive_tags") != "none" else ""
    return (f"{measured} Average engine speed was {_d(p, 'average_engine_rpm')}, coolant {_d(p, 'coolant_c')}; "
            f"{_d(p, 'neighbours')}.{extra} Interpretation: {interp} {limit} {nxt}")


def _session(p: EvidencePackage, intent: str) -> str:
    ctx = p.context
    top = ctx.get("top_events_by_duration", [])[:3]
    top_txt = "; ".join(f"{e['start'].replace('T', ' ')[:19]} ({e['duration']}, {e['fuel_l']:.3f} L)" for e in top)
    measured = (f"Measured: {_d(p, 'engine_on_s')} engine-on, of which {_d(p, 'moving_s')} moving and "
                f"{_d(p, 'stationary_engine_on_s')} stationary with the engine running ({_d(p, 'stationary_pct')} of "
                f"engine-on time); {_d(p, 'distance_km')} driven and {_d(p, 'fuel_l')} of fuel consumed, "
                f"{_d(p, 'stationary_fuel_l')} of it while stationary.")
    limit = "Limitation: the telemetry cannot say why the vehicle was stationary."
    if intent == "stationary_amount":
        return (f"Measured: {_d(p, 'stationary_engine_on_s')} of stationary engine-on operation "
                f"({_d(p, 'stationary_pct')} of engine-on time) across {_d(p, 'stationary_events')} events; "
                f"the longest lasted {_d(p, 'longest_event_s')}; {_d(p, 'stationary_fuel_l')} of fuel was consumed while "
                f"stationary. {limit}")
    if intent == "investigate":
        if _d(p, "extended_events") == "0":
            return (f"No extended events (5 minutes or longer) were detected in this session; the longest lasted "
                    f"{_d(p, 'longest_event_s')}. {limit}")
        return (f"Investigation recommended: the {_d(p, 'extended_events')} extended event(s) of 5 minutes or longer, "
                f"starting with the longest: {top_txt}. Review them with location, job and PTO context. {limit}")
    if intent == "concentration":
        fuel_top = ctx.get("top_events_by_fuel", [])[:3]
        ft = "; ".join(f"{e['start'].replace('T', ' ')[:19]} ({e['fuel_l']:.3f} L, {e['duration']})" for e in fuel_top)
        return (f"Measured: {_d(p, 'stationary_fuel_l')} of fuel was consumed while stationary. Extended events account "
                f"for {_d(p, 'extended_fuel_l')}. The events with the most fuel were: {ft}. {limit}")
    if intent.startswith("prohibited"):
        return (f"The evidence cannot support conclusions about fuel waste or driver responsibility. {measured} {limit}")
    return (f"{measured} FleetPulse detected {_d(p, 'stationary_events')} stationary engine-on events, "
            f"{_d(p, 'extended_events')} of them extended (5 minutes or longer); the longest lasted "
            f"{_d(p, 'longest_event_s')}. {limit}")


def _overall(p: EvidencePackage, intent: str) -> str:
    ctx = p.context
    measured = (f"Measured for one vehicle in the Part 3 observation window: {_d(p, 'engine_on_hours')} engine-on, "
                f"{_d(p, 'moving_hours')} moving and {_d(p, 'stationary_engine_on_hours')} stationary with the engine "
                f"running ({_d(p, 'stationary_pct')} of engine-on time); {_d(p, 'distance_km')} driven and "
                f"{_d(p, 'fuel_l')} of fuel observed.")
    limit = "Limitation: the telemetry cannot identify why the vehicle was stationary; this is not a fleet-wide measurement."
    hl = ctx.get("events_over_15_min", [])
    hl_txt = "; ".join(f"{e['start'].replace('T', ' ')[:19]} ({e['duration']}, {e['fuel_l']:.3f} L)" for e in hl)
    if intent == "strongest_finding":
        return (f"Interpretation: {p.interpretation[1]} Measured: {_d(p, 'stationary_engine_on_hours')} of stationary "
                f"engine-on operation; {_d(p, 'stationary_fuel_l')}. {p.interpretation[2]} {limit}")
    if intent == "stationary_fuel":
        return (f"Measured: {_d(p, 'stationary_fuel_l')} was consumed while the vehicle was stationary with the engine "
                f"running, out of {_d(p, 'fuel_l')} observed. Extended events account for {_d(p, 'extended_share')}. "
                f"This is observed consumption; the evidence cannot show whether any of it was avoidable. {limit}")
    if intent == "extended_frequency":
        return (f"Measured: {_d(p, 'duration_distribution')}. Extended events (5 minutes or longer): "
                f"{_d(p, 'extended_events')}; together they account for {_d(p, 'extended_share')}. "
                f"Interpretation: {p.interpretation[0]} {limit}")
    if intent == "investigate_first":
        return (f"Investigation recommended: start with the events over 15 minutes: {hl_txt}. Then review the other "
                f"extended events ({p.fact('extended_events').value} in total) with location, job and PTO context. "
                f"{_d(p, 'sessions_over_15_min')} engine-operation sessions accumulated more than 15 minutes of "
                f"stationary engine-on time. {limit}")
    if intent == "prohibited_fleet":
        return (f"The evidence covers one vehicle only, so it cannot describe fleet-wide behaviour or compare vehicles. "
                f"{measured}")
    if intent.startswith("prohibited"):
        return (f"The evidence cannot support conclusions about fuel waste or driver responsibility. {measured} "
                f"{_d(p, 'stationary_fuel_l')} was consumed while stationary (observed). {limit}")
    return (f"{measured} FleetPulse detected {_d(p, 'stationary_events')} stationary engine-on events "
            f"({_d(p, 'duration_distribution')}); {_d(p, 'stationary_fuel_l')} of fuel was consumed while stationary. "
            f"Interpretation: {p.interpretation[0]} {limit}")


def _scenario(p: EvidencePackage, intent: str) -> str:
    return (f"Scenario estimate, not observed savings: FleetPulse applied your assumed share of {_d(p, 'assumed_pct')} "
            f"to the observed stationary fuel of {_d(p, 'observed_stationary_fuel_l')}, giving a scenario fuel of "
            f"{_d(p, 'scenario_fuel_l')} and, at {_d(p, 'fuel_price')}, a scenario cost exposure of {_d(p, 'scenario_cost')}. "
            "Only the basis fuel is measured; the share and price are assumptions. The telemetry cannot tell which "
            "stationary running, if any, was avoidable.")


def _summary(p: EvidencePackage, intent: str) -> str:
    return " ".join(f"{f.label}: {f.display}." for f in p.facts)


_COMPOSERS = {"event": _event, "session": _session, "overall": _overall, "scenario": _scenario}
