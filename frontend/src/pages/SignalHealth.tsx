import { useState } from "react";
import { Link } from "react-router-dom";
import { Provenance } from "../components/foundation/Provenance";
import { ErrorBox, Loading } from "../components/ui";
import VehicleVisualization from "../components/vehicle/VehicleVisualization";
import { HEALTH_META, HealthBadge, SystemIcon } from "../components/vehicle/signals";
import { VEHICLE_SYSTEMS, type SignalAnnotation, type VehicleSystemId } from "../components/vehicle/types";
import { signalsApi, type HealthState } from "../lib/api";
import { int, num, ts, useApi } from "../lib/format";

const ORDER: HealthState[] = ["quality_warning", "unavailable", "partial", "available"];

export default function SignalHealth() {
  const health = useApi(signalsApi.health, []);
  const [scope, setScope] = useState<"engine_running_rows" | "all_rows">("engine_running_rows");
  const [active, setActive] = useState<VehicleSystemId | null>(null);
  if (health.error) return <ErrorBox error={health.error} onRetry={health.reload} />;
  if (!health.data) return <Loading />;
  const h = health.data;
  const bySystem = (id: VehicleSystemId) => h.signals.filter((s) => s.system === id);
  const worst = (id: VehicleSystemId) => ORDER.find((st) => bySystem(id).some((s) => s.state === st)) ?? "available";
  const annotations: SignalAnnotation[] = VEHICLE_SYSTEMS.map((sys) => {
    const w = worst(sys.id);
    const flagged = bySystem(sys.id).filter((s) => s.state !== "available");
    return {
      signal: sys.signals[0],
      label: flagged.length ? flagged.map((s) => s.label).join(", ") : `${sys.signals.length} signal${sys.signals.length > 1 ? "s" : ""}`,
      value: `${HEALTH_META[w].glyph} ${HEALTH_META[w].label}`,
      context: { kind: "dataset", label: "Signal health" }, provenance: "derived",
      quality: w === "quality_warning" ? "warning" : w === "unavailable" ? "unavailable" : "ok",
    };
  });
  const counts = ORDER.map((st) => [st, h.signals.filter((s) => s.state === st).length] as const);

  return (
    <div className="page">
      <div className="page-head">
        <div className="eyebrow">Vehicle · Signal health</div>
        <h1>Signal Health</h1>
      </div>
      <div className="health-banner" role="note">
        <div>
          <div className="hb-title">Data / signal health</div>
          <div className="hb-sub">Not mechanical vehicle health.</div>
        </div>
        <p>{h.statement} Availability is counted from Silver telemetry status and freshness columns ({h.freshness_rule}).</p>
      </div>

      <div className="scope-strip">
        <div><span className="lbl">Vehicle</span><span className="mono">{h.scope.vehicle_id}</span></div>
        <div><span className="lbl">Dataset</span><span>{h.scope.dataset} · {h.scope.recording_sessions} recording sessions</span></div>
        <div><span className="lbl">Window</span><span className="mono">{ts(h.scope.window_start, false)} → {ts(h.scope.window_end, false)}</span></div>
        <div><span className="lbl">Silver rows analysed</span><span className="mono">{int(h.rows_analysed)}</span></div>
        <div><span className="lbl">Engine-running rows</span><span className="mono">{int(h.engine_running_rows)}</span></div>
        <div><span className="lbl">Nature</span><span>Historical recording · not live</span></div>
      </div>

      <div className="health-grid">
        <div className="panel">
          <div className="spread" style={{ marginBottom: 6 }}>
            <div className="row" style={{ gap: 14 }}>
              {counts.map(([st, n]) => <span key={st} className="row" style={{ gap: 6 }}><HealthBadge state={st} /><b className="mono">{n}</b></span>)}
            </div>
            <Provenance kind="derived" />
          </div>
          <VehicleVisualization variant="inspect" activeSystem={active} onSystemSelect={setActive} annotations={annotations}
            highlightedSystems={VEHICLE_SYSTEMS.filter((s) => worst(s.id) === "quality_warning").map((s) => s.id)}
            caption="Signal health by vehicle system" />
        </div>

        <div className="panel health-list">
          <div className="spread" style={{ marginBottom: 10 }}>
            <h2>Signals by system</h2>
            <div className="seg" role="radiogroup" aria-label="Rows in scope">
              <button role="radio" aria-checked={scope === "engine_running_rows"} className={scope === "engine_running_rows" ? "on" : ""}
                onClick={() => setScope("engine_running_rows")}>Engine running</button>
              <button role="radio" aria-checked={scope === "all_rows"} className={scope === "all_rows" ? "on" : ""}
                onClick={() => setScope("all_rows")}>All rows</button>
            </div>
          </div>
          {VEHICLE_SYSTEMS.filter((s) => !active || s.id === active).map((sys) => (
            <div key={sys.id} className="health-sys">
              <div className="health-sys-head"><SystemIcon system={sys.id} /> {sys.label}</div>
              {bySystem(sys.id).map((s) => {
                const sc = s.scopes[scope];
                return (
                  <div key={s.name} className={`health-row ${s.state}`}>
                    <HealthBadge state={s.state} compact />
                    <Link to={`/vehicle/sensors?signal=${s.name}`} className="health-name">{s.label}</Link>
                    <span className="health-bar" aria-hidden="true"><i style={{ width: `${sc.available_pct ?? 0}%` }} /></span>
                    <span className="mono small" title={`valid & fresh ${int(sc.available_rows)} of ${int(sc.rows)} rows`}>{num(sc.available_pct, 2)}%</span>
                    <span className="health-note small muted">
                      {HEALTH_META[s.state].label}
                      {s.caveats.filter((c) => c.level !== "note").map((c) => ` — ${c.text}`)}
                      {sc.no_observation_rows > 0 && ` · no observation in ${int(sc.no_observation_rows)} row${sc.no_observation_rows === 1 ? "" : "s"}`}
                      {sc.stale_rows > 0 && ` · stale in ${int(sc.stale_rows)} row${sc.stale_rows === 1 ? "" : "s"}`}
                    </span>
                  </div>
                );
              })}
            </div>
          ))}
          <div className="state-rules">
            {ORDER.map((st) => <div key={st}><HealthBadge state={st} /> <span className="small muted">{h.state_rules[st]}</span></div>)}
          </div>
        </div>
      </div>
    </div>
  );
}
