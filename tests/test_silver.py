"""Silver layer (PySpark): J1939 decode, signal events, recording sessions, telemetry, parity."""

import io
import json
import tarfile
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pyspark")

from fleetpulse.bronze.schema import BRONZE_CAN_FRAMES  # noqa: E402
from fleetpulse.j1939.definitions import MESSAGES, SIGNALS  # noqa: E402
from fleetpulse.j1939.identifier import parse_can_id  # noqa: E402
from fleetpulse.j1939.signals import decode_signal  # noqa: E402
from fleetpulse.silver import alignment, decode, sessions  # noqa: E402
from fleetpulse.silver.schema import RECORDING_SESSIONS, TELEMETRY  # noqa: E402
from fleetpulse.telemetry.alignment import build_telemetry  # noqa: E402
from fleetpulse.validation.continuity import ContinuityError  # noqa: E402

T0 = datetime(2021, 1, 19, 10, 0, 0)
VID = "test-vehicle"
BID = "test-build"
MSG = {m.acronym: m for m in MESSAGES}
# (can_id, real payload) per message, from the Turku data
FRAME = {
    "EEC1": (0x0CF004E6, [240, 125, 131, 194, 18, 255, 255, 255]),
    "EEC2": (0x0CF003E6, [241, 0, 3, 255, 255, 255, 255, 255]),
    "CCVS": (0x18FEF1E6, [243, 76, 47, 196, 0, 255, 224, 255]),
    "LFE": (0x18FEF2E6, [12, 0, 255, 255, 255, 255, 255, 255]),
    "HRLFC": (0x18FD09E6, [255, 255, 255, 255, 214, 202, 39, 0]),
    "VDHR": (0x18FEC1E6, [176, 181, 26, 0, 178, 181, 26, 0]),
    "ET1": (0x18FEEEE6, [120, 255, 98, 45, 255, 255, 255, 255]),
    "AMB": (0x18FEF5E6, [199, 255, 255, 22, 34, 255, 255, 255]),
}


@pytest.fixture(scope="module")
def spark():
    from fleetpulse.bronze.spark import get_spark

    s = get_spark(app="silver-tests", master="local[2]", driver_memory="1g", shuffle_partitions=2)
    yield s
    s.stop()


def bronze_rows(frames):
    """frames: iterable of (offset_s, can_id, payload, source_file, source_line)."""
    rows = []
    for off, can_id, payload, file, line in frames:
        ts = T0 + timedelta(seconds=off)
        b = list(payload) + [None] * (8 - len(payload))
        rows.append((ts, can_id, len(payload), *b, "arch.tar.xz", file, line,
                     datetime(2026, 1, 1), "run-1", ts.date()))
    return rows


def bronze_df(spark, frames):
    return spark.createDataFrame(bronze_rows(frames), BRONZE_CAN_FRAMES)


def stream(messages=("EEC1", "EEC2", "CCVS", "LFE", "HRLFC", "VDHR", "ET1", "AMB"), start=0.0, seconds=10.0,
           file="p/a.csv", drop=None, payload_override=None):
    """Regular traffic of the given messages at their FMS rates (+ per-message phase)."""
    drop = drop or {}
    out = []
    for k, acr in enumerate(messages):
        period = MSG[acr].nominal_period_ms / 1000
        t = start + 0.0007 * (k + 1)
        while t < start + seconds:
            lo_hi = drop.get(acr)
            if not (lo_hi and lo_hi[0] <= t - start < lo_hi[1]):
                can_id, payload = FRAME[acr]
                if payload_override and acr in payload_override:
                    payload = payload_override[acr](t - start) or payload
                out.append((round(t, 6), can_id, payload))
            t += period
    out.sort()
    return [(t, c, p, file, i + 2) for i, (t, c, p) in enumerate(out)]


def silver_from(spark, frames, gap_s=1.0):
    bronze = bronze_df(spark, frames)
    pieces = sessions.file_pieces(bronze, gap_s)
    sess, breaks = sessions.assemble_sessions(pieces, gap_s)
    sdf = spark.createDataFrame(sessions.session_rows(sess, VID, gap_s, BID), RECORDING_SESSIONS)
    events = sessions.assign_sessions(decode.decode(bronze, VID, BID), sdf)
    tele = alignment.build(events, sdf, VID, BID)
    return sess, breaks, events, tele


# --- J1939 identifier -----------------------------------------------------------
KNOWN_IDS = [0x0CF004E6, 0x18EAFF00, 0x18EA17F9, 0x15FF55E6, 0x08FE6EE6, 0x18E5FFB8, 0x1CFF9053,
             0x18FEF1E6, 0x18FD09E6, 0x10FF80E6, 0x18FEC1E6, 0x0CF003E6, 0x18ECFFE6]


def _ids(spark, ids):
    rows = [(T0, i, 8, *[0] * 8, "a", "f", n + 2, T0, "r", T0.date()) for n, i in enumerate(ids)]
    df = decode.with_identifier(spark.createDataFrame(rows, BRONZE_CAN_FRAMES))
    return {r.can_id: r for r in df.collect()}


def test_known_can_ids_match_reference_parser(spark):
    got = _ids(spark, KNOWN_IDS)
    for can_id in KNOWN_IDS:
        ref = parse_can_id(can_id)
        r = got[can_id]
        assert (r.priority, r.pgn, r.source_address) == (ref.priority, ref.pgn, ref.source_address), hex(can_id)


def test_pdu2_pgn_includes_group_extension(spark):
    got = _ids(spark, [0x0CF004E6, 0x18FEF1E6, 0x15FF55E6])
    assert got[0x0CF004E6].pgn == 61444  # EEC1
    assert got[0x18FEF1E6].pgn == 65265  # CCVS
    assert got[0x15FF55E6].pgn == 130901  # data page 1


def test_pdu1_pgn_excludes_destination(spark):
    got = _ids(spark, [0x18EAFF00, 0x18EA17F9, 0x18ECFFE6])
    assert got[0x18EAFF00].pgn == got[0x18EA17F9].pgn == 59904  # Request, dest 0xFF / 0x17
    assert got[0x18ECFFE6].pgn == 60416  # TP.CM


def test_source_address_and_priority(spark):
    got = _ids(spark, [0x18EA17F9, 0x0CF004E6, 0x08FE6EE6])
    assert got[0x18EA17F9].source_address == 0xF9
    assert (got[0x0CF004E6].priority, got[0x0CF004E6].source_address) == (3, 0xE6)
    assert got[0x08FE6EE6].priority == 2


# --- decoding ---------------------------------------------------------------------
def _events(spark, frames):
    ev = decode.decode(bronze_df(spark, frames), VID, BID).collect()
    return {(r.source_line, r.signal_name): r for r in ev}


def one(acr, payload):
    return [(0.0, FRAME[acr][0], payload, "p/a.csv", 2)]


def test_little_endian_multibyte(spark):
    ev = _events(spark, one("EEC1", FRAME["EEC1"][1]))
    assert ev[(2, "engine_speed_rpm")].signal_raw == 194 + 18 * 256 == 4802
    assert ev[(2, "engine_speed_rpm")].signal_value == 600.25
    ev = _events(spark, one("VDHR", FRAME["VDHR"][1]))
    assert ev[(2, "total_distance_m")].signal_raw == 0x001AB5B0  # bytes 1-4
    ev = _events(spark, one("HRLFC", FRAME["HRLFC"][1]))
    assert ev[(2, "total_fuel_used_l")].signal_value == pytest.approx(0x0027CAD6 * 0.001)  # bytes 5-8


@pytest.mark.parametrize(
    "acr,payload,name,value",
    [
        ("ET1", [120, 255, 98, 45, 255, 255, 255, 255], "coolant_temp_c", 80.0),
        ("ET1", [0, 255, 98, 45, 255, 255, 255, 255], "coolant_temp_c", -40.0),
        ("AMB", [199, 255, 255, 22, 34, 255, 255, 255], "ambient_temp_c", (34 * 256 + 22) * 0.03125 - 273),
        ("LFE", [12, 0, 255, 255, 255, 255, 255, 255], "fuel_rate_lph", 0.6),
        ("EEC2", [241, 250, 3, 255, 255, 255, 255, 255], "accelerator_pedal_pct", 100.0),
        ("CCVS", [243, 76, 47, 196, 0, 255, 224, 255], "wheel_speed_kph", (47 * 256 + 76) / 256),
    ],
)
def test_scaling_and_offsets(spark, acr, payload, name, value):
    r = _events(spark, one(acr, payload))[(2, name)]
    assert r.signal_status == "valid" and r.signal_value == pytest.approx(value)


@pytest.mark.parametrize(
    "acr,payload,name",
    [
        ("EEC1", [240, 125, 131, 255, 255, 255, 255, 255], "engine_speed_rpm"),
        ("ET1", [255, 255, 98, 45, 255, 255, 255, 255], "coolant_temp_c"),
        ("HRLFC", [255, 255, 255, 255, 0, 0, 0, 255], "total_fuel_used_l"),
        ("CCVS", [243, 255, 255, 196, 0, 255, 224, 255], "wheel_speed_kph"),
    ],
)
def test_not_available_is_null_not_zero(spark, acr, payload, name):
    r = _events(spark, one(acr, payload))[(2, name)]
    assert r.signal_status == "not_available" and r.signal_value is None and r.signal_raw is not None


def test_physical_zero_is_a_valid_zero(spark):
    r = _events(spark, one("CCVS", [243, 0, 0, 196, 0, 255, 224, 255]))[(2, "wheel_speed_kph")]
    assert (r.signal_status, r.signal_value) == ("valid", 0.0)


@pytest.mark.parametrize(
    "byte4,brake,status",
    [(0b11_00_01_00, 0.0, "valid"), (0b11_01_01_00, 1.0, "valid"),
     (0b11_10_01_00, None, "error"), (0b11_11_01_00, None, "not_available")],
)
def test_switch_values(spark, byte4, brake, status):
    payload = [243, 76, 47, byte4, 0, 255, 224, 255]
    ev = _events(spark, one("CCVS", payload))
    assert (ev[(2, "brake_switch")].signal_value, ev[(2, "brake_switch")].signal_status) == (brake, status)
    assert ev[(2, "clutch_switch")].signal_status == "not_available"  # bits 7-8 = 11
    assert ev[(2, "cruise_control_active")].signal_value == 0.0


BOUNDARY_BYTES = [0x00, 0x01, 0x7F, 0xFA, 0xFB, 0xFD, 0xFE, 0xFF]


def test_every_signal_matches_reference_decoder_on_boundary_payloads(spark):
    """Exhaustive-ish: every verified signal x payloads built from J1939 boundary bytes."""
    rng = np.random.default_rng(7)
    payloads = [[b] * 8 for b in BOUNDARY_BYTES] + [list(rng.choice(BOUNDARY_BYTES, 8)) for _ in range(60)]
    payloads = [[int(x) for x in p] for p in payloads]
    frames, line = [], 2
    for m in MESSAGES:
        for p in payloads:
            frames.append((0.001 * line, FRAME[m.acronym][0], p, "p/a.csv", line))
            line += 1
    ev = _events(spark, frames)
    ref_frames = pd.DataFrame([p for _, _, p, _, _ in frames], columns=[f"b{i}" for i in range(8)]).astype("UInt8")
    checked = 0
    for idx, (_, can_id, _, _, ln) in enumerate(frames):
        m = next(m for m in MESSAGES if parse_can_id(can_id).pgn == m.pgn)
        for s in m.signals:
            ref = decode_signal(ref_frames.iloc[[idx]], s).iloc[0]
            got = ev[(ln, s.name)]
            assert got.signal_status == ref[f"{s.name}_status"]
            assert got.signal_raw == int(ref[f"{s.name}_raw"])
            rv = ref[s.name]
            assert (got.signal_value is None) == bool(pd.isna(rv))
            if got.signal_value is not None:
                assert got.signal_value == float(rv)
            checked += 1
    assert checked == len(payloads) * len(SIGNALS)


def test_lineage_is_preserved(spark):
    r = _events(spark, [(1.5, FRAME["EEC1"][0], FRAME["EEC1"][1], "part_3/x.csv", 1234)])[(1234, "engine_speed_rpm")]
    assert (r.source_archive, r.source_file, r.source_line, r.ingestion_run_id) == ("arch.tar.xz", "part_3/x.csv", 1234, "run-1")
    assert (r.can_id, r.pgn, r.spn, r.message, r.vehicle_id) == (0x0CF004E6, 61444, 190, "EEC1", VID)
    assert r.event_timestamp == T0 + timedelta(seconds=1.5) and r.event_date == T0.date()


def test_recording_gap_default_is_one_second_everywhere():
    from fleetpulse.pipelines import build_session, inspect_gaps, silver_build, silver_parity
    from fleetpulse.validation.continuity import DEFAULT_RECORDING_GAP_S

    assert DEFAULT_RECORDING_GAP_S == 1.0
    for mod in (build_session, inspect_gaps, silver_build, silver_parity):
        assert mod.DEFAULT_RECORDING_GAP_S is DEFAULT_RECORDING_GAP_S


def test_short_dlc_frames_of_verified_pgns_are_excluded_and_counted(spark):
    frames = one("EEC1", FRAME["EEC1"][1]) + [(0.01, FRAME["EEC1"][0], [240, 125, 131, 194], "p/a.csv", 3)]
    df = bronze_df(spark, frames)
    assert decode.decode(df, VID, BID).count() == 1
    assert [(r.pgn, r["count"]) for r in decode.excluded_frames(decode.with_identifier(df)).collect()] == [(61444, 1)]
    assert decode.expected_event_count(df) == 1


# --- recording sessions -----------------------------------------------------------
def _sessions(spark, frames, gap=1.0):
    return sessions.assemble_sessions(sessions.file_pieces(bronze_df(spark, frames), gap), gap)


def test_gap_above_threshold_splits_equal_does_not(spark):
    f = [(0.0, 1, [1], "p/a.csv", 2), (1.0, 1, [1], "p/a.csv", 3), (2.5, 1, [1], "p/a.csv", 4)]
    sess, breaks = _sessions(spark, f)
    assert len(sess) == 2 and breaks[0]["kind"] == "within_file" and breaks[0]["gap_s"] == pytest.approx(1.5)
    assert [s.frames for s in sess] == [2, 1]


def test_between_file_gap_and_internal_gap(spark):
    a = stream(("EEC1",), 0, 5, "p/a.csv")
    b = stream(("EEC1",), 5.02, 3, "p/b.csv") + stream(("EEC1",), 20, 3, "p/b.csv")
    b = [(t, c, p, f, i + 2) for i, (t, c, p, f, _) in enumerate(b)]
    c = stream(("EEC1",), 60, 2, "p/c.csv")
    sess, breaks = _sessions(spark, a + b + c)
    assert [x["kind"] for x in breaks] == ["within_file", "between_files"]
    assert len(sess) == 3 and sess[0].first_file == "p/a.csv" and sess[0].last_file == "p/b.csv"


def test_long_continuous_parked_stream_is_one_session(spark):
    parked = stream(("EEC1", "CCVS"), 0, 300, "p/a.csv", payload_override={
        "CCVS": lambda t: [243, 0, 0, 196, 0, 255, 224, 255]})  # 0 km/h for 5 minutes
    sess, breaks = _sessions(spark, parked)
    assert len(sess) == 1 and breaks == []


def test_structural_errors_raise(spark):
    with pytest.raises(ContinuityError, match="overlap"):
        _sessions(spark, [(0, 1, [1], "p/a.csv", 2), (5, 1, [1], "p/a.csv", 3), (4, 1, [1], "p/b.csv", 2)])
    with pytest.raises(ContinuityError, match="jumps back"):
        _sessions(spark, [(0, 1, [1], "p/a.csv", 2), (5, 1, [1], "p/a.csv", 3), (1, 1, [1], "p/a.csv", 4)])


# --- telemetry --------------------------------------------------------------------
def _tele(spark, frames):
    sess, breaks, events, tele = silver_from(spark, frames)
    return sess, pd.DataFrame([r.asDict() for r in tele.orderBy("grid_timestamp").collect()])


def test_grid_follows_recording_session_boundaries(spark):
    sess, t = _tele(spark, stream(("EEC1", "CCVS", "AMB"), 0.037, 3.0))
    ts = pd.to_datetime(t["grid_timestamp"])
    (s,) = sess
    assert (ts.diff().dropna() == pd.Timedelta(milliseconds=100)).all()
    assert ts.iloc[0] == pd.Timestamp(s.start).ceil("100ms") == pd.Timestamp(T0) + pd.Timedelta(milliseconds=100)
    assert ts.iloc[-1] == pd.Timestamp(s.end).floor("100ms")
    assert (ts.dt.microsecond % 100_000 == 0).all()
    assert list(TELEMETRY.fieldNames()) == list(t.columns)


def test_no_look_ahead_and_exact_match(spark):
    eec1 = FRAME["EEC1"][0]
    rpm = lambda v: [240, 125, 131, v & 0xFF, v >> 8, 255, 255, 255]  # noqa: E731
    frames = [(0.000, eec1, rpm(8000), "p/a.csv", 2),   # 1000 rpm
              (0.100, eec1, rpm(4800), "p/a.csv", 3),   # 600 rpm exactly on the grid point
              (0.150, eec1, rpm(8000), "p/a.csv", 4),
              (0.200001, eec1, rpm(16000), "p/a.csv", 5),  # 1 us after grid point 0.2
              (0.210, eec1, rpm(8000), "p/a.csv", 6)]
    _, t = _tele(spark, frames)
    got = dict(zip(pd.to_datetime(t["grid_timestamp"]), t["engine_speed_rpm"]))
    assert got[pd.Timestamp(T0) + pd.Timedelta(milliseconds=100)] == 600.0  # exact match used
    assert got[pd.Timestamp(T0) + pd.Timedelta(milliseconds=200)] == 1000.0  # 0.150 frame, not the future one


def test_freshness_expiration_and_status_explain_nulls(spark):
    # HRLFC (1 Hz, max age 3 s) drops out from 2 s to 8 s while everything else keeps going.
    frames = stream(drop={"HRLFC": (2.0, 8.0)}, seconds=12,
                    payload_override={"ET1": lambda t: [255, 255, 98, 45, 255, 255, 255, 255]})
    _, t = _tele(spark, frames)
    rel = (pd.to_datetime(t["grid_timestamp"]) - pd.Timestamp(T0)).dt.total_seconds()
    stale = t[(rel > 4.1) & (rel < 7.9)]
    assert (stale["hrlfc_freshness"] == "stale").all() and stale["total_fuel_used_l"].isna().all()
    assert stale["hrlfc_age_ms"].isna().all()
    fresh = t[t["hrlfc_freshness"] == "fresh"]
    assert (fresh["hrlfc_age_ms"] <= 3000).all() and fresh["total_fuel_used_l"].notna().all()
    # not_available frame: fresh, status says why the value is null
    assert (t["et1_freshness"] == "fresh").all() and t["coolant_temp_c"].isna().all()
    assert (t["coolant_temp_c_status"] == "not_available").all()


def test_message_absent_from_session_is_no_observation(spark):
    sess, t = _tele(spark, stream(("EEC1", "CCVS"), 0, 2))
    assert (t["amb_freshness"] == "no_observation").all() and t["ambient_temp_c"].isna().all()
    assert t["ambient_temp_c_status"].isna().all() and t["amb_age_ms"].isna().all()
    # the absent message does not shorten the session's grid
    assert pd.to_datetime(t["grid_timestamp"]).iloc[-1] == pd.Timestamp(sess[0].end).floor("100ms")
    assert t["engine_speed_rpm"].notna().all()


def _rel(t):
    return (pd.to_datetime(t["grid_timestamp"]) - pd.Timestamp(T0)).dt.total_seconds().round(1)


def test_rpm_continues_when_ambient_stops(spark):
    # AMB (1 Hz, max age 3 s) stops at 3 s; everything else runs to 10 s.
    sess, t = _tele(spark, stream(seconds=10, drop={"AMB": (3.0, 99)}))
    rel = _rel(t)
    assert rel.iloc[-1] == 9.9  # grid runs to the session end, not to AMB's last frame
    assert t["engine_speed_rpm"].notna().all() and (t["eec1_freshness"] == "fresh").all()
    last_amb = 2.0 + 0.0007 * 8  # last AMB frame (8th message in the stream helper)
    fresh = rel <= last_amb + 3.0
    assert t.loc[fresh & (rel > 0.1), "ambient_temp_c"].notna().all()
    assert t.loc[~fresh, "ambient_temp_c"].isna().all()
    assert (t.loc[~fresh, "amb_freshness"] == "stale").all()


def test_speed_continues_when_fuel_rate_stops(spark):
    # LFE (10 Hz, max age 300 ms) stops at 4 s.
    _, t = _tele(spark, stream(seconds=8, drop={"LFE": (4.0, 99)}))
    rel = _rel(t)
    assert rel.iloc[-1] == 7.9
    assert t["wheel_speed_kph"].notna().all()
    assert t.loc[rel >= 4.3, "fuel_rate_lph"].isna().all() and (t.loc[rel >= 4.3, "lfe_freshness"] == "stale").all()
    assert t.loc[(rel > 0.1) & (rel < 4.0), "fuel_rate_lph"].notna().all()


def test_message_resumes_after_becoming_stale(spark):
    _, t = _tele(spark, stream(seconds=12, drop={"HRLFC": (2.0, 8.0)}))
    rel = _rel(t)
    states = t.set_index(rel)["hrlfc_freshness"]
    assert states.loc[5.0] == "stale"
    assert states.loc[9.0] == "fresh" and t.set_index(rel).loc[9.0, "total_fuel_used_l"] is not None
    assert (states.loc[8.1:] == "fresh").all()


def test_session_shorter_than_one_grid_interval(spark):
    # 10-50 ms: contains no 100 ms grid point -> no rows, no error
    sess, t = _tele(spark, [(0.010 + 0.01 * i, FRAME["EEC1"][0], FRAME["EEC1"][1], "p/a.csv", i + 2) for i in range(5)])
    assert len(sess) == 1 and t.empty
    # 50-120 ms: contains exactly the 100 ms grid point
    sess, t = _tele(spark, [(0.050 + 0.01 * i, FRAME["EEC1"][0], FRAME["EEC1"][1], "p/a.csv", i + 2) for i in range(8)])
    assert list(_rel(t)) == [0.1] and t["engine_speed_rpm"].iloc[0] == 600.25


def test_session_ending_between_grid_points(spark):
    frames = [(0.02 * i, FRAME["EEC1"][0], FRAME["EEC1"][1], "p/a.csv", i + 2) for i in range(118)]  # last at 2.34 s
    sess, t = _tele(spark, frames)
    assert pd.Timestamp(sess[0].end) == pd.Timestamp(T0) + pd.Timedelta(seconds=2.34)
    assert _rel(t).iloc[-1] == 2.3 and _rel(t).iloc[0] == 0.0


def test_no_forward_fill_across_recording_sessions(spark):
    a = stream(("EEC1", "HRLFC"), 0, 3)
    b = [(t + 10, c, [1, 2, 3, 4, 5, 6, 7, 8] if c == FRAME["HRLFC"][0] else p, f, n + len(a))
         for t, c, p, f, n in stream(("EEC1", "HRLFC"), 0, 3)]
    sess, t = _tele(spark, a + b)
    assert len(sess) == 2
    s2 = t[t["recording_session_id"] == sessions.session_id(VID, sess[1].start)]
    assert pd.to_datetime(s2["grid_timestamp"]).min() >= pd.Timestamp(sess[1].start)
    assert (s2["total_fuel_used_l"] != pytest.approx(0x0027CAD6 * 0.001)).all()  # nothing carried over
    gap_rows = t[(pd.to_datetime(t["grid_timestamp"]) > pd.Timestamp(sess[0].end))
                 & (pd.to_datetime(t["grid_timestamp"]) < pd.Timestamp(sess[1].start))]
    assert gap_rows.empty


def test_telemetry_parity_with_reference_including_stale_periods(spark):
    """Synthetic dropouts (stale periods inside one session) vs reference build_telemetry."""
    frames = stream(seconds=15, drop={"EEC1": (3.0, 3.25), "CCVS": (5.0, 5.9), "HRLFC": (6.0, 11.0),
                                      "AMB": (1.0, 9.0), "EEC2": (8.0, 8.4)},
                    payload_override={"LFE": lambda t: [255, 255, 0, 0, 0, 0, 0, 0] if 9 < t < 10 else None})
    _, t = _tele(spark, frames)
    ref_frames = pd.DataFrame(
        {"timestamp": [T0 + timedelta(seconds=o) for o, *_ in frames], "can_id": [c for _, c, *_ in frames],
         **{f"b{i}": pd.array([p[i] for _, _, p, *_ in frames], dtype="UInt8") for i in range(8)}}
    )
    ref_frames["timestamp"] = ref_frames["timestamp"].astype("datetime64[us]")
    ref_frames["pgn"] = [parse_can_id(c).pgn for c in ref_frames["can_id"]]
    ref_frames["source_address"] = 0xE6
    tables = {}
    for m in MESSAGES:
        sel = ref_frames[ref_frames["pgn"] == m.pgn].reset_index(drop=True)
        tables[m.key] = pd.concat([sel[["timestamp", "source_address"]]] + [decode_signal(sel, s) for s in m.signals], axis=1)
    ref = build_telemetry(tables)
    # Production contract: Silver grid spans the recording session; the reference grid
    # (latest first .. earliest last observation) must be contained in it and agree cell by cell.
    grid = pd.to_datetime(t["grid_timestamp"])
    assert set(ref["timestamp"]) <= set(grid) and len(t) > len(ref)
    t = t[grid.isin(ref["timestamp"])].reset_index(drop=True)
    assert list(pd.to_datetime(t["grid_timestamp"])) == list(ref["timestamp"])
    stale_cells = 0
    for m in MESSAGES:
        age = f"{m.key}_age_ms"
        assert (t[age].isna() == ref[age].isna()).all(), m.key
        assert np.allclose(t[age].dropna(), ref[age].dropna(), atol=1e-6)
        stale_cells += int(ref[age].isna().sum())
        for s in m.signals:
            a = pd.to_numeric(t[s.name], errors="coerce").astype(float)
            b = pd.to_numeric(ref[s.name], errors="coerce").astype(float)
            assert (a.isna() == b.isna()).all(), s.name
            assert np.allclose(a.dropna(), b.dropna(), atol=1e-9, rtol=1e-12), s.name
    assert stale_cells > 100  # the test really exercised freshness expiry


# --- end-to-end: storage, idempotency, reference parity on real files ------------
REAL = Path(__file__).resolve().parents[1] / "data/raw/part_3"
REAL_FILES = ["20210119101259207093.csv", "20210119101358162388.csv", "20210119105907167768.csv"]


@pytest.fixture(scope="module")
def real_build(spark, tmp_path_factory):
    if not all((REAL / f).exists() for f in REAL_FILES):
        pytest.skip("real part_3 files not extracted")
    from fleetpulse.bronze.storage import ParquetBronzeStore
    from fleetpulse.pipelines.bronze_ingest import BronzeIngestion
    from fleetpulse.pipelines.silver_build import build_silver

    tmp = tmp_path_factory.mktemp("silver_e2e")
    archive = tmp / "real.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        for f in REAL_FILES:
            tar.add(REAL / f, arcname=f"part_3/{f}")
    BronzeIngestion(spark, ParquetBronzeStore(tmp / "bronze")).run(archive)
    m1 = build_silver(spark, tmp / "bronze", tmp / "silver")
    return tmp, archive, m1


def test_reference_parity_on_real_files(real_build):
    from fleetpulse.pipelines.silver_parity import run

    tmp, archive, _ = real_build
    report = run(archive, [f"part_3/{f}" for f in REAL_FILES], tmp / "silver", 1.0, tmp / "parity")
    assert report["parity"], json.dumps(report, default=str)[:2000]
    assert report["decode_parity_exact"] and report["session_parity_exact"] and report["alignment_parity_on_shared_grid"]
    assert report["telemetry"]["totals"]["silver_only_rows"] > 0  # intentional grid-lifetime difference
    assert report["recording_sessions"]["reference_sessions"] == 3  # 2 contiguous files + internal 51.5 min gap
    assert report["signal_events"]["compared"] > 20000
    assert report["telemetry"]["totals"]["cells"] > 10000


def test_parity_tool_detects_injected_differences(real_build):
    from fleetpulse.pipelines.silver_parity import compare_events, reference_events, run_reference

    tmp, archive, _ = real_build
    _, ref_out = run_reference(archive, [f"part_3/{REAL_FILES[0]}"], tmp / "inject", 1.0)
    ref = reference_events(ref_out)
    silver = ref.copy()
    silver.loc[5, "signal_value"] += 0.125
    silver.loc[7, "signal_status"] = "error"
    silver = silver.drop(index=9)
    r = compare_events(ref, silver)
    assert r["mismatches"]["signal_value"] == 1 and r["mismatches"]["signal_status"] == 1
    assert r["only_in_reference"] == 1


def test_rebuild_is_idempotent_and_recovers(spark, real_build):
    from fleetpulse.pipelines.silver_build import build_silver
    from fleetpulse.silver.storage import EVENTS, TELEMETRY_TABLE, ParquetSilverStore

    tmp, _, m1 = real_build
    store = ParquetSilverStore(tmp / "silver")
    before = {t: store.read(spark, t).drop("silver_build_id").orderBy(*store.read(spark, t).columns[:4]).collect()
              for t in (EVENTS, TELEMETRY_TABLE)}
    # simulate a crash mid-publish: current table moved to trash, staging left behind
    (tmp / "silver" / "_trash" / "crashed").mkdir(parents=True)
    (tmp / "silver" / TELEMETRY_TABLE).rename(tmp / "silver" / "_trash" / "crashed" / TELEMETRY_TABLE)
    (tmp / "silver" / "_staging" / "crashed").mkdir(parents=True)
    m2 = build_silver(spark, tmp / "bronze", tmp / "silver")
    assert m2["recovered"] == {"restored_tables": 1}
    assert (m1["signal_events"], m1["telemetry_rows"]) == (m2["signal_events"], m2["telemetry_rows"])
    after = {t: store.read(spark, t).drop("silver_build_id").orderBy(*store.read(spark, t).columns[:4]).collect()
             for t in (EVENTS, TELEMETRY_TABLE)}
    assert before == after
    assert not (tmp / "silver" / "_staging").exists() and not (tmp / "silver" / "_trash").exists()
    assert len(list((tmp / "silver" / "_builds").glob("*.json"))) == 2
    assert {p.name for p in (tmp / "silver" / EVENTS).iterdir()} == {"event_date=2021-01-19"}
