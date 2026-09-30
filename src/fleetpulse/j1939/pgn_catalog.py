"""PGN catalogue: classification by J1939-21 range plus a deliberately small
table of well-known, documented PGN names.

Policy
------
* A PGN only gets a name here if it is a widely documented SAE J1939-71 /
  J1939-21 / FMS-Standard message. Every name should be verified against the
  SAE J1939 Digital Annex before a signal from it is decoded.
* Anything in the Proprietary A / A2 / B ranges is manufacturer-defined. We
  never attach a meaning to those.
* A PGN in the standard range that is not in this table is reported as
  ``standard_unmapped``: it is *not* proprietary, but we have not verified
  its definition, so it is treated as unknown.
"""

from __future__ import annotations

from enum import Enum


class PgnCategory(str, Enum):
    STANDARD_DOCUMENTED = "standard_documented"
    STANDARD_UNMAPPED = "standard_unmapped"
    PROPRIETARY_A = "proprietary_a"
    PROPRIETARY_A2 = "proprietary_a2"
    PROPRIETARY_B = "proprietary_b"
    RESERVED_EDP = "reserved_edp"


PROPRIETARY_A_PGN = 61184  # 0x0EF00, PDU1, DP=0
PROPRIETARY_A2_PGN = 126720  # 0x1EF00, PDU1, DP=1
PROPRIETARY_B_RANGES = (
    (65280, 65535),  # 0x0FF00-0x0FFFF, DP=0
    (130816, 131071),  # 0x1FF00-0x1FFFF, DP=1
)

# pgn -> (acronym, name, reference)
DOCUMENTED_PGNS: dict[int, tuple[str, str, str]] = {
    # Network / transport layer (J1939-21, J1939-81)
    59392: ("ACKM", "Acknowledgement", "J1939-21"),
    59904: ("RQST", "Request", "J1939-21"),
    60160: ("TP.DT", "Transport Protocol - Data Transfer", "J1939-21"),
    60416: ("TP.CM", "Transport Protocol - Connection Management", "J1939-21"),
    60928: ("AC", "Address Claimed", "J1939-81"),
    # Application layer (J1939-71 / FMS-Standard)
    61441: ("EBC1", "Electronic Brake Controller 1", "J1939-71"),
    61442: ("ETC1", "Electronic Transmission Controller 1", "J1939-71"),
    61443: ("EEC2", "Electronic Engine Controller 2", "J1939-71"),
    61444: ("EEC1", "Electronic Engine Controller 1", "J1939-71 / FMS"),
    61445: ("ETC2", "Electronic Transmission Controller 2", "J1939-71"),
    61449: ("VDC2", "Vehicle Dynamic Stability Control 2", "J1939-71"),
    64777: ("HRLFC", "High Resolution Fuel Consumption (Liquid)", "J1939-71 / FMS"),
    65132: ("TCO1", "Tachograph", "J1939-71 / FMS"),
    65134: ("HRW", "High Resolution Wheel Speed", "J1939-71"),
    65198: ("AIR1", "Air Supply Pressure", "J1939-71 / FMS"),
    65217: ("VDHR", "High Resolution Vehicle Distance", "J1939-71 / FMS"),
    65253: ("HOURS", "Engine Hours, Revolutions", "J1939-71 / FMS"),
    65254: ("TD", "Time/Date", "J1939-71"),
    65262: ("ET1", "Engine Temperature 1", "J1939-71 / FMS"),
    65263: ("EFL/P1", "Engine Fluid Level/Pressure 1", "J1939-71"),
    65265: ("CCVS", "Cruise Control/Vehicle Speed", "J1939-71 / FMS"),
    65266: ("LFE", "Fuel Economy (Liquid)", "J1939-71 / FMS"),
    65269: ("AMB", "Ambient Conditions", "J1939-71 / FMS"),
    65272: ("TRF1", "Transmission Fluids 1", "J1939-71"),
    65276: ("DD", "Dash Display", "J1939-71 / FMS"),
}


def classify_pgn(pgn: int) -> PgnCategory:
    if pgn > 0x1FFFF:
        return PgnCategory.RESERVED_EDP
    if pgn in DOCUMENTED_PGNS:
        return PgnCategory.STANDARD_DOCUMENTED
    if pgn == PROPRIETARY_A_PGN:
        return PgnCategory.PROPRIETARY_A
    if pgn == PROPRIETARY_A2_PGN:
        return PgnCategory.PROPRIETARY_A2
    if any(lo <= pgn <= hi for lo, hi in PROPRIETARY_B_RANGES):
        return PgnCategory.PROPRIETARY_B
    return PgnCategory.STANDARD_UNMAPPED


def pgn_name(pgn: int) -> str:
    """Acronym for documented PGNs, otherwise an explicit 'unknown' label."""
    if pgn in DOCUMENTED_PGNS:
        return DOCUMENTED_PGNS[pgn][0]
    return f"UNKNOWN ({classify_pgn(pgn).value})"
