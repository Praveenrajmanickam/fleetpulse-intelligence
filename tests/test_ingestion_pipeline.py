import hashlib
import json

import pandas as pd
import pytest

from fleetpulse.ingestion.can_csv import RawFormatError, read_can_csv
from fleetpulse.j1939.definitions import MESSAGES
from fleetpulse.pipelines.decode_can_file import build_frames, decode_message, run

EEC1 = next(m for m in MESSAGES if m.acronym == "EEC1")

SAMPLE = """timestamp;id;dlc;data
2021-01-19 10:12:59.220499;0xcf004e6;8;240;125;131;194;18;255;255;255
2021-01-19 10:12:59.279253;0x1cff80e6;1;230
2021-01-19 10:12:59.278855;0x1cff9053;4;254;31;59;128
2021-01-19 10:12:59.300000;0xcf004e6;8;240;125;131;255;255;255;255;255
2021-01-19 10:12:59.310000;0xcf004e6;8;240;125;131
"""


@pytest.fixture
def raw_file(tmp_path):
    root = tmp_path / "raw"
    (root / "part_x").mkdir(parents=True)
    path = root / "part_x" / "sample.csv"
    path.write_text(SAMPLE)
    return path


def test_reads_variable_length_rows(raw_file):
    df = read_can_csv(raw_file)
    assert len(df) == 5
    assert df["dlc"].tolist() == [8, 1, 4, 8, 8]
    assert df["n_bytes"].tolist() == [8, 1, 4, 8, 3]
    assert df.loc[0, "can_id"] == 0x0CF004E6
    assert pd.isna(df.loc[1, "b1"])
    assert df.loc[0, "source_line"] == 2


def test_rejects_unexpected_header(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("time,id\n")
    with pytest.raises(RawFormatError):
        read_can_csv(p)


def test_rejects_more_than_8_bytes(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("timestamp;id;dlc;data\n2021-01-19 10:00:00.000000;0x1;9;1;2;3;4;5;6;7;8;9\n")
    with pytest.raises(RawFormatError):
        read_can_csv(p)


def test_flags_dlc_mismatch_and_decodes_eec1(raw_file):
    frames = build_frames(read_can_csv(raw_file))
    assert frames["is_valid"].tolist() == [True, True, True, True, False]
    assert frames.loc[4, "quality_issue"] == "dlc_byte_count_mismatch"
    assert pd.isna(frames.loc[4, "pgn"])

    eec1, bad_dlc = decode_message(frames, EEC1)
    assert bad_dlc == 0
    assert len(eec1) == 2
    assert eec1.loc[0, "engine_speed_rpm"] == pytest.approx(600.25)
    assert eec1.loc[1, "engine_speed_rpm_status"] == "not_available"


def test_run_writes_outputs_and_preserves_raw(raw_file, tmp_path):
    before = hashlib.sha256(raw_file.read_bytes()).hexdigest()
    report = run(raw_file, tmp_path / "processed", raw_file.parents[1])
    assert hashlib.sha256(raw_file.read_bytes()).hexdigest() == before
    assert report["run"]["input_sha256"] == before

    out = tmp_path / "processed"
    frames = pd.read_parquet(out / "can_frames" / "part_x" / "sample.parquet")
    assert len(frames) == 5
    eec1 = pd.read_parquet(out / "signals" / "eec1" / "part_x" / "sample.parquet")
    assert len(eec1) == 2
    saved = json.loads((out / "reports" / "part_x" / "sample_report.json").read_text())
    assert saved["totals"]["unique_pgns"] == 3
    categories = {u["pgn"]: u["pgn_category"] for u in saved["unknown_pgns"]}
    assert categories == {65408: "proprietary_b", 65424: "proprietary_b"}
