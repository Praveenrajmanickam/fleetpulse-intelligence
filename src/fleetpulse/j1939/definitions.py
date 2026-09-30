"""Verified signal definitions.

Every SignalSpec here has been checked against an authoritative document kept
in ``docs/references/``. The primary reference is the official FMS-Standard
interface description, which is the interface the Turku dataset was logged
from (dataset DOI 10.23729/3160254e-85e9-4268-a636-5b3e54091706)::

    FMS-Standard Interface description Vers. 02.00, 11.11.2010
    docs/references/fms_document_ver02.00vers_11_11_2010.pdf
    sha256 e3de1b50c5da9ec94ca7e75982a99e965b7a93b510c9cc4a877cde774bcb69f6
    downloaded from http://www.fms-standard.com/down_load/

The FMS document states that SAE J1939 prevails if the two disagree, and that
unavailable data is sent as "not available according to SAE". Error /
not-available classification therefore follows the SAE J1939-71 range
conventions implemented in :mod:`fleetpulse.j1939.signals`.

Do not add a signal here without a document reference you have read.
"""

from __future__ import annotations

from dataclasses import dataclass

from fleetpulse.j1939.signals import SignalKind, SignalSpec

FMS_V02 = "FMS-Standard v02.00 (11.11.2010)"

EEC1_ENGINE_SPEED = SignalSpec(
    name="engine_speed_rpm",
    spn=190,
    pgn=61444,
    start_byte=4,
    length_bits=16,
    resolution=0.125,
    offset=0.0,
    unit="rpm",
    min_value=0.0,
    max_value=8031.875,
    reference=f"{FMS_V02} §1.5 p.10 EEC1; SAE J1939-71 SPN 190",
    description="Actual engine speed, calculated over a minimum crankshaft angle of "
    "720 degrees divided by the number of cylinders.",
)

EEC2_ACCELERATOR_PEDAL = SignalSpec(
    name="accelerator_pedal_pct",
    spn=91,
    pgn=61443,
    start_byte=2,
    length_bits=8,
    resolution=0.4,
    offset=0.0,
    unit="%",
    min_value=0.0,
    max_value=100.0,
    reference=f"{FMS_V02} §1.2 p.7 EEC2; SAE J1939-71 SPN 91",
    description="Accelerator pedal position 1: actual position relative to the "
    "maximum position of the primary accelerator control.",
)

EEC2_ENGINE_LOAD = SignalSpec(
    name="engine_load_pct",
    spn=92,
    pgn=61443,
    start_byte=3,
    length_bits=8,
    resolution=1.0,
    offset=0.0,
    unit="%",
    min_value=0.0,
    max_value=125.0,  # FMS: "0 to 125 % op. range"
    reference=f"{FMS_V02} §1.2 p.7 EEC2; SAE J1939-71 SPN 92",
    description="Engine percent load at current speed: actual engine percent torque "
    "(indicated) relative to maximum indicated torque available at the current "
    "engine speed, clipped to zero torque during engine braking.",
)

CCVS_WHEEL_SPEED = SignalSpec(
    name="wheel_speed_kph",
    spn=84,
    pgn=65265,
    start_byte=2,
    length_bits=16,
    resolution=1 / 256,
    offset=0.0,
    unit="km/h",
    min_value=0.0,
    max_value=0xFAFF / 256,  # top of the J1939 2-byte valid range
    reference=f"{FMS_V02} §1.1 p.5 CCVS; SAE J1939-71 SPN 84",
    description="Wheel-based vehicle speed, calculated from wheel or tailshaft speed. "
    "FMS note: may differ from tachograph speed.",
)

_SWITCH_STATES = {0: "pedal released", 1: "pedal depressed"}

CCVS_CRUISE_CONTROL_ACTIVE = SignalSpec(
    name="cruise_control_active",
    spn=595,
    pgn=65265,
    start_byte=4,
    start_bit=1,
    length_bits=2,
    kind=SignalKind.DISCRETE,
    states={0: "switched off", 1: "switched on"},
    resolution=1.0,
    offset=0.0,
    unit="state",
    min_value=0,
    max_value=1,
    reference=f"{FMS_V02} §1.1 p.5 CCVS; SAE J1939-71 SPN 595",
    description="Cruise control is switched on. Does not guarantee the engine is "
    "being controlled by cruise control (driver demand may exceed it).",
)

CCVS_BRAKE_SWITCH = SignalSpec(
    name="brake_switch",
    spn=597,
    pgn=65265,
    start_byte=4,
    start_bit=5,
    length_bits=2,
    kind=SignalKind.DISCRETE,
    states=_SWITCH_STATES,
    resolution=1.0,
    offset=0.0,
    unit="state",
    min_value=0,
    max_value=1,
    reference=f"{FMS_V02} §1.1 p.5 CCVS; SAE J1939-71 SPN 597",
    description="Driver-operated service brake foot pedal is pressed. Does not "
    "cover park brake, retarder or engine brake.",
)

CCVS_CLUTCH_SWITCH = SignalSpec(
    name="clutch_switch",
    spn=598,
    pgn=65265,
    start_byte=4,
    start_bit=7,
    length_bits=2,
    kind=SignalKind.DISCRETE,
    states=_SWITCH_STATES,
    resolution=1.0,
    offset=0.0,
    unit="state",
    min_value=0,
    max_value=1,
    reference=f"{FMS_V02} §1.1 p.5 CCVS + p.30 overview; SAE J1939-71 SPN 598",
    description="Clutch pedal is pressed. FMS overview: trucks with automatic gear "
    "send this as not available.",
)

LFE_FUEL_RATE = SignalSpec(
    name="fuel_rate_lph",
    spn=183,
    pgn=65266,
    start_byte=1,
    length_bits=16,
    resolution=0.05,
    offset=0.0,
    unit="L/h",
    min_value=0.0,
    max_value=3212.75,
    reference=f"{FMS_V02} §1.16 p.24 LFE; SAE J1939-71 SPN 183",
    description="Amount of fuel consumed by the engine per unit of time. FMS: calculated "
    "values given as indications, not as contractual values.",
)

HRLFC_TOTAL_FUEL = SignalSpec(
    name="total_fuel_used_l",
    spn=5054,
    pgn=64777,
    start_byte=5,
    length_bits=32,
    resolution=0.001,
    offset=0.0,
    unit="L",
    min_value=0.0,
    max_value=4_211_081.215,
    reference=f"{FMS_V02} §1.18 p.26 HRLFC; SAE J1939-71 SPN 5054",
    description="High resolution engine total fuel used: accumulated fuel used during "
    "vehicle operation. FMS: implemented if technically possible.",
)

VDHR_TOTAL_DISTANCE = SignalSpec(
    name="total_distance_m",
    spn=917,
    pgn=65217,
    start_byte=1,
    length_bits=32,
    resolution=5.0,
    offset=0.0,
    unit="m",
    min_value=0.0,
    max_value=21_055_406_000.0,  # FMS: 0 - 21 055 406 km
    reference=f"{FMS_V02} §1.10 p.17 VDHR; SAE J1939-71 SPN 917",
    description="High resolution total vehicle distance: accumulated distance travelled "
    "by the vehicle during its operation.",
)

ET1_COOLANT_TEMP = SignalSpec(
    name="coolant_temp_c",
    spn=110,
    pgn=65262,
    start_byte=1,
    length_bits=8,
    resolution=1.0,
    offset=-40.0,
    unit="degC",
    min_value=-40.0,
    max_value=210.0,  # FMS overview: -40 to 210
    reference=f"{FMS_V02} §1.13 p.21 ET1; SAE J1939-71 SPN 110",
    description="Engine coolant temperature: temperature of liquid found in the engine "
    "cooling system.",
)

AMB_AMBIENT_TEMP = SignalSpec(
    name="ambient_temp_c",
    spn=171,
    pgn=65269,
    start_byte=4,
    length_bits=16,
    resolution=0.03125,
    offset=-273.0,
    unit="degC",
    min_value=-273.0,
    max_value=0xFAFF * 0.03125 - 273.0,  # top of the J1939 2-byte valid range
    reference=f"{FMS_V02} §1.14 p.22 AMB; SAE J1939-71 SPN 171",
    description="Ambient air temperature: temperature of air surrounding the vehicle.",
)


@dataclass(frozen=True)
class MessageSpec:
    pgn: int
    acronym: str
    nominal_period_ms: float  # FMS-Standard repetition rate
    signals: tuple[SignalSpec, ...]

    @property
    def key(self) -> str:
        return self.acronym.lower()


MESSAGES: tuple[MessageSpec, ...] = (
    MessageSpec(61444, "EEC1", 20, (EEC1_ENGINE_SPEED,)),
    MessageSpec(61443, "EEC2", 50, (EEC2_ACCELERATOR_PEDAL, EEC2_ENGINE_LOAD)),
    MessageSpec(
        65265,
        "CCVS",
        100,
        (
            CCVS_WHEEL_SPEED,
            CCVS_CRUISE_CONTROL_ACTIVE,
            CCVS_BRAKE_SWITCH,
            CCVS_CLUTCH_SWITCH,
        ),
    ),
    MessageSpec(65266, "LFE", 100, (LFE_FUEL_RATE,)),
    MessageSpec(64777, "HRLFC", 1000, (HRLFC_TOTAL_FUEL,)),
    MessageSpec(65217, "VDHR", 1000, (VDHR_TOTAL_DISTANCE,)),
    MessageSpec(65262, "ET1", 1000, (ET1_COOLANT_TEMP,)),
    MessageSpec(65269, "AMB", 1000, (AMB_AMBIENT_TEMP,)),
)

SIGNALS: tuple[SignalSpec, ...] = tuple(s for m in MESSAGES for s in m.signals)

for _m in MESSAGES:
    assert all(s.pgn == _m.pgn for s in _m.signals), _m.acronym
