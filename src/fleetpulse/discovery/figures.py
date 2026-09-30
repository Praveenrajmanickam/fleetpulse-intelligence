"""Exploratory figures for business discovery v1 (plain, reuses analysis.plots styling)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from fleetpulse.analysis.plots import PRIMARY, SURFACE, TEXT, TEXT_2, _save, plt

SECOND = "#eb6834"
REGIME_ORDER = ["stationary_engine_on", "creeping", "accelerating", "steady_speed", "decelerating"]
REGIME_COLORS = dict(zip(REGIME_ORDER, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]))


def session_timeline(df: pd.DataFrame, sid: str, title: str, path: Path) -> Path:
    s = df[df["recording_session_id"] == sid]
    t = (s["timestamp"] - s["timestamp"].iloc[0]).dt.total_seconds() / 60
    fig, axes = plt.subplots(4, 1, figsize=(11, 8.5), sharex=True)
    panels = [("wheel_speed_kph", "Wheel-based speed (km/h)"), ("engine_speed_rpm", "Engine speed (rpm)"),
              ("fuel_rate_lph", "Fuel rate (L/h)"), ("coolant_temp_c", "Coolant temperature (°C)")]
    for ax, (col, label) in zip(axes, panels):
        ax.plot(t, s[col], color=PRIMARY, lw=1.0)
        ax.set_title(label)
    axes[-1].set_xlabel("Minutes since recording-session start")
    fig.suptitle(title, x=0.01, ha="left", fontsize=11, color=TEXT)
    fig.tight_layout()
    return _save(fig, path)


def fuel_vs_load(df: pd.DataFrame, rg: pd.DataFrame, path: Path) -> Path:
    on = df["engine_speed_rpm"] >= 300
    bands = [("idle band (300-700)", "#9ec5f4"), ("1000-1300", PRIMARY), (">=1600", SECOND)]
    fig, ax = plt.subplots(figsize=(8, 5.5))
    for b, c in bands:
        m = on & (rg["rpm_band"] == b)
        ax.scatter(df.loc[m, "engine_load_pct"], df.loc[m, "fuel_rate_lph"], s=3, lw=0, alpha=0.3, color=c, label=f"rpm {b}")
    ax.set_xlabel("Engine load at current speed (%)")
    ax.set_ylabel("Fuel rate (L/h)")
    ax.set_title("Fuel rate vs engine load, by engine-speed band (sample of bands)")
    ax.legend(markerscale=4)
    return _save(fig, path)


def fuel_by_regime(df: pd.DataFrame, rg: pd.DataFrame, path: Path) -> Path:
    g = df.assign(regime=rg["regime"]).groupby("regime")
    time_pct = (g.size() / len(df) * 100).reindex(REGIME_ORDER).fillna(0)
    fuel_pct = (g["fuel_l"].sum() / df["fuel_l"].sum() * 100).reindex(REGIME_ORDER).fillna(0)
    y = np.arange(len(REGIME_ORDER))
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.barh(y - 0.2, time_pct, height=0.38, color=PRIMARY, label="Share of observed time")
    ax.barh(y + 0.2, fuel_pct, height=0.38, color=SECOND, label="Share of observed fuel")
    for i, (a, b) in enumerate(zip(time_pct, fuel_pct)):
        ax.text(a + 0.5, i - 0.2, f"{a:.0f}%", va="center", fontsize=8, color=TEXT_2)
        ax.text(b + 0.5, i + 0.2, f"{b:.0f}%", va="center", fontsize=8, color=TEXT_2)
    ax.set_yticks(y, REGIME_ORDER)
    ax.invert_yaxis()
    ax.set_xlabel("%")
    ax.set_title("Observed time and fuel by operating regime (5 sessions, exploratory definitions)")
    ax.legend(loc="lower right")
    return _save(fig, path)


def stationary_episodes(eps: dict, path: Path) -> Path:
    e = eps["engine_on_stationary"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    axes[0].hist(e["duration_s"], bins=np.logspace(np.log10(2), np.log10(max(e["duration_s"].max(), 3)), 25), color=PRIMARY)
    axes[0].set_xscale("log")
    axes[0].set_xlabel("Episode duration (s, log scale)")
    axes[0].set_ylabel("Episodes")
    axes[0].set_title(f"Engine-on stationary episodes (n={len(e)})")
    axes[1].scatter(e["duration_s"] / 60, e["fuel_l"] * 1000, s=14, color=PRIMARY)
    axes[1].set_xlabel("Episode duration (min)")
    axes[1].set_ylabel("Observed fuel (mL)")
    axes[1].set_title("Observed fuel per engine-on stationary episode")
    fig.tight_layout()
    return _save(fig, path)


def warm_up(df: pd.DataFrame, targets: pd.DataFrame, path: Path) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
    colors = [PRIMARY, SECOND, "#1baf7a", "#eda100", "#e87ba4"]
    for (_, s), c in zip(targets.iterrows(), colors):
        x = df[df["recording_session_id"] == s["recording_session_id"]]
        t = (x["timestamp"] - x["timestamp"].iloc[0]).dt.total_seconds() / 60
        # gap_before_s in a subset build is relative to the subset, so label by start coolant instead
        label = f"{pd.Timestamp(s['session_start']):%b %d %H:%M} (coolant at start {x['coolant_temp_c'].dropna().iloc[0]:.0f} °C)"
        axes[0].plot(t, x["coolant_temp_c"], color=c, lw=1.2, label=label)
    axes[0].set_xlabel("Minutes since session start")
    axes[0].set_ylabel("Coolant temperature (°C)")
    axes[0].set_title("Coolant temperature by recording session")
    axes[0].legend(fontsize=7)
    idle = (df["engine_speed_rpm"] >= 300) & (df["wheel_speed_kph"] < 0.5) & (df["accelerator_pedal_pct"] == 0)
    axes[1].scatter(df.loc[idle, "coolant_temp_c"], df.loc[idle, "fuel_rate_lph"], s=3, lw=0, alpha=0.25, color=PRIMARY)
    axes[1].set_xlabel("Coolant temperature (°C)")
    axes[1].set_ylabel("Fuel rate (L/h)")
    axes[1].set_title("Stationary, pedal 0, engine on: fuel rate vs coolant")
    fig.tight_layout()
    return _save(fig, path)


def distributions(df: pd.DataFrame, rg: pd.DataFrame, path: Path) -> Path:
    cols = [("speed_band", "Speed band (km/h)"), ("rpm_band", "Engine-speed band (rpm)"),
            ("load_band", "Engine-load band (%)"), ("fuel_band", "Fuel-rate band (L/h)")]
    fig, axes = plt.subplots(2, 2, figsize=(12, 7))
    for ax, (c, title) in zip(axes.flat, cols):
        share = rg[c].value_counts(normalize=True, sort=False).mul(100)
        ax.barh(range(len(share)), share.values, color=PRIMARY)
        ax.set_yticks(range(len(share)), [str(i) for i in share.index])
        for i, v in enumerate(share.values):
            ax.text(v + 0.5, i, f"{v:.0f}%", va="center", fontsize=8, color=TEXT_2)
        ax.invert_yaxis()
        ax.set_title(f"Time share: {title}")
    fig.tight_layout()
    return _save(fig, path)


def rpm_vs_speed(df: pd.DataFrame, path: Path) -> Path:
    m = df["wheel_speed_kph"] >= 0.5
    fig, ax = plt.subplots(figsize=(8, 5.5))
    ax.scatter(df.loc[m, "wheel_speed_kph"], df.loc[m, "engine_speed_rpm"], s=2, lw=0, alpha=0.15, color=PRIMARY)
    ax.set_xlabel("Wheel-based speed (km/h)")
    ax.set_ylabel("Engine speed (rpm)")
    ax.set_title("Engine speed vs vehicle speed while moving (5 sessions)")
    return _save(fig, path)


def make_all(df, rg, eps, targets, fig_dir: Path) -> list[Path]:
    fig_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams["figure.facecolor"] = SURFACE
    t = targets.sort_values("session_start")
    longest = t.loc[t["duration_s"].idxmax(), "recording_session_id"]
    cold = t.loc[t["gap_before_s"].idxmax(), "recording_session_id"]
    return [
        session_timeline(df, longest, "Sustained-driving session: speed, RPM, fuel rate, coolant", fig_dir / "01_session_sustained_driving.png"),
        session_timeline(df, cold, "Cold-start session 2021-02-02 (coolant 23 °C at start)", fig_dir / "02_session_cold_start.png"),
        distributions(df, rg, fig_dir / "03_operating_distributions.png"),
        fuel_by_regime(df, rg, fig_dir / "04_fuel_by_regime.png"),
        fuel_vs_load(df, rg, fig_dir / "05_fuel_vs_load_by_rpm.png"),
        stationary_episodes(eps, fig_dir / "06_engine_on_stationary_episodes.png"),
        warm_up(df, t, fig_dir / "07_warm_up.png"),
        rpm_vs_speed(df, fig_dir / "08_rpm_vs_speed.png"),
    ]
