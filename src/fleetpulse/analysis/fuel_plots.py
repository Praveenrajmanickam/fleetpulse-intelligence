"""Static figures for the fuel / stops / coasting analysis (same style as plots.py)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from fleetpulse.analysis.plots import (
    MOTION_COLORS,
    MOTION_ORDER,
    PRIMARY,
    SURFACE,
    TEXT,
    TEXT_2,
    _brake_spans,
    _save,
    _time_axis,
    plt,
)

SECOND = "#eb6834"  # categorical slot 2
SEQ = ["#fcfcfb", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def fuel_over_time(tel, fuel_counter: pd.DataFrame, path):
    dt = tel["timestamp"].diff().dt.total_seconds().median()
    fig, axes = plt.subplots(3, 1, figsize=(11, 7.5), sharex=True, gridspec_kw={"height_ratios": [1.3, 1.6, 1.3]})
    axes[0].plot(tel["timestamp"], tel["wheel_speed_kph"], color=TEXT_2, lw=1.2)
    axes[0].set_title("Wheel-based speed (SPN 84)")
    axes[0].set_ylabel("km/h")
    _brake_spans(axes[1], tel["timestamp"], tel["brake_switch"])
    axes[1].plot(tel["timestamp"], tel["fuel_rate_lph"], color=PRIMARY, lw=1.1, label="Fuel rate (LFE SPN 183)")
    axes[1].set_title("Fuel rate")
    axes[1].set_ylabel("L/h")
    axes[1].legend(loc="upper right")
    integrated = (tel["fuel_rate_lph"].fillna(0) * dt / 3600).cumsum()
    c = fuel_counter.sort_values("timestamp")
    c = c[(c["timestamp"] >= tel["timestamp"].iloc[0]) & (c["timestamp"] <= tel["timestamp"].iloc[-1])]
    axes[2].plot(tel["timestamp"], integrated, color=PRIMARY, label="Integrated fuel rate (SPN 183)")
    axes[2].step(c["timestamp"], c["total_fuel_used_l"] - c["total_fuel_used_l"].iloc[0], where="post",
                 color=SECOND, lw=1.2, label="Total fuel counter (HRLFC SPN 5054), from start")
    axes[2].set_title("Cumulative fuel: two independent sources")
    axes[2].set_ylabel("L")
    axes[2].legend(loc="upper left")
    _time_axis(axes[2])
    fig.tight_layout()
    return _save(fig, path)


def fuel_by_state(by_state: pd.DataFrame, path):
    d = by_state.set_index("motion_state").reindex([s for s in MOTION_ORDER if s in set(by_state["motion_state"])])
    d = d.drop(index="unknown", errors="ignore")
    y = np.arange(len(d))
    fig, axes = plt.subplots(1, 2, figsize=(12, 3.8), gridspec_kw={"width_ratios": [1.4, 1]})
    ax = axes[0]
    ax.barh(y - 0.2, d["time_share_pct"], height=0.38, color=PRIMARY, label="Share of time")
    ax.barh(y + 0.2, d["fuel_share_pct"], height=0.38, color=SECOND, label="Share of fuel")
    for i, (t, f) in enumerate(zip(d["time_share_pct"], d["fuel_share_pct"])):
        ax.text(t + 0.5, i - 0.2, f"{t:.0f}%", va="center", fontsize=8, color=TEXT_2)
        ax.text(f + 0.5, i + 0.2, f"{f:.0f}%", va="center", fontsize=8, color=TEXT_2)
    ax.set_yticks(y, d.index)
    ax.invert_yaxis()
    ax.set_xlabel("% of session")
    ax.set_title("Time vs fuel by motion state")
    ax.legend(loc="lower right")
    ax = axes[1]
    ax.barh(y, d["mean_fuel_rate_lph"], color=PRIMARY, height=0.6)
    for i, v in enumerate(d["mean_fuel_rate_lph"]):
        ax.text(v + 0.2, i, f"{v:.1f}", va="center", fontsize=8, color=TEXT_2)
    ax.set_yticks(y, d.index)
    ax.invert_yaxis()
    ax.set_xlabel("L/h")
    ax.set_title("Mean fuel rate by motion state")
    fig.tight_layout()
    return _save(fig, path)


def fuel_relationships(tel, summary_bins: dict, path):
    specs = [
        ("engine_speed_rpm", "Engine speed (rpm)"),
        ("engine_load_pct", "Engine load at current speed (%)"),
        ("wheel_speed_kph", "Wheel-based speed (km/h)"),
        ("accelerator_pedal_pct", "Accelerator pedal (%)"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), sharey=True)
    for ax, (col, label) in zip(axes.flat, specs):
        ax.scatter(tel[col], tel["fuel_rate_lph"], s=4, lw=0, alpha=0.2, color=PRIMARY)
        ax.set_xlabel(label)
        ax.set_title(f"Fuel rate vs {label.split(' (')[0].lower()}")
    for ax in axes[:, 0]:
        ax.set_ylabel("Fuel rate (L/h)")
    fig.tight_layout()
    return _save(fig, path)


def _heat(ax, pivot: pd.DataFrame, title: str):
    from matplotlib.colors import LinearSegmentedColormap

    cmap = LinearSegmentedColormap.from_list("seq", SEQ)
    data = pivot.to_numpy(dtype=float)
    im = ax.imshow(np.nan_to_num(data), cmap=cmap, aspect="auto", vmin=0, origin="lower")
    for (i, j), v in np.ndenumerate(data):
        if not np.isnan(v) and v >= 0.05:
            ax.text(j, i, f"{v:.1f}", ha="center", va="center", fontsize=8,
                    color=SURFACE if v > 0.55 * np.nanmax(data) else TEXT)
    ax.set_xticks(range(pivot.shape[1]), pivot.columns)
    ax.set_yticks(range(pivot.shape[0]), pivot.index)
    ax.set_xlabel("Engine load at current speed (%)")
    ax.set_ylabel("Engine speed (rpm)")
    ax.set_title(title)
    ax.grid(False)
    return im


def operating_zones(zones: pd.DataFrame, path):
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.6))
    for ax, col, title in [
        (axes[0], "time_share_pct", "Share of engine-running time (%)"),
        (axes[1], "fuel_share_pct", "Share of fuel (%)"),
    ]:
        pivot = zones.pivot(index="rpm_band", columns="load_band", values=col)
        _heat(ax, pivot, title)
    fig.tight_layout()
    return _save(fig, path)


def stops(stop_table: pd.DataFrame, path):
    d = stop_table[stop_table["duration_s"] >= 2].reset_index(drop=True)
    labels = [t.strftime("%H:%M:%S") for t in d["start"]]
    y = np.arange(len(d))
    fig, axes = plt.subplots(1, 2, figsize=(12, 0.45 * len(d) + 1.6), sharey=True)
    axes[0].barh(y, d["duration_s"], color=PRIMARY, height=0.6)
    axes[0].set_title("Stop duration (stops ≥ 2 s)")
    axes[0].set_xlabel("s")
    axes[1].barh(y, 1000 * d["fuel_l"], color=PRIMARY, height=0.6)
    axes[1].set_title("Fuel used during stop")
    axes[1].set_xlabel("mL (integrated fuel rate)")
    for ax, vals, fmt in [(axes[0], d["duration_s"], "{:.0f} s"), (axes[1], 1000 * d["fuel_l"], "{:.0f} mL")]:
        for i, v in enumerate(vals):
            ax.text(v, i, " " + fmt.format(v), va="center", fontsize=8, color=TEXT_2)
    axes[0].set_yticks(y, labels)
    axes[0].invert_yaxis()
    axes[0].set_ylabel("Stop start time")
    fig.tight_layout()
    return _save(fig, path)


def coasting(samples: pd.DataFrame, path):
    fig, axes = plt.subplots(1, 2, figsize=(12, 3.8))
    groups = [("engine_above_idle", PRIMARY, "Engine above idle"), ("engine_at_idle", SECOND, "Engine at idle (< 700 rpm)")]
    bins_f = np.arange(0, 15.5, 0.5)
    bins_l = np.arange(0, 36, 1)
    for kind, color, label in groups:
        g = samples[samples["coast_kind"] == kind]
        axes[0].hist(g["fuel_rate_lph"], bins=bins_f, color=color, alpha=0.85, label=f"{label} (n={len(g)})")
        axes[1].hist(g["engine_load_pct"], bins=bins_l, color=color, alpha=0.85, label=label)
    axes[0].set_title("Fuel rate while coasting (pedal 0, brake off, speed falling)")
    axes[0].set_xlabel("L/h")
    axes[0].set_ylabel("samples (0.1 s)")
    axes[0].legend()
    axes[1].set_title("Engine load while coasting")
    axes[1].set_xlabel("%")
    axes[1].legend()
    fig.tight_layout()
    return _save(fig, path)


def temperatures(tel, path):
    fig, axes = plt.subplots(2, 1, figsize=(11, 4.8), sharex=True)
    axes[0].plot(tel["timestamp"], tel["coolant_temp_c"], color=PRIMARY)
    axes[0].set_title("Engine coolant temperature (ET1 SPN 110)")
    axes[0].set_ylabel("°C")
    axes[1].plot(tel["timestamp"], tel["ambient_temp_c"], color=PRIMARY)
    axes[1].set_title("Ambient air temperature (AMB SPN 171)")
    axes[1].set_ylabel("°C")
    _time_axis(axes[1])
    fig.tight_layout()
    return _save(fig, path)


def make_all(tel, details: dict, coast_samples: pd.DataFrame, fuel_counter: pd.DataFrame, summary: dict, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    return [
        fuel_over_time(tel, fuel_counter, out_dir / "09_fuel_over_time.png"),
        fuel_by_state(details["fuel_by_motion_state"], out_dir / "10_fuel_by_motion_state.png"),
        fuel_relationships(tel, summary, out_dir / "11_fuel_rate_relationships.png"),
        operating_zones(details["operating_zones"], out_dir / "12_engine_operating_zones.png"),
        stops(details["stops"], out_dir / "13_stops.png"),
        coasting(coast_samples, out_dir / "14_coasting.png"),
        temperatures(tel, out_dir / "15_coolant_ambient.png"),
    ]
