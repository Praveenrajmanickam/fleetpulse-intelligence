"""29-bit SAE J1939 CAN identifier parsing (J1939-21).

Bit layout of the 29-bit extended identifier (bit 28 = MSB):

    28..26  priority (3 bits)
    25      extended data page (EDP / "reserved")
    24      data page (DP)
    23..16  PDU format (PF)
    15..8   PDU specific (PS)  -> destination address if PF < 240 (PDU1)
                                -> group extension   if PF >= 240 (PDU2)
    7..0    source address (SA)

PGN = EDP<<17 | DP<<16 | PF<<8 | (PS if PDU2 else 0)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MAX_29BIT_ID = 0x1FFFFFFF
PDU2_THRESHOLD = 240
GLOBAL_ADDRESS = 0xFF


@dataclass(frozen=True)
class J1939Id:
    can_id: int
    priority: int
    extended_data_page: int
    data_page: int
    pdu_format: int
    pdu_specific: int
    source_address: int
    pgn: int
    is_pdu2: bool
    destination_address: int | None  # only meaningful for PDU1


def _fields(can_id):
    """Bit extraction shared by the scalar and vectorised parsers."""
    priority = (can_id >> 26) & 0x7
    edp = (can_id >> 25) & 0x1
    dp = (can_id >> 24) & 0x1
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    sa = can_id & 0xFF
    return priority, edp, dp, pf, ps, sa


def parse_can_id(can_id: int) -> J1939Id:
    """Parse a single 29-bit identifier."""
    if not 0 <= can_id <= MAX_29BIT_ID:
        raise ValueError(f"CAN ID 0x{can_id:X} is not a valid 29-bit identifier")
    priority, edp, dp, pf, ps, sa = _fields(can_id)
    is_pdu2 = pf >= PDU2_THRESHOLD
    pgn = (edp << 17) | (dp << 16) | (pf << 8) | (ps if is_pdu2 else 0)
    return J1939Id(
        can_id=can_id,
        priority=priority,
        extended_data_page=edp,
        data_page=dp,
        pdu_format=pf,
        pdu_specific=ps,
        source_address=sa,
        pgn=pgn,
        is_pdu2=is_pdu2,
        destination_address=None if is_pdu2 else ps,
    )


def parse_can_ids(can_ids: pd.Series) -> pd.DataFrame:
    """Vectorised equivalent of :func:`parse_can_id` for a Series of ints.

    Raises ValueError if any identifier exceeds 29 bits.
    """
    ids = can_ids.to_numpy(dtype=np.int64)
    bad = (ids < 0) | (ids > MAX_29BIT_ID)
    if bad.any():
        raise ValueError(f"{int(bad.sum())} CAN IDs are not valid 29-bit identifiers")
    priority, edp, dp, pf, ps, sa = _fields(ids)
    is_pdu2 = pf >= PDU2_THRESHOLD
    pgn = (edp << 17) | (dp << 16) | (pf << 8) | np.where(is_pdu2, ps, 0)
    dest = pd.array(np.where(is_pdu2, 0, ps), dtype="UInt8")
    dest[is_pdu2] = pd.NA
    return pd.DataFrame(
        {
            "priority": priority.astype("uint8"),
            "extended_data_page": edp.astype("uint8"),
            "data_page": dp.astype("uint8"),
            "pdu_format": pf.astype("uint8"),
            "pdu_specific": ps.astype("uint8"),
            "source_address": sa.astype("uint8"),
            "pgn": pgn.astype("uint32"),
            "is_pdu2": is_pdu2,
            "destination_address": dest,
        },
        index=can_ids.index,
    )
