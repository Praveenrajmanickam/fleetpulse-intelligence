"""Process memory sampling and Spark event-log analysis (per pipeline phase)."""

from __future__ import annotations

import json
import os
import statistics
import threading
import time
from pathlib import Path


# --- memory -----------------------------------------------------------------------
def _rss_kb(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        pass
    return 0


def _ppid(pid: int) -> int:
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
    except (FileNotFoundError, IndexError, ValueError):
        return 0


def descendant_jvms(root_pid: int) -> list[int]:
    """Java processes whose parent chain leads to ``root_pid`` (the Spark driver JVM)."""
    out = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            cmd = (d / "cmdline").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if b"java" not in cmd.split(b"\0")[0]:
            continue
        pid, seen = int(d.name), 0
        p = pid
        while p > 1 and seen < 10:
            p = _ppid(p)
            seen += 1
            if p == root_pid:
                out.append(pid)
                break
    return out


class MemorySampler:
    """Samples RSS of this Python process and its Spark JVM every ``interval`` seconds."""

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.samples: list[tuple[float, int, int]] = []  # (epoch, python_kb, jvm_kb)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> MemorySampler:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def _run(self) -> None:
        me = os.getpid()
        jvms: list[int] = []
        while not self._stop.is_set():
            if not jvms:
                jvms = descendant_jvms(me)
            self.samples.append((time.time(), _rss_kb(me), sum(_rss_kb(p) for p in jvms)))
            time.sleep(self.interval)

    def peaks(self, windows: dict[str, list[float]]) -> dict:
        def peak(t0=0.0, t1=float("inf")):
            s = [x for x in self.samples if t0 <= x[0] <= t1]
            if not s:
                return {"python_mb": None, "jvm_mb": None}
            return {"python_mb": round(max(x[1] for x in s) / 1024), "jvm_mb": round(max(x[2] for x in s) / 1024)}

        return {"overall": peak(), **{k: peak(*v) for k, v in windows.items()}}


# --- Spark event log ---------------------------------------------------------------
def _task_metrics(ev: dict) -> dict:
    tm = ev.get("Task Metrics") or {}
    sr = tm.get("Shuffle Read Metrics") or {}
    sw = tm.get("Shuffle Write Metrics") or {}
    ti = ev["Task Info"]
    return {
        "stage": ev["Stage ID"],
        "launch": ti["Launch Time"] / 1000,
        "finish": ti["Finish Time"] / 1000,
        "run_ms": tm.get("Executor Run Time", 0),
        "cpu_ns": tm.get("Executor CPU Time", 0),
        "gc_ms": tm.get("JVM GC Time", 0),
        "mem_spill": tm.get("Memory Bytes Spilled", 0),
        "disk_spill": tm.get("Disk Bytes Spilled", 0),
        "peak_exec_mem": tm.get("Peak Execution Memory", 0),
        "shuffle_read": sr.get("Remote Bytes Read", 0) + sr.get("Local Bytes Read", 0),
        "shuffle_write": sw.get("Shuffle Bytes Written", 0),
        "input_bytes": (tm.get("Input Metrics") or {}).get("Bytes Read", 0),
        "output_bytes": (tm.get("Output Metrics") or {}).get("Bytes Written", 0),
    }


def read_event_log(log_dir: Path) -> tuple[list[dict], dict[int, str]]:
    files = [p for p in Path(log_dir).iterdir() if p.is_file()]
    tasks, stage_names = [], {}
    for f in files:
        for line in f.open():
            ev = json.loads(line)
            kind = ev.get("Event")
            if kind == "SparkListenerTaskEnd":
                tasks.append(_task_metrics(ev))
            elif kind == "SparkListenerStageCompleted":
                info = ev["Stage Info"]
                stage_names[info["Stage ID"]] = info.get("Stage Name", "")
    return tasks, stage_names


def phase_metrics(tasks: list[dict], stage_names: dict[int, str], windows: dict[str, list[float]]) -> dict:
    """Aggregate task metrics per phase (task attributed by its finish time)."""
    out = {}
    for phase, (t0, t1) in windows.items():
        ts = [t for t in tasks if t0 <= t["finish"] <= t1 + 0.5]
        if not ts:
            out[phase] = {"tasks": 0}
            continue
        by_stage: dict[int, list[dict]] = {}
        for t in ts:
            by_stage.setdefault(t["stage"], []).append(t)
        worst = None
        for sid, st in by_stage.items():
            if len(st) < 2:
                continue
            runs = [x["run_ms"] for x in st]
            med = statistics.median(runs)
            ratio = max(runs) / med if med > 0 else None
            if worst is None or max(runs) > worst["max_task_s"] * 1000:
                worst = {"stage": sid, "name": stage_names.get(sid, "")[:80], "tasks": len(st),
                         "max_task_s": round(max(runs) / 1000, 2), "median_task_s": round(med / 1000, 3),
                         "max_over_median": round(ratio, 1) if ratio else None}
        longest = max(ts, key=lambda x: x["run_ms"])
        out[phase] = {
            "stages": len(by_stage),
            "tasks": len(ts),
            "executor_run_s": round(sum(t["run_ms"] for t in ts) / 1000, 1),
            "executor_cpu_s": round(sum(t["cpu_ns"] for t in ts) / 1e9, 1),
            "jvm_gc_s": round(sum(t["gc_ms"] for t in ts) / 1000, 1),
            "memory_spill_mb": round(sum(t["mem_spill"] for t in ts) / 1e6, 1),
            "disk_spill_mb": round(sum(t["disk_spill"] for t in ts) / 1e6, 1),
            "shuffle_read_mb": round(sum(t["shuffle_read"] for t in ts) / 1e6, 1),
            "shuffle_write_mb": round(sum(t["shuffle_write"] for t in ts) / 1e6, 1),
            "input_mb": round(sum(t["input_bytes"] for t in ts) / 1e6, 1),
            "output_mb": round(sum(t["output_bytes"] for t in ts) / 1e6, 1),
            "max_peak_execution_memory_mb": round(max(t["peak_exec_mem"] for t in ts) / 1e6, 1),
            "longest_task_s": round(longest["run_ms"] / 1000, 2),
            "longest_task_stage": stage_names.get(longest["stage"], "")[:80],
            "most_skewed_stage": worst,
        }
    return out
