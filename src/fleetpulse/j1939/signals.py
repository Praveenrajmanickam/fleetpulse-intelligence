"""Generic J1939 signal (SPN) extraction.

Signal *definitions* live in :mod:`fleetpulse.j1939.definitions`; this module
only knows how to pull bits out of frames and classify raw values.

Bit numbering follows J1939-71 / FMS-Standard: bytes are 1-indexed, bits
within a byte are 1-indexed with bit 1 = least significant bit. Multi-byte
parameters are little-endian (first byte = least significant).

Raw-value classification (SAE J1939-71 parameter range conventions)::

    measured  1 byte  valid 0x00-0xFA     reserved 0xFB-0xFD     error 0xFE         n/a 0xFF
    measured  2 byte  valid 0x0000-0xFAFF reserved 0xFB00-0xFDFF error 0xFE00-0xFEFF n/a 0xFF00-0xFFFF
    measured  4 byte  valid <= 0xFAFFFFFF reserved 0xFB000000-0xFDFFFFFF
                      error 0xFE000000-0xFEFFFFFF n/a 0xFF000000-0xFFFFFFFF
    discrete  2 bit   valid 00, 01        error 10               not available 11

A measured value that is in the valid raw range but outside the documented
operating range of the signal is reported as ``out_of_range``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd


class SignalStatus(str, Enum):
    VALID = "valid"
    OUT_OF_RANGE = "out_of_range"
    RESERVED = "reserved"
    ERROR = "error"
    NOT_AVAILABLE = "not_available"
    MISSING_BYTES = "missing_bytes"


class SignalKind(str, Enum):
    MEASURED = "measured"
    DISCRETE = "discrete"


_MEASURED_RANGES = {
    # length_bits -> (max_valid, error_lo, not_available_lo)
    8: (0xFA, 0xFE, 0xFF),
    16: (0xFAFF, 0xFE00, 0xFF00),
    32: (0xFAFFFFFF, 0xFE000000, 0xFF000000),
}


@dataclass(frozen=True)
class SignalSpec:
    name: str
    spn: int
    pgn: int
    start_byte: int  # 1-indexed
    length_bits: int
    resolution: float
    offset: float
    unit: str
    min_value: float
    max_value: float
    reference: str
    start_bit: int = 1  # 1-indexed within start_byte, 1 = LSB
    kind: SignalKind = SignalKind.MEASURED
    states: dict[int, str] = field(default_factory=dict)  # discrete only
    description: str = ""

    def __post_init__(self):
        if self.kind is SignalKind.MEASURED:
            if self.start_bit != 1 or self.length_bits not in _MEASURED_RANGES:
                raise ValueError(f"{self.name}: only byte-aligned 8/16/32-bit measured signals supported")
        elif self.length_bits != 2 or not 1 <= self.start_bit <= 7:
            raise ValueError(f"{self.name}: only 2-bit discrete signals supported")

    @property
    def byte_columns(self) -> list[str]:
        """0-indexed byte columns (b0..b7) this signal occupies, LSB first."""
        first = self.start_byte - 1
        n_bytes = max(1, self.length_bits // 8)
        return [f"b{i}" for i in range(first, first + n_bytes)]

    @property
    def position(self) -> str:
        """Human-readable position, e.g. 'byte 4-5' or 'byte 4 bits 5-6'."""
        if self.kind is SignalKind.DISCRETE:
            return f"byte {self.start_byte} bits {self.start_bit}-{self.start_bit + self.length_bits - 1}"
        cols = self.byte_columns
        last = self.start_byte + len(cols) - 1
        return f"byte {self.start_byte}" if len(cols) == 1 else f"byte {self.start_byte}-{last}"


def _extract_raw(frames: pd.DataFrame, spec: SignalSpec) -> tuple[np.ndarray, np.ndarray]:
    cols = spec.byte_columns
    block = frames[cols]
    missing = block.isna().any(axis=1).to_numpy()
    raw = np.zeros(len(frames), dtype=np.int64)
    for i, col in enumerate(cols):
        raw |= block[col].fillna(0).to_numpy(dtype=np.int64) << (8 * i)
    if spec.kind is SignalKind.DISCRETE:
        raw = (raw >> (spec.start_bit - 1)) & ((1 << spec.length_bits) - 1)
    return raw, missing


def _classify(raw: np.ndarray, missing: np.ndarray, spec: SignalSpec) -> np.ndarray:
    if spec.kind is SignalKind.DISCRETE:
        conditions = [missing, raw <= 1, raw == 2]
        choices = [SignalStatus.MISSING_BYTES, SignalStatus.VALID, SignalStatus.ERROR]
        default = SignalStatus.NOT_AVAILABLE
    else:
        max_valid, error_lo, na_lo = _MEASURED_RANGES[spec.length_bits]
        physical = raw * spec.resolution + spec.offset
        in_op_range = (physical >= spec.min_value) & (physical <= spec.max_value)
        conditions = [
            missing,
            (raw <= max_valid) & in_op_range,
            raw <= max_valid,
            raw >= na_lo,
            raw >= error_lo,
        ]
        choices = [
            SignalStatus.MISSING_BYTES,
            SignalStatus.VALID,
            SignalStatus.OUT_OF_RANGE,
            SignalStatus.NOT_AVAILABLE,
            SignalStatus.ERROR,
        ]
        default = SignalStatus.RESERVED
    return np.select(conditions, [c.value for c in choices], default=default.value)


def decode_signal(frames: pd.DataFrame, spec: SignalSpec) -> pd.DataFrame:
    """Decode ``spec`` from frames with byte columns b0..b7 (nullable ints).

    The caller is responsible for filtering ``frames`` to the right PGN.
    Returns ``<name>_raw``, ``<name>`` and ``<name>_status``. The value column
    is only populated when status is ``valid``: float for measured signals,
    nullable Int8 (the documented state code) for discrete signals.
    """
    raw, missing = _extract_raw(frames, spec)
    status = _classify(raw, missing, spec)
    valid = status == SignalStatus.VALID.value

    if spec.kind is SignalKind.DISCRETE:
        value = pd.array(raw, dtype="Int8")
        value[~valid] = pd.NA
    else:
        value = np.where(valid, raw * spec.resolution + spec.offset, np.nan)

    raw_series = pd.array(raw, dtype="Int64")
    raw_series[missing] = pd.NA
    return pd.DataFrame(
        {
            f"{spec.name}_raw": raw_series,
            spec.name: value,
            f"{spec.name}_status": status,
        },
        index=frames.index,
    )
