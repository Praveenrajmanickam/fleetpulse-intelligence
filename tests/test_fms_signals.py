"""Verified FMS v02.00 signals, using real frames from the Turku session."""

import math

import pandas as pd
import pytest

from fleetpulse.j1939.definitions import (
    AMB_AMBIENT_TEMP,
    CCVS_BRAKE_SWITCH,
    CCVS_CLUTCH_SWITCH,
    CCVS_CRUISE_CONTROL_ACTIVE,
    CCVS_WHEEL_SPEED,
    EEC2_ACCELERATOR_PEDAL,
    EEC2_ENGINE_LOAD,
    ET1_COOLANT_TEMP,
    HRLFC_TOTAL_FUEL,
    LFE_FUEL_RATE,
    MESSAGES,
    SIGNALS,
    VDHR_TOTAL_DISTANCE,
)
from fleetpulse.j1939.signals import SignalKind, SignalSpec, SignalStatus, decode_signal

BYTES = [f"b{i}" for i in range(8)]

# 2021-01-19 10:07:05.593040;0x18fef1e6;8;243;76;47;196;0;255;224;255
CCVS_FRAME = [243, 76, 47, 196, 0, 255, 224, 255]
# 2021-01-19 10:07:05.510787;0xcf003e6;8;241;0;3;255;255;255;255;255
EEC2_FRAME = [241, 0, 3, 255, 255, 255, 255, 255]


def _frames(*payloads):
    return pd.DataFrame([list(p) for p in payloads], columns=BYTES).astype("UInt8")


def _decode(payload, spec):
    return decode_signal(_frames(payload), spec).iloc[0]


def test_ccvs_wheel_speed_real_frame():
    row = _decode(CCVS_FRAME, CCVS_WHEEL_SPEED)
    assert row["wheel_speed_kph_raw"] == 47 * 256 + 76
    assert row["wheel_speed_kph"] == pytest.approx((47 * 256 + 76) / 256)  # 47.297 km/h
    assert row["wheel_speed_kph_status"] == "valid"


def test_ccvs_byte4_bit_fields_real_frame():
    # 196 = 0b11_00_01_00: clutch n/a, brake released, (SPN 596 not decoded), cruise off
    assert _decode(CCVS_FRAME, CCVS_CRUISE_CONTROL_ACTIVE)["cruise_control_active"] == 0
    assert _decode(CCVS_FRAME, CCVS_BRAKE_SWITCH)["brake_switch"] == 0
    clutch = _decode(CCVS_FRAME, CCVS_CLUTCH_SWITCH)
    assert clutch["clutch_switch_status"] == "not_available"
    assert pd.isna(clutch["clutch_switch"])


@pytest.mark.parametrize(
    "byte4,brake,status",
    [
        (0b11_01_01_00, 1, "valid"),  # 212, seen while braking
        (0b11_00_01_00, 0, "valid"),
        (0b11_10_01_00, None, "error"),
        (0b11_11_01_00, None, "not_available"),
    ],
)
def test_brake_switch_two_bit_states(byte4, brake, status):
    frame = CCVS_FRAME.copy()
    frame[3] = byte4
    row = _decode(frame, CCVS_BRAKE_SWITCH)
    assert row["brake_switch_status"] == status
    assert (pd.isna(row["brake_switch"]) and brake is None) or row["brake_switch"] == brake


def test_eec2_real_frame():
    assert _decode(EEC2_FRAME, EEC2_ACCELERATOR_PEDAL)["accelerator_pedal_pct"] == 0.0
    assert _decode(EEC2_FRAME, EEC2_ENGINE_LOAD)["engine_load_pct"] == 3.0


def test_accelerator_pedal_scaling_and_na():
    frame = EEC2_FRAME.copy()
    frame[1] = 250  # 0xFA, top of valid range
    assert _decode(frame, EEC2_ACCELERATOR_PEDAL)["accelerator_pedal_pct"] == pytest.approx(100.0)
    frame[1] = 0xFF
    row = _decode(frame, EEC2_ACCELERATOR_PEDAL)
    assert row["accelerator_pedal_pct_status"] == "not_available"
    assert math.isnan(row["accelerator_pedal_pct"])


def test_engine_load_beyond_fms_operating_range():
    frame = EEC2_FRAME.copy()
    frame[2] = 126  # valid J1939 raw, but FMS op. range is 0-125 %
    row = _decode(frame, EEC2_ENGINE_LOAD)
    assert row["engine_load_pct_status"] == SignalStatus.OUT_OF_RANGE.value
    assert math.isnan(row["engine_load_pct"])


def test_positions_match_fms_document():
    positions = {s.spn: s.position for s in SIGNALS}
    assert positions == {
        190: "byte 4-5",
        91: "byte 2",
        92: "byte 3",
        84: "byte 2-3",
        595: "byte 4 bits 1-2",
        597: "byte 4 bits 5-6",
        598: "byte 4 bits 7-8",
        183: "byte 1-2",
        5054: "byte 5-8",
        917: "byte 1-4",
        110: "byte 1",
        171: "byte 4-5",
    }


def test_every_signal_cites_a_reference():
    for s in SIGNALS:
        assert "FMS-Standard v02.00" in s.reference, s.name


def test_message_rates_from_fms():
    assert {m.acronym: m.nominal_period_ms for m in MESSAGES} == {
        "EEC1": 20, "EEC2": 50, "CCVS": 100, "LFE": 100, "HRLFC": 1000, "VDHR": 1000, "ET1": 1000, "AMB": 1000,
    }


def test_unsupported_layouts_rejected():
    with pytest.raises(ValueError):
        SignalSpec("x", 1, 1, start_byte=1, start_bit=3, length_bits=8, resolution=1, offset=0,
                   unit="", min_value=0, max_value=1, reference="")
    with pytest.raises(ValueError):
        SignalSpec("x", 1, 1, start_byte=1, length_bits=4, kind=SignalKind.DISCRETE, resolution=1,
                   offset=0, unit="", min_value=0, max_value=1, reference="")


# Real frames, 20210119100705481168.csv
LFE_FRAME = [12, 0, 255, 255, 255, 255, 255, 255]  # 10:07:05.497585 0x18fef2e6
HRLFC_FRAME = [255, 255, 255, 255, 214, 202, 39, 0]  # 10:07:06.067087 0x18fd09e6
VDHR_FRAME = [176, 181, 26, 0, 178, 181, 26, 0]  # 10:07:06.117563 0x18fec1e6
ET1_FRAME = [120, 255, 98, 45, 255, 255, 255, 255]  # 10:07:05.703139 0x18feeee6
AMB_FRAME = [199, 255, 255, 22, 34, 255, 255, 255]  # 10:07:06.178070 0x18fef5e6


@pytest.mark.parametrize(
    "frame,spec,expected",
    [
        (LFE_FRAME, LFE_FUEL_RATE, 12 * 0.05),
        (HRLFC_FRAME, HRLFC_TOTAL_FUEL, 0x0027CAD6 * 0.001),  # 2607.830 L
        (VDHR_FRAME, VDHR_TOTAL_DISTANCE, 0x001AB5B0 * 5.0),  # 8752.24 km
        (ET1_FRAME, ET1_COOLANT_TEMP, 80.0),
        (AMB_FRAME, AMB_AMBIENT_TEMP, (34 * 256 + 22) * 0.03125 - 273),  # -0.3125 degC
    ],
)
def test_real_frames_new_signals(frame, spec, expected):
    row = _decode(frame, spec)
    assert row[f"{spec.name}_status"] == "valid"
    assert row[spec.name] == pytest.approx(expected)


def test_32_bit_not_available_and_error():
    na = HRLFC_FRAME[:4] + [0, 0, 0, 0xFF]
    assert _decode(na, HRLFC_TOTAL_FUEL)["total_fuel_used_l_status"] == "not_available"
    err = HRLFC_FRAME[:4] + [0, 0, 0, 0xFE]
    assert _decode(err, HRLFC_TOTAL_FUEL)["total_fuel_used_l_status"] == "error"


def test_negative_offset_temperatures():
    frame = ET1_FRAME.copy()
    frame[0] = 0
    assert _decode(frame, ET1_COOLANT_TEMP)["coolant_temp_c"] == -40.0
    frame[0] = 0xFF
    assert _decode(frame, ET1_COOLANT_TEMP)["coolant_temp_c_status"] == "not_available"
