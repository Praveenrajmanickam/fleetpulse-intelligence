"""Reader for the University of Turku raw CAN CSV logs.

File format (semicolon separated)::

    timestamp;id;dlc;data
    2021-01-19 10:12:59.220499;0xcf004e6;8;240;125;131;194;18;255;255;255

The header declares a single ``data`` column, but every data byte is its own
field, so rows have 3 + DLC fields. We therefore ignore the header names,
check the header text, and read up to 8 byte columns (b0..b7). Short frames
leave trailing byte columns empty (<NA>).

The source file is only ever opened for reading.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

EXPECTED_HEADER = "timestamp;id;dlc;data"
MAX_DLC = 8
BYTE_COLUMNS = [f"b{i}" for i in range(MAX_DLC)]
RAW_COLUMNS = ["timestamp", "id", "dlc", *BYTE_COLUMNS]
OVERFLOW_COLUMN = "_overflow"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
# The logger writes str(datetime), which omits ".%f" when microseconds == 0.
TIMESTAMP_FORMAT_WHOLE_SECOND = "%Y-%m-%d %H:%M:%S"


class RawFormatError(ValueError):
    """The raw file does not match the expected layout."""


def file_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def read_can_csv(path: str | Path) -> pd.DataFrame:
    """Read a raw CAN CSV into a typed DataFrame.

    Values that cannot be parsed become <NA>/NaT rather than raising, so the
    validation step can count and report them. Structural problems (wrong
    header, more than 8 data bytes) raise RawFormatError.

    Columns: source_line, timestamp, can_id_raw, can_id, dlc, n_bytes, b0..b7
    """
    path = Path(path)
    with path.open("r", encoding="ascii") as fh:
        header = fh.readline().strip()
    if header != EXPECTED_HEADER:
        raise RawFormatError(f"{path}: unexpected header {header!r}")

    # pandas silently truncates (index_col=False) or shifts into the index
    # rows with more fields than names, so read one sentinel column and
    # fail if anything lands in it.
    raw = pd.read_csv(
        path,
        sep=";",
        header=None,
        skiprows=1,
        names=[*RAW_COLUMNS, OVERFLOW_COLUMN],
        index_col=False,
        dtype=str,
        keep_default_na=False,
        na_values=[""],
        engine="c",
    )
    overflow = raw[OVERFLOW_COLUMN].notna()
    if overflow.any():
        first_line = int(overflow.idxmax()) + 2
        raise RawFormatError(
            f"{path}: {int(overflow.sum())} rows with more than {MAX_DLC} data bytes "
            f"(first at line {first_line})"
        )

    log.info("Read %d rows from %s", len(raw), path)

    df = pd.DataFrame(index=raw.index)
    df["source_line"] = (raw.index + 2).astype("int64")  # 1-based, after header
    df["timestamp"] = _parse_timestamps(raw["timestamp"])
    df["can_id_raw"] = raw["id"]
    df["can_id"] = raw["id"].map(_parse_hex).astype("Int64")
    df["dlc"] = pd.to_numeric(raw["dlc"], errors="coerce").astype("Int8")
    for col in BYTE_COLUMNS:
        # Int16 so out-of-range values (e.g. 256) survive for validation.
        df[col] = pd.to_numeric(raw[col], errors="coerce").astype("Int16")
    df["n_bytes"] = raw[BYTE_COLUMNS].notna().sum(axis=1).astype("int8")
    return df


def read_can_timestamps(source, name: str | None = None) -> pd.DataFrame:
    """Read only the timestamp of every frame (fast path for gap inspection).

    ``source`` is a path or a binary file object (e.g. a tar member). Returns
    ``source_line`` (1-based line in the file) and ``timestamp`` (NaT where
    unparseable), in logged order.
    """
    import io

    if isinstance(source, (str, Path)):
        name = name or str(source)
        data = Path(source).read_bytes()
    else:
        data = source.read()
    text = data.decode("ascii")
    header, _, body = text.partition("\n")
    if header.strip() != EXPECTED_HEADER:
        raise RawFormatError(f"{name}: unexpected header {header.strip()!r}")
    first = pd.read_csv(
        io.StringIO(body), sep="\x1f", header=None, names=["line"], dtype=str,
        keep_default_na=False, engine="c",
    )["line"].str.split(";", n=1).str[0].str.strip()
    return pd.DataFrame(
        {"source_line": np.arange(2, len(first) + 2, dtype=np.int64), "timestamp": _parse_timestamps(first)}
    )


def _parse_timestamps(values: pd.Series) -> pd.Series:
    ts = pd.to_datetime(values, format=TIMESTAMP_FORMAT, errors="coerce")
    retry = ts.isna() & values.notna()
    if retry.any():
        ts[retry] = pd.to_datetime(values[retry], format=TIMESTAMP_FORMAT_WHOLE_SECOND, errors="coerce")
        log.info("%d timestamps on an exact second (no fractional part)", int(ts[retry].notna().sum()))
    return ts


def _parse_hex(value: str) -> int | None:
    try:
        return int(value, 16)
    except (TypeError, ValueError):
        return None
