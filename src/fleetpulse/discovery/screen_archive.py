"""Light per-file driving screen over the raw archive, to choose analysis windows.

Streams the tar(.xz) once, one member in memory at a time, single core. For
each file it keeps only the frames of three verified messages (EEC1, CCVS,
LFE, selected by the CAN IDs observed in part_3) and decodes them with the
validated reference decoder (``fleetpulse.j1939.signals.decode_signal`` and
the project's SignalSpecs); no new decoding logic. Output: one summary row
per file. This screen only *selects* windows; analysis uses Bronze/Silver.

    nice -n 19 python -m fleetpulse.discovery.screen_archive \\
        --archive data/raw/part_3.tar.xz --out data/discovery/file_screen.csv
"""

from __future__ import annotations

import argparse
import sys
import tarfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

from fleetpulse.ingestion.can_csv import _parse_timestamps
from fleetpulse.j1939.definitions import CCVS_BRAKE_SWITCH, CCVS_WHEEL_SPEED, EEC1_ENGINE_SPEED, LFE_FUEL_RATE
from fleetpulse.j1939.signals import decode_signal

# CAN IDs carrying the screened messages in part_3 (all from source address 0xE6).
IDS = {b"0xcf004e6": "EEC1", b"0x18fef1e6": "CCVS", b"0x18fef2e6": "LFE"}
BYTES = [f"b{i}" for i in range(8)]


def _frames(lines: list[bytes]) -> pd.DataFrame:
    rows = [ln.rstrip(b"\r").decode().split(";") for ln in lines]
    rows = [r for r in rows if len(r) == 11 and r[2] == "8"]
    df = pd.DataFrame(rows, columns=["ts", "id", "dlc", *BYTES])
    out = pd.DataFrame({"timestamp": _parse_timestamps(df["ts"])})
    for b in BYTES:
        out[b] = pd.to_numeric(df[b], errors="coerce").astype("UInt8")
    return out.dropna(subset=["timestamp"]).sort_values("timestamp", kind="stable").reset_index(drop=True)


def _hold_seconds(ts: pd.Series, max_gap_s: float) -> np.ndarray:
    """Seconds each sample stands for (until the next one, capped)."""
    dt = ts.diff().shift(-1).dt.total_seconds().fillna(0).to_numpy()
    return np.clip(dt, 0, max_gap_s)


def screen_member(name: str, data: bytes) -> dict:
    groups: dict[str, list[bytes]] = {k: [] for k in IDS.values()}
    for ln in data.split(b"\n")[1:]:
        f = ln.split(b";", 2)
        if len(f) > 2 and f[1] in IDS:
            groups[IDS[f[1]]].append(ln)
    row = {"file": name}
    c = _frames(groups["CCVS"])
    if len(c):
        sp = decode_signal(c, CCVS_WHEEL_SPEED)["wheel_speed_kph"].astype(float)
        br = decode_signal(c, CCVS_BRAKE_SWITCH)["brake_switch"].astype("float")
        h = _hold_seconds(c["timestamp"], 0.3)
        moving = (sp >= 0.5).to_numpy()
        row.update(
            first=c["timestamp"].iloc[0], last=c["timestamp"].iloc[-1], ccvs_frames=len(c),
            moving_s=float(h[moving].sum()), driving_s=float(h[(sp >= 20).to_numpy()].sum()),
            mean_speed=float(sp.mean()), max_speed=float(sp.max()),
            distance_km=float(np.nansum(sp.to_numpy() * h) / 3600),
            brake_s=float(h[(br == 1).to_numpy()].sum()),
        )
    e = _frames(groups["EEC1"])
    if len(e):
        rpm = decode_signal(e, EEC1_ENGINE_SPEED)["engine_speed_rpm"].astype(float)
        h = _hold_seconds(e["timestamp"], 0.06)
        row.update(engine_on_s=float(h[(rpm >= 300).to_numpy()].sum()), max_rpm=float(rpm.max()),
                   mean_rpm_running=float(rpm[rpm >= 300].mean()) if (rpm >= 300).any() else np.nan)
    lf = _frames(groups["LFE"])
    if len(lf):
        fr = decode_signal(lf, LFE_FUEL_RATE)["fuel_rate_lph"].astype(float)
        h = _hold_seconds(lf["timestamp"], 0.3)
        row.update(fuel_l=float(np.nansum(fr.to_numpy() * h) / 3600))
        if len(c):
            # Attribute each fuel-rate sample to the latest speed sample at or before it (<= 300 ms old).
            spd = pd.DataFrame({"timestamp": c["timestamp"], "speed": sp.to_numpy()})
            m = pd.merge_asof(pd.DataFrame({"timestamp": lf["timestamp"], "fr": fr.to_numpy(), "h": h}), spd,
                              on="timestamp", direction="backward", tolerance=pd.Timedelta("300ms"))
            stat = (m["speed"] < 0.5).to_numpy()
            row.update(fuel_stationary_l=float(np.nansum(m["fr"].to_numpy()[stat] * m["h"].to_numpy()[stat]) / 3600))
    if len(e) and len(c):
        spd = pd.DataFrame({"timestamp": c["timestamp"], "speed": sp.to_numpy()})
        m = pd.merge_asof(pd.DataFrame({"timestamp": e["timestamp"], "rpm": rpm.to_numpy(),
                                        "h": _hold_seconds(e["timestamp"], 0.06)}),
                          spd, on="timestamp", direction="backward", tolerance=pd.Timedelta("300ms"))
        so = ((m["rpm"] >= 300) & (m["speed"] < 0.5)).to_numpy()
        row.update(engine_on_stationary_s=float(m["h"].to_numpy()[so].sum()))
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--archive", type=Path, default=Path("data/raw/part_3.tar.xz"))
    parser.add_argument("--out", type=Path, default=Path("data/discovery/file_screen.csv"))
    args = parser.parse_args(argv)
    t0, rows = time.perf_counter(), []
    with tarfile.open(args.archive, "r:*") as tar:
        for info in tar:
            if info.isfile() and info.name.endswith(".csv"):
                rows.append(screen_member(info.name, tar.extractfile(info).read()))
                if len(rows) % 200 == 0:
                    print(f"{len(rows)} files, {time.perf_counter() - t0:.0f} s", flush=True)
    df = pd.DataFrame(rows).sort_values("first").reset_index(drop=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"{len(df)} files screened in {time.perf_counter() - t0:.0f} s -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
