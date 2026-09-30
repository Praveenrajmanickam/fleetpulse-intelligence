"""Static exploratory figures (PNG) for one telemetry session.

Conventions: one y-scale per axes (small multiples instead of dual axes),
recessive grid, 1.5 px lines, categorical colours in fixed slot order from a
palette validated for colour-vision deficiency.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"
PRIMARY = "#2a78d6"
BRAKE = "#e34948"
UNKNOWN = "#c3c2b7"

MOTION_ORDER = ["stationary", "creeping", "accelerating", "cruising", "decelerating", "unknown"]
MOTION_COLORS = dict(zip(MOTION_ORDER, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", UNKNOWN]))

plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID,
        "axes.labelcolor": TEXT_2,
        "axes.titlecolor": TEXT,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.labelsize": 9,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.color": TEXT_2,
        "ytick.color": TEXT_2,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.frameon": False,
        "legend.fontsize": 8,
        "lines.linewidth": 1.5,
        "font.family": "DejaVu Sans",
    }
)


def _time_axis(ax):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.set_xlabel("Time (logged, timezone unknown)")


def _brake_spans(ax, t: pd.Series, brake: pd.Series, label=True):
    on = brake.fillna(0).astype(int).to_numpy()
    edges = np.flatnonzero(np.diff(np.r_[0, on, 0]))
    first = True
    for start, stop in zip(edges[::2], edges[1::2]):
        ax.axvspan(
            t.iloc[start], t.iloc[stop - 1], color=BRAKE, alpha=0.15, lw=0,
            label="Brake pedal pressed (SPN 597)" if (label and first) else None,
        )
        first = False


def _save(fig, path: Path) -> Path:
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def speed_over_time(tel, path):
    fig, ax = plt.subplots(figsize=(11, 3.4))
    _brake_spans(ax, tel["timestamp"], tel["brake_switch"])
    ax.plot(tel["timestamp"], tel["wheel_speed_kph"], color=PRIMARY, label="Wheel-based speed (SPN 84)")
    ax.set_title("Vehicle speed")
    ax.set_ylabel("km/h")
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper right")
    _time_axis(ax)
    return _save(fig, path)


def rpm_over_time(tel, path):
    fig, ax = plt.subplots(figsize=(11, 3.4))
    ax.plot(tel["timestamp"], tel["engine_speed_rpm"], color=PRIMARY)
    ax.set_title("Engine speed (EEC1 SPN 190)")
    ax.set_ylabel("rpm")
    _time_axis(ax)
    return _save(fig, path)


def rpm_vs_speed(tel, path):
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(tel["wheel_speed_kph"], tel["engine_speed_rpm"], s=4, color=PRIMARY, alpha=0.25, lw=0)
    ax.set_title("Engine speed vs vehicle speed (10 Hz samples)")
    ax.set_xlabel("Wheel-based speed (km/h)")
    ax.set_ylabel("Engine speed (rpm)")
    ax.set_xlim(left=0)
    return _save(fig, path)


def demand_and_load(tel, path):
    fig, axes = plt.subplots(3, 1, figsize=(11, 7), sharex=True)
    for ax, col, title in [
        (axes[0], "accelerator_pedal_pct", "Accelerator pedal position 1 (EEC2 SPN 91)"),
        (axes[1], "engine_load_pct", "Engine percent load at current speed (EEC2 SPN 92)"),
    ]:
        ax.plot(tel["timestamp"], tel[col], color=PRIMARY)
        ax.set_title(title)
        ax.set_ylabel("%")
        ax.set_ylim(0, 100)
    axes[2].plot(tel["timestamp"], tel["wheel_speed_kph"], color=TEXT_2, lw=1.2)
    axes[2].set_title("Wheel-based speed, for reference (SPN 84)")
    axes[2].set_ylabel("km/h")
    _time_axis(axes[2])
    fig.tight_layout()
    return _save(fig, path)


def braking(tel, states, path):
    fig, axes = plt.subplots(1, 2, figsize=(12, 3.8), gridspec_kw={"width_ratios": [2.2, 1]})
    ax = axes[0]
    _brake_spans(ax, tel["timestamp"], tel["brake_switch"])
    ax.plot(tel["timestamp"], states["acceleration_mps2"], color=PRIMARY, lw=1.2, label="Acceleration (derived from SPN 84)")
    ax.axhline(0, color=TEXT_2, lw=0.8)
    ax.set_title("Brake switch vs derived acceleration")
    ax.set_ylabel("m/s²")
    ax.legend(loc="lower right")
    _time_axis(ax)

    ax = axes[1]
    moving = tel["wheel_speed_kph"] >= 0.5
    brake = tel["brake_switch"] == 1
    bins = np.arange(-1.4, 1.45, 0.05)
    a = states["acceleration_mps2"]
    ax.hist(a[moving & ~brake].dropna(), bins=bins, color=PRIMARY, alpha=0.9, label="Moving, brake released")
    ax.hist(a[moving & brake].dropna(), bins=bins, color=BRAKE, alpha=0.8, label="Moving, brake pressed")
    ax.set_title("Acceleration while moving")
    ax.set_xlabel("m/s²")
    ax.set_ylabel("samples (0.1 s)")
    ax.legend(loc="upper left")
    fig.tight_layout()
    return _save(fig, path)


def operating_states(tel, states, path):
    fig, axes = plt.subplots(
        2, 1, figsize=(11, 5.2), gridspec_kw={"height_ratios": [3, 1.2]}, sharex=False
    )
    ax = axes[0]
    for state in MOTION_ORDER:
        mask = states["motion_state"] == state
        if mask.any():
            ax.scatter(tel.loc[mask, "timestamp"], tel.loc[mask, "wheel_speed_kph"], s=6, lw=0,
                       color=MOTION_COLORS[state], label=state)
    ax.set_title("Motion state (derived) over vehicle speed")
    ax.set_ylabel("km/h")
    ax.legend(loc="upper right", ncol=6, markerscale=2.5)
    _time_axis(ax)

    ax = axes[1]
    share = states["motion_state"].astype(str).value_counts(normalize=True).mul(100)
    order = [s for s in MOTION_ORDER if s in share.index]
    left = 0.0
    for state in order:
        w = share[state]
        ax.barh(0, w, left=left, color=MOTION_COLORS[state], edgecolor=SURFACE, linewidth=2, height=0.6)
        if w >= 4:
            ax.text(left + w / 2, 0, f"{state}\n{w:.1f}%", ha="center", va="center", fontsize=8, color=TEXT)
        left += w
    ax.set_xlim(0, 100)
    ax.set_yticks([])
    ax.set_xlabel("% of session time")
    ax.set_title("Time share by motion state")
    ax.grid(False)
    fig.tight_layout()
    return _save(fig, path)


def rpm_per_kph_histogram(states, path):
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.hist(states["rpm_per_kph"].dropna(), bins=np.arange(0, 160, 1), color=PRIMARY)
    ax.set_title("Engine rpm per km/h while moving ≥ 5 km/h (derived, not a decoded gear)")
    ax.set_xlabel("rpm / (km/h)")
    ax.set_ylabel("samples (0.1 s)")
    return _save(fig, path)


def overview(tel, states, path):
    fig, axes = plt.subplots(5, 1, figsize=(11, 10), sharex=True,
                             gridspec_kw={"height_ratios": [2, 2, 1.5, 1.5, 0.6]})
    panels = [
        ("wheel_speed_kph", "Wheel-based speed (SPN 84)", "km/h"),
        ("engine_speed_rpm", "Engine speed (SPN 190)", "rpm"),
        ("accelerator_pedal_pct", "Accelerator pedal (SPN 91)", "%"),
        ("engine_load_pct", "Engine load at current speed (SPN 92)", "%"),
    ]
    for ax, (col, title, unit) in zip(axes, panels):
        _brake_spans(ax, tel["timestamp"], tel["brake_switch"], label=(col == "wheel_speed_kph"))
        ax.plot(tel["timestamp"], tel[col], color=PRIMARY, lw=1.2)
        ax.set_title(title)
        ax.set_ylabel(unit)
    axes[0].legend(loc="upper right")
    ax = axes[4]
    for state in MOTION_ORDER:
        mask = (states["motion_state"] == state).to_numpy()
        if mask.any():
            ax.fill_between(tel["timestamp"], 0, 1, where=mask, color=MOTION_COLORS[state], lw=0,
                            step="post", label=state)
    ax.set_yticks([])
    ax.set_title("Motion state (derived)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.9), ncol=6)
    ax.grid(False)
    _time_axis(ax)
    fig.tight_layout()
    return _save(fig, path)


def make_all(tel: pd.DataFrame, states: pd.DataFrame, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return [
        overview(tel, states, out_dir / "01_overview.png"),
        speed_over_time(tel, out_dir / "02_speed_over_time.png"),
        rpm_over_time(tel, out_dir / "03_engine_rpm_over_time.png"),
        rpm_vs_speed(tel, out_dir / "04_rpm_vs_speed.png"),
        demand_and_load(tel, out_dir / "05_pedal_and_load.png"),
        braking(tel, states, out_dir / "06_braking.png"),
        operating_states(tel, states, out_dir / "07_motion_states.png"),
        rpm_per_kph_histogram(states, out_dir / "08_rpm_per_kph_histogram.png"),
    ]
