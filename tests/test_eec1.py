import math

import pandas as pd
import pytest

from fleetpulse.j1939.definitions import EEC1_ENGINE_SPEED
from fleetpulse.j1939.signals import SignalStatus, decode_signal

BYTES = [f"b{i}" for i in range(8)]


def _frames(*payloads):
    rows = [list(p) + [None] * (8 - len(p)) for p in payloads]
    return pd.DataFrame(rows, columns=BYTES).astype("UInt8")


def test_manually_verified_frame():
    # 2021-01-19 10:12:59.220499;0xcf004e6;8;240;125;131;194;18;255;255;255
    out = decode_signal(_frames([240, 125, 131, 194, 18, 255, 255, 255]), EEC1_ENGINE_SPEED)
    assert out.loc[0, "engine_speed_rpm_raw"] == 0x12C2 == 4802
    assert out.loc[0, "engine_speed_rpm"] == pytest.approx(600.25)
    assert out.loc[0, "engine_speed_rpm_status"] == SignalStatus.VALID.value


def test_spec_matches_j1939_71():
    s = EEC1_ENGINE_SPEED
    assert (s.pgn, s.spn, s.start_byte, s.length_bits) == (61444, 190, 4, 16)
    assert s.resolution == 0.125
    assert s.byte_columns == ["b3", "b4"]


@pytest.mark.parametrize(
    "lo,hi,status,rpm",
    [
        (0x00, 0x00, SignalStatus.VALID, 0.0),
        (0xFF, 0xFA, SignalStatus.VALID, 8031.875),  # max valid 0xFAFF
        (0x00, 0xFB, SignalStatus.RESERVED, None),
        (0x00, 0xFE, SignalStatus.ERROR, None),
        (0xFF, 0xFF, SignalStatus.NOT_AVAILABLE, None),
    ],
)
def test_j1939_range_conventions(lo, hi, status, rpm):
    out = decode_signal(_frames([0, 0, 0, lo, hi, 0, 0, 0]), EEC1_ENGINE_SPEED)
    assert out.loc[0, "engine_speed_rpm_status"] == status.value
    if rpm is None:
        assert math.isnan(out.loc[0, "engine_speed_rpm"])
    else:
        assert out.loc[0, "engine_speed_rpm"] == pytest.approx(rpm)


def test_missing_bytes():
    out = decode_signal(_frames([240, 125, 131, 194]), EEC1_ENGINE_SPEED)
    assert out.loc[0, "engine_speed_rpm_status"] == SignalStatus.MISSING_BYTES.value
    assert pd.isna(out.loc[0, "engine_speed_rpm_raw"])
    assert math.isnan(out.loc[0, "engine_speed_rpm"])
