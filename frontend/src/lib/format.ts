// Display formatting only - unit conversion for readability, no metric derivation.
import { useEffect, useState } from "react";

const DASH = "—";

export function num(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return DASH;
  return v.toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function int(v: number | null | undefined): string {
  if (v === null || v === undefined) return DASH;
  return v.toLocaleString("en-GB", { maximumFractionDigits: 0 });
}

/** seconds -> "19m 29s" / "1h 04m" / "32.4 s" */
export function duration(s: number | null | undefined): string {
  if (s === null || s === undefined) return DASH;
  if (s < 60) return `${s.toFixed(1)} s`;
  const total = Math.round(s);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(sec).padStart(2, "0")}s`;
}

/** seconds -> hours number for charts */
export const hours = (s: number | null | undefined) => (s ?? 0) / 3600;

/** Logger wall clock, timezone unknown: shown as-is without conversion. */
export function ts(iso: string | null | undefined, withSeconds = true): string {
  if (!iso) return DASH;
  const [d, t] = iso.split("T");
  return `${d} ${t.slice(0, withSeconds ? 8 : 5)}`;
}

export const clock = (iso: string) => iso.split("T")[1]?.slice(0, 8) ?? iso;

const NEIGHBOUR: Record<string, string> = {
  driving: "Driving",
  engine_off: "Engine off",
  signal_unavailable: "Signal unavailable",
  session_start: "Recording start",
  session_end: "Recording end",
};
export const neighbour = (v: string) => NEIGHBOUR[v] ?? v;

const TAGS: Record<string, string> = {
  cold_engine_at_start: "Cold engine",
  raised_engine_speed: "Raised RPM",
  over_15_min: "> 15 min",
  at_session_start: "At recording start",
};
export const tag = (v: string) => TAGS[v] ?? v;

export const confidence = (v: string) => `${v.toUpperCase()} CONFIDENCE`;
export const interpretation = (v: string) => v.replace(/_/g, " ").toUpperCase();

export const shortSession = (id: string) => {
  const m = id.match(/(\d{8})T(\d{6})/);
  return m ? `${m[1].slice(0, 4)}-${m[1].slice(4, 6)}-${m[1].slice(6)} ${m[2].slice(0, 2)}:${m[2].slice(2, 4)}` : id;
};

export function useApi<T>(fn: () => Promise<T>, deps: unknown[]): { data?: T; error?: Error; loading: boolean } {
  const [state, setState] = useState<{ data?: T; error?: Error; loading: boolean }>({ loading: true });
  useEffect(() => {
    let live = true;
    setState((s) => ({ ...s, loading: true }));
    fn()
      .then((data) => live && setState({ data, loading: false }))
      .catch((error) => live && setState({ error, loading: false }));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
