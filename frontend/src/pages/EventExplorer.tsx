import { useSearchParams } from "react-router-dom";
import { ErrorBox, EventTable, Loading, type SortKey } from "../components/ui";
import { api } from "../lib/api";
import { int, neighbour, shortSession, useApi } from "../lib/format";

const FILTER_KEYS = ["session_id", "duration_class", "attention_group", "min_duration_s", "max_duration_s", "min_fuel_l",
  "max_fuel_l", "cold_engine", "raised_rpm", "preceded_by", "followed_by"] as const;
const CLASS_LABEL: Record<string, string> = { under_1_min: "< 1 min", "1_to_5_min": "1–5 min", "5_to_15_min": "5–15 min", over_15_min: "> 15 min" };

export default function EventExplorer() {
  const [sp, setSp] = useSearchParams();
  const page = Number(sp.get("page") ?? 1);
  const sort = sp.get("sort") ?? "-duration_seconds";
  const q: Record<string, string | number> = { page, page_size: 50, sort };
  FILTER_KEYS.forEach((k) => { const v = sp.get(k); if (v) q[k] = v; });

  const events = useApi(() => api.events(q), [sp.toString()]);
  const sessions = useApi(() => api.sessions("all"), []);

  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v); else n.delete(k);
    if (k !== "page") n.delete("page");
    setSp(n, { replace: true });
  };
  const onSort = (k: SortKey) => set("sort", sort === `-${k}` ? k : `-${k}`);
  const opts = events.data?.filter_options;
  const Sel = ({ k, label, options }: { k: string; label: string; options: [string, string][] }) => (
    <label className="field">{label}
      <select value={sp.get(k) ?? ""} onChange={(e) => set(k, e.target.value)}>
        <option value="">Any</option>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
  const Num = ({ k, label, step }: { k: string; label: string; step: string }) => (
    <label className="field">{label}
      <input type="number" min={0} step={step} defaultValue={sp.get(k) ?? ""} key={`${k}-${sp.get(k) ?? ""}`}
        onBlur={(e) => set(k, e.target.value)} onKeyDown={(e) => e.key === "Enter" && set(k, (e.target as HTMLInputElement).value)} />
    </label>
  );

  return (
    <div className="page">
      <div className="page-head">
        <div className="eyebrow">Idle Intelligence · Event Explorer</div>
        <h1>Stationary engine-on events</h1>
        <p className="lede">Every Gold event with its measured values. Select a row for the event detail, its telemetry and its evidence object.</p>
      </div>

      <div className="filters">
        <Sel k="attention_group" label="Group" options={[["routine_short", "Routine / short"], ["extended", "Extended (≥ 5 min)"]]} />
        <Sel k="duration_class" label="Duration class" options={(opts?.duration_class ?? []).map((c) => [c, CLASS_LABEL[c] ?? c])} />
        <Num k="min_duration_s" label="Min duration s" step="10" />
        <Num k="max_duration_s" label="Max duration s" step="10" />
        <Num k="min_fuel_l" label="Min fuel L" step="0.01" />
        <Num k="max_fuel_l" label="Max fuel L" step="0.01" />
        <label className="field">Session
          <select value={sp.get("session_id") ?? ""} onChange={(e) => set("session_id", e.target.value)}>
            <option value="">Any</option>
            {(sessions.data?.items ?? []).filter((s) => s.stationary_events > 0).map((s) => (
              <option key={s.recording_session_id} value={s.recording_session_id}>
                {shortSession(s.recording_session_id)} ({s.stationary_events})
              </option>
            ))}
          </select>
        </label>
        <Sel k="cold_engine" label="Cold engine" options={[["true", "Yes"], ["false", "No"]]} />
        <Sel k="raised_rpm" label="Raised RPM" options={[["true", "Yes"], ["false", "No"]]} />
        <Sel k="preceded_by" label="Preceded by" options={(opts?.preceded_by ?? []).map((v) => [v, neighbour(v)])} />
        <Sel k="followed_by" label="Followed by" options={(opts?.followed_by ?? []).map((v) => [v, neighbour(v)])} />
        <button className="btn" onClick={() => setSp(new URLSearchParams(), { replace: true })}>Reset</button>
      </div>

      {events.error && <ErrorBox error={events.error} onRetry={events.reload} embedded />}
      {!events.data && !events.error && <Loading embedded />}
      {events.data && (
        <>
          <div className="spread small muted" style={{ marginBottom: 8 }}>
            <span><b style={{ color: "#fff" }}>{int(events.data.total)}</b> of 880 events match · most short events are ordinary stops between driving</span>
            <span>Detection confidence describes the state detection; interpretation is context unknown for every event.</span>
          </div>
          <EventTable events={events.data.items} sort={sort} onSort={onSort} showSession />
          <div className="pager">
            <span>Page {events.data.page} of {Math.max(1, events.data.pages)}</span>
            <button className="btn" disabled={page <= 1} onClick={() => set("page", String(page - 1))}>← Prev</button>
            <button className="btn" disabled={page >= events.data.pages} onClick={() => set("page", String(page + 1))}>Next →</button>
          </div>
        </>
      )}
    </div>
  );
}
