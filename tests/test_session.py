"""Archive window selection, timestamp parsing and telemetry alignment.

Recording-session continuity is tested in test_continuity.py."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from fleetpulse.ingestion.archive import ArchiveMember, filename_start_time, select_window
from fleetpulse.ingestion.can_csv import read_can_csv
from fleetpulse.j1939.definitions import MESSAGES
from fleetpulse.telemetry.alignment import AmbiguousSourceError, align_message, build_telemetry, make_grid

CCVS = next(m for m in MESSAGES if m.acronym == "CCVS")
EEC1 = next(m for m in MESSAGES if m.acronym == "EEC1")
T0 = pd.Timestamp("2021-01-19 10:00:00")


def ms(x):
    return pd.Timedelta(milliseconds=x)


# --- ingestion -------------------------------------------------------------

def test_whole_second_timestamp_is_parsed(tmp_path):
    # Real row from 20210119101555909929.csv line 46872
    p = tmp_path / "x.csv"
    p.write_text(
        "timestamp;id;dlc;data\n"
        "2021-01-19 10:16:50.996444;0x18fec4b8;8;255;255;255;255;0;255;255;255\n"
        "2021-01-19 10:16:51;0x10ff80e6;8;0;0;252;109;240;144;255;255\n"
    )
    df = read_can_csv(p)
    assert df["timestamp"].tolist() == [
        pd.Timestamp("2021-01-19 10:16:50.996444"),
        pd.Timestamp("2021-01-19 10:16:51"),
    ]


# --- archive ---------------------------------------------------------------

def test_filename_start_time():
    assert filename_start_time("part_3/20210119101259207093.csv") == datetime(2021, 1, 19, 10, 12, 59, 207093)


def test_select_window():
    names = [f"part_3/2021011910{m:02d}00000000.csv" for m in range(10)]
    members = [ArchiveMember(n, 1) for n in names]
    window = select_window(members, "20210119100500000000.csv", before=2, after=1)
    assert [m.name for m in window] == names[3:7]


# --- alignment -------------------------------------------------------------

def _ccvs(times_ms, speeds, sa=230):
    n = len(times_ms)
    return pd.DataFrame(
        {
            "timestamp": [T0 + ms(t) for t in times_ms],
            "source_address": pd.array([sa] * n, dtype="UInt8"),
            "wheel_speed_kph": speeds,
            "cruise_control_active": pd.array([0] * n, dtype="Int8"),
            "brake_switch": pd.array([0] * n, dtype="Int8"),
            "clutch_switch": pd.array([pd.NA] * n, dtype="Int8"),
        }
    )


def test_grid_is_regular():
    grid = make_grid(T0 + ms(37), T0 + ms(1000), 100)
    assert grid[0] == T0 + ms(100) and grid[-1] == T0 + ms(1000)
    assert (np.diff(grid.asi8) == 100_000_000 // 1000).all()


def test_asof_uses_latest_frame_without_lookahead():
    grid = make_grid(T0, T0 + ms(300), 100)
    out = align_message(grid, _ccvs([0, 95, 205], [10.0, 11.0, 12.0]), CCVS)
    # t=0 -> frame@0; t=100 -> frame@95; t=200 -> frame@95 (205 is in the future); t=300 -> frame@205
    assert out["wheel_speed_kph"].tolist() == [10.0, 11.0, 11.0, 12.0]
    assert out["ccvs_age_ms"].tolist() == pytest.approx([0, 5, 105, 95])


def test_stale_values_are_not_forward_filled():
    grid = make_grid(T0, T0 + ms(1000), 100)
    out = align_message(grid, _ccvs([0, 100], [10.0, 11.0]), CCVS)  # then 900 ms dropout
    max_age = 3 * CCVS.nominal_period_ms  # 300 ms
    ok = out["ccvs_age_ms"].notna()
    assert out.loc[ok, "ccvs_age_ms"].max() <= max_age
    assert out["wheel_speed_kph"].tolist()[:5] == [10.0, 11.0, 11.0, 11.0, 11.0]
    assert out["wheel_speed_kph"].iloc[5:].isna().all()  # t >= 500 ms: age > 300 ms


def test_invalid_latest_frame_is_not_replaced_by_older_valid():
    grid = make_grid(T0 + ms(100), T0 + ms(100), 100)
    out = align_message(grid, _ccvs([0, 90], [10.0, np.nan]), CCVS)
    assert np.isnan(out["wheel_speed_kph"].iloc[0])


def test_multiple_source_addresses_rejected():
    table = pd.concat([_ccvs([0], [1.0], sa=230), _ccvs([50], [1.0], sa=231)])
    with pytest.raises(AmbiguousSourceError):
        align_message(make_grid(T0, T0 + ms(100)), table, CCVS)


def test_build_telemetry_uses_overlap_of_messages():
    eec1 = pd.DataFrame(
        {
            "timestamp": [T0 + ms(t) for t in range(0, 1001, 20)],
            "source_address": pd.array([230] * 51, dtype="UInt8"),
            "engine_speed_rpm": 600.0,
        }
    )
    tel = build_telemetry({"eec1": eec1, "ccvs": _ccvs(range(200, 801, 100), [5.0] * 7)})
    assert tel["timestamp"].iloc[0] == T0 + ms(200)
    assert tel["timestamp"].iloc[-1] == T0 + ms(800)
    assert tel[["engine_speed_rpm", "wheel_speed_kph"]].notna().all().all()
    assert {"eec1_age_ms", "ccvs_age_ms"} <= set(tel.columns)
