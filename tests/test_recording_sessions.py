"""End-to-end recording-session behaviour of build_session and inspect_gaps."""

import io
import json
import tarfile

import pandas as pd
import pytest

from fleetpulse.ingestion.can_csv import read_can_timestamps
from fleetpulse.pipelines.build_session import build_session
from fleetpulse.pipelines.inspect_gaps import inspect
from fleetpulse.validation.continuity import ContinuityError

T0 = pd.Timestamp("2021-01-19 09:00:00")

# (can_id, period_s, payload) - real payloads from the Turku data; CCVS speed = 0 (stationary)
MESSAGES = [
    ("0xcf004e6", 0.02, "240;125;131;194;18;255;255;255"),  # EEC1 600.25 rpm
    ("0xcf003e6", 0.05, "241;0;3;255;255;255;255;255"),  # EEC2
    ("0x18fef1e6", 0.1, "243;0;0;196;0;255;224;255"),  # CCVS 0 km/h
    ("0x18fef2e6", 0.1, "12;0;255;255;255;255;255;255"),  # LFE
    ("0x18fd09e6", 1.0, "255;255;255;255;214;202;39;0"),  # HRLFC
    ("0x18fec1e6", 1.0, "176;181;26;0;178;181;26;0"),  # VDHR
    ("0x18feeee6", 1.0, "120;255;98;45;255;255;255;255"),  # ET1
    ("0x18fef5e6", 1.0, "199;255;255;22;34;255;255;255"),  # AMB
]


def _frames(start_s, end_s):
    rows = []
    for i, (can_id, period, payload) in enumerate(MESSAGES):
        t = start_s + i * 0.0003
        while t < end_s:
            rows.append((t, can_id, payload))
            t += period
    return sorted(rows)


def _write(path, *segments):
    lines = ["timestamp;id;dlc;data"]
    for start, end in segments:
        for t, can_id, payload in _frames(start, end):
            ts = (T0 + pd.Timedelta(seconds=t)).strftime("%Y-%m-%d %H:%M:%S.%f")
            lines.append(f"{ts};{can_id};8;{payload}")
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture
def raw(tmp_path):
    d = tmp_path / "raw" / "part_x"
    d.mkdir(parents=True)
    return d


def test_internal_gap_splits_into_two_recording_sessions(raw, tmp_path):
    a = _write(raw / "20210119090000000000.csv", (0, 20))
    # second file: 10 s of traffic, 120 s silence inside the file, 20 s of traffic
    b = _write(raw / "20210119090020001000.csv", (20.001, 30), (150, 170))
    out = tmp_path / "processed"
    rep = build_session([a, b], out, raw.parent, recording_gap_s=3.0)

    assert len(rep["sessions"]) == 2
    (brk,) = rep["breaks"]
    assert brk["kind"] == "within_file" and brk["before_file"] == brk["after_file"] == b.name
    assert brk["gap_s"] == pytest.approx(120, abs=0.1)

    s1, s2 = rep["sessions"]
    assert s1["duration_s"] == pytest.approx(30, abs=0.1)
    assert s2["duration_s"] == pytest.approx(20, abs=0.1)
    for s in (s1, s2):
        d = out / "sessions" / s["session_id"]
        tel = pd.read_parquet(d / "telemetry.parquet")
        assert tel["timestamp"].min() >= pd.Timestamp(s["first_timestamp"])
        assert tel["timestamp"].max() <= pd.Timestamp(s["last_timestamp"])
        assert tel["engine_speed_rpm"].notna().all()  # no silent empty stretch
        man = pd.read_csv(d / "raw_manifest.csv")
        assert man["frames"].sum() == s["total_can_frames"]
    # the split file contributes to both sessions
    m1 = pd.read_csv(out / "sessions" / s1["session_id"] / "raw_manifest.csv")
    m2 = pd.read_csv(out / "sessions" / s2["session_id"] / "raw_manifest.csv")
    assert b.name in set(m1["file"]) and set(m2["file"]) == {b.name}

    rec = out / "recordings" / rep["run"]["build_id"]
    breaks = pd.read_csv(rec / "session_breaks.csv")
    assert breaks.loc[0, "before_source_line"] + 1 == breaks.loc[0, "after_source_line"]
    dist = pd.read_csv(rec / "gap_distribution.csv")
    assert dist.loc[dist["above_threshold"], "total"].sum() == 1


def test_fail_mode_raises_and_still_writes_gap_report(raw, tmp_path):
    f = _write(raw / "20210119090000000000.csv", (0, 10), (100, 110))
    out = tmp_path / "processed"
    with pytest.raises(ContinuityError, match="recording gap"):
        build_session([f], out, raw.parent, recording_gap_s=3.0, on_gap="fail")
    (rec,) = (out / "recordings").iterdir()
    assert json.loads((rec / "gap_report.json").read_text())["stats"]["gaps_above_threshold"] == 1
    assert not (out / "sessions").exists()


def test_long_stationary_recording_is_one_session(raw, tmp_path):
    # 60 s parked, engine idling, 0 km/h, bus broadcasting normally: far longer than the threshold.
    f = _write(raw / "20210119090000000000.csv", (0, 60))
    rep = build_session([f], tmp_path / "processed", raw.parent, recording_gap_s=3.0)
    assert len(rep["sessions"]) == 1 and rep["breaks"] == []
    assert rep["sessions"][0]["duration_s"] == pytest.approx(60, abs=0.1)


def test_between_file_gap_splits(raw, tmp_path):
    a = _write(raw / "20210119090000000000.csv", (0, 10))
    b = _write(raw / "20210119090500000000.csv", (300, 310))
    rep = build_session([a, b], tmp_path / "processed", raw.parent, recording_gap_s=3.0)
    assert len(rep["sessions"]) == 2
    assert rep["breaks"][0]["kind"] == "between_files"


def test_read_can_timestamps_matches_path_and_buffer(raw):
    f = _write(raw / "x.csv", (0, 1))
    a = read_can_timestamps(f)
    b = read_can_timestamps(io.BytesIO(f.read_bytes()), "x.csv")
    pd.testing.assert_frame_equal(a, b)
    assert a["source_line"].iloc[0] == 2 and a["timestamp"].notna().all()


def test_inspect_gaps_from_archive_equals_from_files(raw, tmp_path):
    files = [
        _write(raw / "20210119090000000000.csv", (0, 5), (20, 25)),
        _write(raw / "20210119090025001000.csv", (25.001, 30)),
    ]
    tar_path = tmp_path / "part_x.tar.xz"
    with tarfile.open(tar_path, "w:xz") as tar:
        for f in reversed(files):  # archive order != chronological order
            tar.add(f, arcname=f"part_x/{f.name}")
    inspect(files, None, tmp_path / "from_files", 3.0)
    inspect([], tar_path, tmp_path / "from_archive", 3.0)
    for name in ("gap_distribution.csv", "recording_sessions.csv", "session_breaks.csv"):
        pd.testing.assert_frame_equal(
            pd.read_csv(tmp_path / "from_files" / name), pd.read_csv(tmp_path / "from_archive" / name)
        )
    assert len(pd.read_csv(tmp_path / "from_files" / "recording_sessions.csv")) == 2
