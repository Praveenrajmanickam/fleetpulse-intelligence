"""Signal serving for Dashboard V2: catalog, data/signal health and traceability.

Sources of truth (nothing here defines a signal):
* decode metadata - ``fleetpulse.j1939.definitions`` (MESSAGES / SIGNALS, verified against FMS-Standard v02.00)
* message names   - ``fleetpulse.j1939.pgn_catalog.DOCUMENTED_PGNS``
* caveats         - documented findings, each with its document reference
* health          - status / freshness columns of Silver telemetry (counted, not judged)
* trace           - a real Bronze frame decoded by the production decoder, linked by lineage to
                    Silver signal events, Silver telemetry and the Gold event

Signal health describes DATA availability and quality in the recording, not the mechanical
condition of the vehicle.
"""

from __future__ import annotations

import math
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd
import pyarrow.compute as pc
import pyarrow.dataset as ds

from fleetpulse.api.repository import GoldRepository, clean
from fleetpulse.j1939.definitions import FMS_V02, MESSAGES
from fleetpulse.j1939.identifier import parse_can_id
from fleetpulse.j1939.pgn_catalog import DOCUMENTED_PGNS
from fleetpulse.j1939.signals import SignalKind, SignalSpec, decode_signal

HEALTH_STATEMENT = ("Signal health describes the availability and quality of recorded data. "
                    "It is not a statement about the mechanical condition of the vehicle.")

DISPLAY = {
    "engine_speed_rpm": ("Engine speed", "engine"),
    "engine_load_pct": ("Engine load at current speed", "engine"),
    "accelerator_pedal_pct": ("Accelerator pedal position", "driver"),
    "wheel_speed_kph": ("Wheel-based vehicle speed", "motion"),
    "cruise_control_active": ("Cruise control active", "driver"),
    "brake_switch": ("Brake switch", "driver"),
    "clutch_switch": ("Clutch switch", "driver"),
    "fuel_rate_lph": ("Fuel rate", "fuel"),
    "total_fuel_used_l": ("Total fuel used (high resolution)", "fuel"),
    "total_distance_m": ("Total vehicle distance (high resolution)", "motion"),
    "coolant_temp_c": ("Engine coolant temperature", "cooling"),
    "ambient_temp_c": ("Ambient air temperature", "environment"),
}
SYSTEM_LABELS = {"engine": "Engine / Powertrain", "fuel": "Fuel System", "cooling": "Cooling",
                 "driver": "Driver Controls", "motion": "Vehicle Motion / Wheels", "environment": "Environment"}
UNIT_DISPLAY = {"degC": "°C", "state": "state"}

# Documented caveats (docs/signal_definitions.md "Signal meaning notes"; docs/business_discovery_v1.md §3).
CAVEATS: dict[str, list[dict]] = {
    "ambient_temp_c": [{
        "level": "quality_warning",
        "text": "Not trustworthy as outside air temperature: it read +19.9 °C on a −17 °C morning (session C), "
                "consistent with sensor heat soak. FleetPulse uses it only as context.",
        "source": "docs/business_discovery_v1.md §3"}],
    "clutch_switch": [{
        "level": "unavailable",
        "text": "FMS-Standard: trucks with an automatic gearbox send this as not available. This truck sends raw 11 "
                "(not available) in 100 % of frames.",
        "source": "docs/signal_definitions.md"}],
    "engine_load_pct": [{"level": "note", "text": "Load relative to the maximum torque available at the current engine "
                         "speed, clipped to 0 during engine braking. Not absolute torque or power.",
                         "source": "docs/signal_definitions.md"}],
    "wheel_speed_kph": [{"level": "note", "text": "Calculated from wheel or tailshaft speed; FMS notes it may vary from "
                         "tachograph speed.", "source": "docs/signal_definitions.md"}],
    "brake_switch": [{"level": "note", "text": "Service-brake foot pedal only; not the park brake, retarder or engine brake.",
                      "source": "docs/signal_definitions.md"}],
    "cruise_control_active": [{"level": "note", "text": "Cruise control switched on; this does not guarantee the engine "
                               "is under cruise control.", "source": "docs/signal_definitions.md"}],
    "fuel_rate_lph": [{"level": "note", "text": "FMS: fuel values are indications, not contractual values. Integrated "
                       "fuel reconciles with the SPN 5054 counter per session.", "source": "docs/signal_definitions.md"}],
    "total_fuel_used_l": [{"level": "note", "text": "0.001 L resolution, but advances in ~0.01 L steps in this data. "
                           "FMS: an indication, not a contractual value.", "source": "docs/signal_definitions.md"}],
}

# How each signal is used by the Gold Idle Intelligence rule (docs/gold_idle_intelligence.md).
GOLD_USE = {
    "engine_speed_rpm": ("state_rule", "Engine running: engine speed ≥ {engine_on_min_rpm:g} rpm (fresh and valid)."),
    "wheel_speed_kph": ("state_rule", "Stationary: wheel speed < {stationary_max_kph:g} km/h (fresh and valid)."),
    "fuel_rate_lph": ("measurement", "Event fuel: fuel rate integrated over the event on the 100 ms grid."),
    "coolant_temp_c": ("context_tag", "Coolant at start/end; 'cold engine at start' tag below {cold_coolant_c:g} °C."),
    "engine_load_pct": ("measurement", "Average engine load reported per event."),
    "total_fuel_used_l": ("reconciliation", "Fuel counter used to reconcile integrated fuel per session."),
    "total_distance_m": ("reconciliation", "Distance counter used to reconcile integrated distance per session."),
}

_SPECS: dict[str, tuple[SignalSpec, object]] = {s.name: (s, m) for m in MESSAGES for s in m.signals}


def _fmt(template: str, cfg: dict) -> str:
    try:
        return template.format(**cfg)
    except (KeyError, IndexError, ValueError):
        return template.split(":")[0]  # never show a raw placeholder


def _unit(spec: SignalSpec) -> str:
    return UNIT_DISPLAY.get(spec.unit, spec.unit)


def _decode_meta(spec: SignalSpec) -> dict:
    d = {"position": spec.position, "start_byte": spec.start_byte, "length_bits": spec.length_bits,
         "byte_order": "little-endian (first byte least significant)" if spec.length_bits > 8 else "single byte",
         "kind": spec.kind.value}
    if spec.kind is SignalKind.DISCRETE:
        d |= {"start_bit": spec.start_bit, "states": {str(k): v for k, v in spec.states.items()},
              "not_available_code": 3, "error_code": 2}
    else:
        d |= {"resolution": spec.resolution, "offset": spec.offset, "unit": _unit(spec),
              "operating_range": [spec.min_value, round(spec.max_value, 3)],
              "formula": f"physical = raw × {spec.resolution:g}" + (f" + ({spec.offset:g})" if spec.offset else "")}
    return d


def catalog(rule_config: dict | None = None) -> list[dict]:
    """Signal catalog. `rule_config` (the Gold build's rule) fills the thresholds quoted in gold_use."""
    cfg = rule_config or {}
    out = []
    for m in MESSAGES:
        acronym, name, _ = DOCUMENTED_PGNS.get(m.pgn, (m.acronym, m.acronym, ""))
        for s in m.signals:
            label, system = DISPLAY[s.name]
            use = GOLD_USE.get(s.name)
            out.append({
                "name": s.name, "label": label, "system": system, "system_label": SYSTEM_LABELS[system],
                "pgn": m.pgn, "pgn_hex": f"0x{m.pgn:04X}", "message": m.acronym, "message_name": name,
                "spn": s.spn, "unit": _unit(s), "description": s.description,
                "fms_period_ms": m.nominal_period_ms, "freshness_limit_ms": 3 * m.nominal_period_ms,
                "freshness_column": f"{m.key}_freshness", "status_column": f"{s.name}_status",
                "decode": _decode_meta(s), "reference": s.reference, "standard": FMS_V02,
                "caveats": CAVEATS.get(s.name, []),
                "gold_use": {"kind": use[0], "text": _fmt(use[1], cfg)} if use else {"kind": "not_used", "text": "Not used by Gold idle-v1."},
            })
    return out


# --- health -------------------------------------------------------------------------------

def _counts(col: pd.Series) -> dict:
    vc = col.fillna("none").value_counts()
    return {str(k): int(v) for k, v in vc.items()}


@lru_cache(maxsize=4)
def _health_cached(silver_root: str, engine_min_rpm: float) -> dict:
    d = ds.dataset(Path(silver_root) / "silver_telemetry", format="parquet", partitioning="hive")
    names = [s.name for s in (x for m in MESSAGES for x in m.signals)]
    cols = (["grid_timestamp", "recording_session_id", "engine_speed_rpm"] + [f"{n}_status" for n in names]
            + [f"{m.key}_freshness" for m in MESSAGES])
    t = d.to_table(columns=cols).to_pandas()
    running = (t["engine_speed_rpm_status"] == "valid") & (t["engine_speed_rpm"] >= engine_min_rpm)
    scopes = {"all_rows": t, "engine_running_rows": t[running]}
    signals = []
    for m in MESSAGES:
        for s in m.signals:
            entry = {"name": s.name, "label": DISPLAY[s.name][0], "system": DISPLAY[s.name][1], "message": m.acronym,
                     "pgn": m.pgn, "spn": s.spn, "scopes": {}}
            for scope, df in scopes.items():
                n = len(df)
                status = _counts(df[f"{s.name}_status"])
                fresh = _counts(df[f"{m.key}_freshness"])
                valid_fresh = int(((df[f"{s.name}_status"] == "valid") & (df[f"{m.key}_freshness"] == "fresh")).sum())
                bad = int(df[f"{s.name}_status"].isin(["not_available", "error", "reserved", "missing_bytes", "out_of_range"]).sum())
                entry["scopes"][scope] = {
                    "rows": n, "status_counts": status, "freshness_counts": fresh,
                    "available_rows": valid_fresh, "stale_rows": fresh.get("stale", 0),
                    "no_observation_rows": fresh.get("no_observation", 0), "unavailable_rows": bad,
                    "available_pct": round(100 * valid_fresh / n, 2) if n else None,
                }
            caveat_levels = {c["level"] for c in CAVEATS.get(s.name, [])}
            pct = entry["scopes"]["engine_running_rows"]["available_pct"] or 0.0
            if "quality_warning" in caveat_levels:
                state = "quality_warning"
            elif pct == 0:
                state = "unavailable"
            elif pct < 95:
                state = "partial"
            else:
                state = "available"
            entry["state"] = state
            entry["caveats"] = CAVEATS.get(s.name, [])
            signals.append(entry)
    return {"rows_analysed": int(len(t)), "engine_running_rows": int(running.sum()),
            "recording_sessions": int(t["recording_session_id"].nunique()),
            "grid_start": clean(t["grid_timestamp"].min()), "grid_end": clean(t["grid_timestamp"].max()),
            "signals": signals}


STATE_RULES = {
    "available": "valid and fresh in ≥ 95 % of engine-running rows",
    "partial": "valid and fresh in some, but < 95 %, of engine-running rows",
    "unavailable": "never valid and fresh in engine-running rows",
    "quality_warning": "values are present but a documented data-quality concern applies",
}


def health(repo: GoldRepository) -> dict:
    if repo.silver_root is None:
        raise FileNotFoundError("Silver telemetry is not configured")
    cfg = repo.build_info()["rule_config"]
    h = _health_cached(str(repo.silver_root), float(cfg["engine_on_min_rpm"]))
    op = repo.operating
    return {
        "statement": HEALTH_STATEMENT,
        "scope": {"vehicle_id": repo.events["vehicle_id"].iloc[0] if len(repo.events) else None, "vehicles": 1,
                  "dataset": "Part 3", "silver_build_ids": repo.build_info()["silver_build_ids"],
                  "recording_sessions": h["recording_sessions"], "grid": "Silver telemetry, 100 ms",
                  "window_start": h["grid_start"], "window_end": h["grid_end"],
                  "timestamp_timezone": op["timestamp_timezone"],
                  "historical": True, "note": "Historical recording. Not current or live vehicle state."},
        "rows_analysed": h["rows_analysed"],
        "engine_running_rows": h["engine_running_rows"],
        "engine_running_definition": f"engine_speed_rpm valid and ≥ {cfg['engine_on_min_rpm']:g} rpm",
        "freshness_rule": "a value is fresh while its latest frame is at most 3 × the FMS repetition period old",
        "state_rules": STATE_RULES,
        "signals": h["signals"],
    }


# --- traceability ---------------------------------------------------------------------------

class TraceNotFound(LookupError):
    pass


def trace(repo: GoldRepository, name: str, event_id: str | None) -> dict:
    """Follow one real value from the Bronze frame to Gold, anchored on the Silver telemetry row at the event midpoint."""
    if name not in _SPECS:
        raise TraceNotFound(f"signal {name!r} is not decoded by FleetPulse")
    if repo.silver_root is None:
        raise FileNotFoundError("Silver telemetry is not configured")
    bronze_root = repo.silver_root.parent / "bronze" / "bronze_can_frames"
    if not bronze_root.exists():
        raise FileNotFoundError("Bronze frames are not available locally")
    spec, msg = _SPECS[name]
    if event_id is None:
        event_id = str(repo.events.sort_values("duration_seconds", ascending=False)["event_id"].iloc[0])
        default_event = True
    else:
        default_event = False
    e = repo.event(event_id)
    if e is None:
        raise TraceNotFound(f"idle event {event_id!r} not found")

    start, end = pd.Timestamp(e["event_start"]), pd.Timestamp(e["event_end"])
    mid = (start + (end - start) / 2).floor("100ms")
    tel = ds.dataset(repo.silver_root / "silver_telemetry", format="parquet", partitioning="hive")
    trow = tel.to_table(filter=(pc.field("recording_session_id") == e["recording_session_id"])
                        & (pc.field("grid_timestamp") == mid.to_datetime64())).to_pandas()
    if trow.empty:
        raise TraceNotFound("no Silver telemetry row at the event midpoint")
    t = trow.iloc[0]
    age = t[f"{msg.key}_age_ms"]
    if age is None or (isinstance(age, float) and math.isnan(age)):
        raise TraceNotFound(f"{msg.acronym} was not observed at the event midpoint")
    frame_ts = mid - timedelta(milliseconds=float(age))

    # Bronze: the frame(s) of this PGN at the as-of timestamp
    bronze = ds.dataset(bronze_root, format="parquet", partitioning="hive")
    day = frame_ts.strftime("%Y-%m-%d")
    lo, hi = frame_ts - timedelta(milliseconds=2), frame_ts + timedelta(milliseconds=2)
    frames = bronze.to_table(filter=(pc.field("event_date") == day) & (pc.field("event_timestamp") >= lo.to_pydatetime())
                             & (pc.field("event_timestamp") <= hi.to_pydatetime())).to_pandas()
    frames = frames[[parse_can_id(int(c)).pgn == msg.pgn for c in frames["can_id"]]]
    if frames.empty:
        raise TraceNotFound("source frame not found in Bronze")
    frames = frames.iloc[(frames["event_timestamp"] - frame_ts).abs().argsort()]
    f = frames.iloc[0]
    ident = parse_can_id(int(f["can_id"]))
    data = [None if pd.isna(f[f"byte_{i}"]) else int(f[f"byte_{i}"]) for i in range(1, 9)]

    # production decoder on the real frame
    bframe = pd.DataFrame([{f"b{i}": (pd.NA if v is None else v) for i, v in enumerate(data)}]).astype("Int64")
    dec = decode_signal(bframe, spec).iloc[0]
    raw = None if pd.isna(dec[f"{name}_raw"]) else int(dec[f"{name}_raw"])
    value = dec[name]
    value = None if value is None or (isinstance(value, float) and math.isnan(value)) or pd.isna(value) else (
        int(value) if spec.kind is SignalKind.DISCRETE else float(value))
    status = str(dec[f"{name}_status"])
    idx = [c for c in spec.byte_columns]
    used_bytes = [{"byte": int(c[1:]) + 1, "value": data[int(c[1:])], "hex": None if data[int(c[1:])] is None else f"0x{data[int(c[1:])]:02X}"}
                  for c in idx]

    # Silver signal event for the same source line (lineage)
    ev = ds.dataset(repo.silver_root / "silver_signal_events", format="parquet", partitioning="hive")
    se = ev.to_table(filter=(pc.field("event_date") == day) & (pc.field("source_file") == f["source_file"])
                     & (pc.field("source_line") == int(f["source_line"])) & (pc.field("spn") == spec.spn)).to_pandas()
    sig_event = None
    if not se.empty:
        r = se.iloc[0]
        sig_event = {k: clean(r[k]) for k in ("event_timestamp", "message", "pgn", "spn", "signal_name", "signal_raw",
                                               "signal_value", "signal_unit", "signal_status", "source_file",
                                               "source_line", "ingestion_run_id", "silver_build_id")}

    tel_value = clean(t[name])
    cfg = repo.build_info()["rule_config"]
    use = GOLD_USE.get(name)
    label, system = DISPLAY[name]
    return {
        "signal": name, "label": label, "system": system,
        "anchor": {"event_id": event_id, "default_reference_event": default_event,
                   "rule": "Silver telemetry row at the event midpoint; its as-of source frame is traced back to Bronze"},
        "steps": {
            "raw_can": {"timestamp": clean(f["event_timestamp"]), "can_id": int(f["can_id"]), "can_id_hex": f"0x{int(f['can_id']):08X}",
                        "dlc": int(f["dlc"]), "data": data, "data_hex": " ".join("--" if b is None else f"{b:02X}" for b in data),
                        "source_archive": f["source_archive"], "source_file": f["source_file"],
                        "source_line": int(f["source_line"]), "ingestion_run_id": f["ingestion_run_id"]},
            "j1939": {"priority": ident.priority, "pgn": ident.pgn, "pgn_hex": f"0x{ident.pgn:04X}",
                      "pdu_format": ident.pdu_format, "pdu_specific": ident.pdu_specific, "is_pdu2": ident.is_pdu2,
                      "source_address": ident.source_address, "message": msg.acronym,
                      "message_name": DOCUMENTED_PGNS.get(msg.pgn, ("", msg.acronym, ""))[1]},
            "spn": {"spn": spec.spn, "label": label, "reference": spec.reference, "description": spec.description},
            "decode": {**_decode_meta(spec), "bytes": used_bytes, "raw": raw},
            "physical": {"value": value, "unit": _unit(spec), "status": status,
                         "state_label": spec.states.get(value) if spec.kind is SignalKind.DISCRETE and value is not None else None},
            "silver_signal_event": sig_event,
            "silver_telemetry": {"grid_timestamp": clean(t["grid_timestamp"]), "column": name, "value": tel_value,
                                 "status": clean(t[f"{name}_status"]), "age_ms": clean(age),
                                 "freshness": clean(t[f"{msg.key}_freshness"]), "freshness_limit_ms": 3 * msg.nominal_period_ms,
                                 "recording_session_id": e["recording_session_id"], "silver_build_id": clean(t["silver_build_id"])},
            "gold_event": {"event_id": event_id, "event_start": e["event_start"], "event_end": e["event_end"],
                           "duration_seconds": e["duration_seconds"], "fuel_consumed_l": e["fuel_consumed_l"],
                           "average_engine_rpm": e["average_engine_rpm"], "detection_confidence": e["detection_confidence"],
                           "interpretation_status": e["interpretation_status"], "rule_version": e["rule_version"],
                           "gold_build_id": e["gold_build_id"],
                           "signal_role": {"kind": use[0], "text": use[1].format(**cfg)} if use
                           else {"kind": "not_used", "text": "Not used by Gold idle-v1 (context only)."}},
            "intelligence": {"text": "Extended engine-on stationary events are candidates for operational investigation. "
                                     "The telemetry does not identify why the vehicle was stationary.",
                             "attention_group": "extended" if e["duration_class"] in ("5_to_15_min", "over_15_min") else "routine_short"},
        },
        "checks": {
            "decoder_matches_silver_event": None if sig_event is None else (
                sig_event["signal_raw"] == raw and sig_event["signal_status"] == status),
            "decoder_matches_telemetry": (tel_value is None and value is None) or (
                tel_value is not None and value is not None and abs(float(tel_value) - float(value)) < 1e-9),
        },
    }
