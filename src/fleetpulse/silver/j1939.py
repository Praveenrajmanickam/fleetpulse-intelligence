"""J1939 identifier fields and FMS signal decoding as native Spark column expressions.

Mirrors the reference implementation exactly:

* identifier layout: fleetpulse.j1939.identifier (J1939-21)
* raw extraction, status classification and scaling: fleetpulse.j1939.signals
  (``_extract_raw`` / ``_classify`` / ``decode_signal``)

Bronze payload columns are 1-indexed (``byte_1`` .. ``byte_8``) like the
J1939-71 / FMS byte numbering used in SignalSpec.start_byte.
"""

from __future__ import annotations

from functools import reduce

from pyspark.sql import Column
from pyspark.sql import functions as F

from fleetpulse.j1939.identifier import PDU2_THRESHOLD
from fleetpulse.j1939.signals import _MEASURED_RANGES, SignalKind, SignalSpec, SignalStatus


# --- identifier ----------------------------------------------------------------
def _bits(col: Column, shift: int, mask: int) -> Column:
    return F.shiftright(col, shift).bitwiseAND(F.lit(mask))


def priority(can_id: Column) -> Column:
    return _bits(can_id, 26, 0x7)


def extended_data_page(can_id: Column) -> Column:
    return _bits(can_id, 25, 0x1)


def data_page(can_id: Column) -> Column:
    return _bits(can_id, 24, 0x1)


def pdu_format(can_id: Column) -> Column:
    return _bits(can_id, 16, 0xFF)


def pdu_specific(can_id: Column) -> Column:
    return _bits(can_id, 8, 0xFF)


def source_address(can_id: Column) -> Column:
    return can_id.bitwiseAND(F.lit(0xFF))


def pgn(can_id: Column) -> Column:
    """PGN = EDP<<17 | DP<<16 | PF<<8 | (PS if PDU2 (PF >= 240) else 0)."""
    pf = pdu_format(can_id)
    group_extension = F.when(pf >= PDU2_THRESHOLD, pdu_specific(can_id)).otherwise(F.lit(0))
    return (
        F.shiftleft(extended_data_page(can_id), 17)
        .bitwiseOR(F.shiftleft(data_page(can_id), 16))
        .bitwiseOR(F.shiftleft(pf, 8))
        .bitwiseOR(group_extension)
    )


# --- signals -------------------------------------------------------------------
def byte_columns(spec: SignalSpec) -> list[str]:
    """Bronze column names (1-indexed) a signal occupies, LSB first."""
    n = max(1, spec.length_bits // 8)
    return [f"byte_{spec.start_byte + i}" for i in range(n)]


def raw_value(spec: SignalSpec) -> Column:
    """Little-endian integer of the signal's bytes; bit field for discrete signals."""
    cols = byte_columns(spec)
    raw = F.lit(0).cast("long")
    for i, c in enumerate(cols):
        raw = raw.bitwiseOR(F.shiftleft(F.coalesce(F.col(c).cast("long"), F.lit(0).cast("long")), 8 * i))
    if spec.kind is SignalKind.DISCRETE:
        raw = F.shiftright(raw, spec.start_bit - 1).bitwiseAND(F.lit((1 << spec.length_bits) - 1))
    return raw


def missing_bytes(spec: SignalSpec) -> Column:
    return reduce(lambda a, b: a | b, [F.col(c).isNull() for c in byte_columns(spec)])


def status(spec: SignalSpec, raw: Column, missing: Column) -> Column:
    """Same decision order as reference ``_classify``."""
    s = SignalStatus
    if spec.kind is SignalKind.DISCRETE:
        return (
            F.when(missing, s.MISSING_BYTES.value)
            .when(raw <= 1, s.VALID.value)
            .when(raw == 2, s.ERROR.value)
            .otherwise(s.NOT_AVAILABLE.value)
        )
    max_valid, error_lo, na_lo = _MEASURED_RANGES[spec.length_bits]
    physical = raw * F.lit(spec.resolution) + F.lit(spec.offset)
    in_op_range = (physical >= F.lit(spec.min_value)) & (physical <= F.lit(spec.max_value))
    return (
        F.when(missing, s.MISSING_BYTES.value)
        .when((raw <= max_valid) & in_op_range, s.VALID.value)
        .when(raw <= max_valid, s.OUT_OF_RANGE.value)
        .when(raw >= na_lo, s.NOT_AVAILABLE.value)
        .when(raw >= error_lo, s.ERROR.value)
        .otherwise(s.RESERVED.value)
    )


def physical_value(spec: SignalSpec, raw: Column, status_col: Column) -> Column:
    """Physical value only when status is valid; never a substitute zero.

    Discrete signals carry their documented state code (0/1) as the value.
    """
    valid = status_col == SignalStatus.VALID.value
    if spec.kind is SignalKind.DISCRETE:
        return F.when(valid, raw.cast("double"))
    return F.when(valid, raw * F.lit(spec.resolution) + F.lit(spec.offset))


def signal_struct(spec: SignalSpec, message_key: str) -> Column:
    raw = raw_value(spec)
    miss = missing_bytes(spec)
    st = status(spec, raw, miss)
    return F.struct(
        F.lit(message_key.upper()).alias("message"),
        F.lit(spec.spn).alias("spn"),
        F.lit(spec.name).alias("signal_name"),
        F.when(~miss, raw).alias("signal_raw"),
        physical_value(spec, raw, st).alias("signal_value"),
        F.lit(spec.unit).alias("signal_unit"),
        st.alias("signal_status"),
    )
