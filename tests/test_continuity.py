"""Recording-session continuity over the ordered event stream (validation.continuity)."""

import numpy as np
import pandas as pd
import pytest

from fleetpulse.validation.continuity import ContinuityError, assemble, summarise_file

T0 = pd.Timestamp("2021-01-19 09:00:00")
GAP_S = 3.0


def _stream(offsets_s):
    # The logger writes microsecond timestamps.
    us = np.round(np.asarray(offsets_s, dtype=float) * 1e6).astype(np.int64)
    ts = T0 + pd.to_timedelta(us, unit="us")
    return ts, np.arange(2, len(ts) + 2)


def _file(name, offsets_s, gap_s=GAP_S):
    ts, lines = _stream(offsets_s)
    return summarise_file(name, ts, lines, gap_s)


def _ms_frames(start_s, seconds, period_s=0.001):
    """Continuous traffic: one frame per ``period_s`` with non-cumulative jitter."""
    n = int(seconds / period_s)
    rng = np.random.default_rng(0)
    return start_s + np.arange(n) * period_s + rng.uniform(0, 0.4 * period_s, n)


# --- normal traffic --------------------------------------------------------

def test_normal_millisecond_gaps_one_session():
    a = _file("a.csv", _ms_frames(0, 60))
    b = _file("b.csv", _ms_frames(60.002, 60))
    r = assemble([a, b], GAP_S)
    assert len(r.sessions) == 1 and r.breaks == []
    assert r.stats["max_gap_s"] < 0.01
    assert r.sessions.loc[0, "frames"] == a.frames + b.frames
    assert r.sessions.loc[0, "n_files"] == 2
    d = r.distribution.set_index("bin")
    assert d["total"].sum() == r.stats["gaps_measured"] == a.frames + b.frames - 1
    assert d.loc[d["above_threshold"], "total"].sum() == 0


def test_long_stationary_period_with_continuous_can_does_not_split():
    # 30 min parked with the ignition on: the bus keeps broadcasting (10 ms here).
    parked = _file("parked.csv", _ms_frames(0, 30 * 60, period_s=0.01))
    r = assemble([parked], GAP_S)
    assert len(r.sessions) == 1 and r.breaks == []
    assert r.sessions.loc[0, "duration_s"] == pytest.approx(30 * 60, abs=1)


# --- gaps inside a file ----------------------------------------------------

def test_internal_file_gap_splits_session():
    # 36 s of traffic, 3090 s silence, 24 s of traffic: all in ONE file.
    offsets = np.r_[_ms_frames(0, 36), _ms_frames(36 + 3090, 24)]
    f = _file("20210119105907167768.csv", offsets)
    r = assemble([f], GAP_S)

    assert len(r.sessions) == 2 and len(r.breaks) == 1
    b = r.breaks[0]
    assert b.kind == "within_file"
    assert b.before_file == b.after_file == "20210119105907167768.csv"
    assert b.gap_s == pytest.approx(3090, abs=0.01)
    i = int(np.argmax(np.diff(offsets)))
    ts, _ = _stream(offsets)
    assert (b.before_timestamp, b.after_timestamp) == (ts[i], ts[i + 1])
    assert (b.before_source_line, b.after_source_line) == (i + 2, i + 3)

    s = r.sessions
    assert s["frames"].sum() == f.frames
    assert s.loc[0, "end"] == b.before_timestamp and s.loc[1, "start"] == b.after_timestamp
    assert s.loc[0, "gap_after_s"] == s.loc[1, "gap_before_s"] == pytest.approx(3090, abs=0.01)
    assert s["max_gap_s"].max() < 0.01  # neither session contains the silence
    assert r.pieces["file"].tolist() == ["20210119105907167768.csv"] * 2


def test_file_is_not_assumed_continuous_between_its_first_and_last_frame():
    # First/last timestamps look like any other 60 s file; the hole is inside.
    offsets = np.r_[_ms_frames(0, 10), _ms_frames(50, 10)]
    r = assemble([_file("x.csv", offsets)], GAP_S)
    assert len(r.sessions) == 2


# --- gaps between files ----------------------------------------------------

def test_between_file_gap_splits_session():
    a = _file("a.csv", _ms_frames(0, 30))
    b = _file("b.csv", _ms_frames(30 + 600, 30))
    c = _file("c.csv", _ms_frames(660 + 0.003, 30))
    r = assemble([c, a, b], GAP_S)  # input order does not matter

    assert len(r.sessions) == 2 and len(r.breaks) == 1
    g = r.breaks[0]
    assert g.kind == "between_files"
    assert (g.before_file, g.after_file) == ("a.csv", "b.csv")
    assert g.gap_s == pytest.approx(600, abs=0.01)
    assert g.before_timestamp == a.last_timestamp and g.after_timestamp == b.first_timestamp
    assert (g.before_source_line, g.after_source_line) == (a.last_source_line, b.first_source_line)
    assert r.sessions["n_files"].tolist() == [1, 2]
    assert r.distribution["between_files"].sum() == 2


def test_internal_and_between_file_gaps_together():
    a = _file("a.csv", np.r_[_ms_frames(0, 5), _ms_frames(20, 5)])  # internal 15 s gap
    b = _file("b.csv", _ms_frames(125, 5))  # 100 s after a
    r = assemble([a, b], GAP_S)
    assert [x.kind for x in r.breaks] == ["within_file", "between_files"]
    assert len(r.sessions) == 3


# --- threshold -------------------------------------------------------------

def test_threshold_is_configurable():
    offsets = np.r_[_ms_frames(0, 5), _ms_frames(15, 5)]  # 10 s gap
    assert len(assemble([_file("x.csv", offsets, gap_s=3.0)], 3.0).sessions) == 2
    assert len(assemble([_file("x.csv", offsets, gap_s=30.0)], 30.0).sessions) == 1


def test_gap_equal_to_threshold_does_not_split():
    r = assemble([_file("x.csv", [0.0, 1.0, 4.0, 5.0])], 3.0)
    assert len(r.sessions) == 1


# --- structural errors and bookkeeping -------------------------------------

def test_overlapping_files_raise():
    with pytest.raises(ContinuityError, match="overlap"):
        assemble([_file("a.csv", _ms_frames(0, 10)), _file("b.csv", _ms_frames(9, 10))], GAP_S)


def test_filename_order_must_match_timestamp_order():
    with pytest.raises(ContinuityError, match="filename order"):
        assemble([_file("b.csv", _ms_frames(0, 10)), _file("a.csv", _ms_frames(11, 10))], GAP_S)


def test_small_backward_step_is_counted_large_one_raises():
    f = _file("x.csv", [0.0, 0.010, 0.009, 0.020])
    assert f.backward_steps == 1 and f.max_backward_s == pytest.approx(0.001)
    with pytest.raises(ContinuityError, match="jumps back"):
        _file("y.csv", [0.0, 10.0, 1.0, 11.0])


def test_unparseable_timestamps_are_ignored():
    ts, lines = _stream([0.0, 0.001, 0.002])
    ts = pd.Series(ts).astype("datetime64[us]")
    ts[1] = pd.NaT
    f = summarise_file("x.csv", ts, lines, GAP_S)
    assert f.frames == 2 and f.last_source_line == 4


def test_quantiles_are_monotonic_and_bounded_by_max():
    r = assemble([_file("x.csv", np.r_[_ms_frames(0, 20), _ms_frames(30, 20)])], GAP_S)
    q = r.quantiles_s
    values = [q[k] for k in ("p50", "p90", "p99", "p99.9", "p99.99", "max")]
    assert values == sorted(values)
    assert q["max"] == pytest.approx(r.stats["max_gap_s"])


def test_largest_gaps_are_reported_with_context():
    r = assemble([_file("a.csv", np.r_[_ms_frames(0, 5), _ms_frames(7, 5)])], GAP_S)
    top = r.largest[0]
    assert top.gap_s == pytest.approx(2.0, abs=0.01) and top.kind == "within_file"
    assert len(r.sessions) == 1  # 2 s < 3 s threshold: reported, not split
