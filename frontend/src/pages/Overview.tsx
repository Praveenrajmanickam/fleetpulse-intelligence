import { useState } from "react";
import { Link } from "react-router-dom";
import AnalystPanel from "../components/AnalystPanel";
import { EngineTimeComposition, ShareBar, C } from "../components/charts";
import { ErrorBox, Kpi, Loading, Panel } from "../components/ui";
import { api } from "../lib/api";
import { duration, int, num, ts, useApi } from "../lib/format";

export default function Overview() {
  const { data, error } = useApi(api.overview, []);
  const [showAnalyst, setShowAnalyst] = useState(false);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const k = data.kpis;
  const s = data.sessions;
  return (
    <div className="page">
      {showAnalyst && <AnalystPanel scope="overall" onClose={() => setShowAnalyst(false)} />}
      <div className="page-head spread" style={{ alignItems: "flex-end" }}>
        <div>
        <div className="eyebrow">Overview · one vehicle · Part 3 observation window</div>
        <h1>Vehicle operation during the observation window</h1>
        <p className="lede">
          Measured from J1939/FMS telemetry of a single Renault T520 ({ts(data.scope.period_start, false)} to{" "}
          {ts(data.scope.period_end, false)}, logger wall clock). These are observations of one vehicle, not fleet-wide figures.
        </p>
        </div>
        <button className="btn primary" onClick={() => setShowAnalyst(true)}>Ask FleetPulse AI</button>
      </div>

      <div className="grid g4">
        <Kpi label="Engine-on time" value={num(k.engine_on_hours, 2)} unit="h" sub={`${s.engine_operation_sessions} engine-operation sessions`} to="/sessions" />
        <Kpi label="Distance" value={num(k.distance_km, 1)} unit="km" sub="integrated wheel speed (Gold)" />
        <Kpi label="Observed fuel" value={num(k.fuel_consumed_l, 1)} unit="L" sub="FMS fuel rate, reconciled with fuel counter" />
        <Kpi label="Engine-operation sessions" value={int(s.engine_operation_sessions)} sub={`≥ 0.1 h engine-on · ${s.recording_sessions_total} recording sessions in total`} to="/sessions" />
      </div>

      <div className="grid g4" style={{ marginTop: 16 }}>
        <Kpi label="Moving" swatch={C.moving} value={num(k.moving_hours, 2)} unit="h" sub="engine on, wheel speed ≥ 0.5 km/h" />
        <Kpi label="Stationary, engine on" swatch={C.stationary} value={num(k.stationary_engine_on_hours, 2)} unit="h"
          sub={`${num(k.stationary_pct_of_engine_on_time, 1)} % of engine-on time`} to="/idle" />
        <Kpi label="Fuel while stationary" swatch={C.stationary} value={num(k.stationary_fuel_consumed_l, 2)} unit="L"
          sub={`${num(k.stationary_fuel_pct, 2)} % of observed fuel`} to="/idle" />
        <Kpi label="Sessions with > 15 min stationary" value={`${int(s.sessions_with_over_15_min_stationary)} / ${int(s.engine_operation_sessions)}`}
          sub="sessions accumulated > 15 min stationary engine-on time" to="/sessions" />
      </div>

      <div className="grid g2 section">
        <Panel title="Engine-on time composition" question="How much of the engine-on time was spent stationary?">
          <EngineTimeComposition movingH={k.moving_hours} stationaryH={k.stationary_engine_on_hours}
            engineOnH={k.engine_on_hours} stationaryPct={k.stationary_pct_of_engine_on_time} />
        </Panel>
        <Panel title="Observed fuel while stationary" question="What share of the observed fuel was used with the vehicle stationary?">
          <ShareBar pct={k.stationary_fuel_pct} color={C.stationary}
            label={`Stationary engine-on · ${num(k.stationary_fuel_consumed_l, 2)} L of ${num(k.fuel_consumed_l, 1)} L`} />
          <p className="small muted" style={{ marginTop: 10, marginBottom: 0 }}>
            Observed consumption. The telemetry does not say whether any of it could have been avoided.
          </p>
        </Panel>
      </div>

      <div className="grid g-2-1 section">
        <Panel title="Solution module #1 · Idle Intelligence" question="Where did the vehicle stand with the engine running, and for how long?"
          right={<Link to="/idle">Open module →</Link>}>
          <div className="grid g3">
            <Kpi label="Stationary engine-on events" value={int(k.stationary_events)} sub="most are short stops between driving" to="/idle/events" />
            <Kpi label="Events > 15 min" value={int(k.stationary_events_over_15_min)} sub="highlighted for review" to="/idle" />
            <Kpi label="Rule" value={data.build.rule_version} sub="rpm ≥ 300 & speed < 0.5 km/h, ≥ 2 s" />
          </div>
          <p className="statement" style={{ marginTop: 14, marginBottom: 0 }}>{data.module_statement}</p>
        </Panel>
        <Panel title="Scope & data notes">
          <dl className="dl">
            <dt>Vehicle</dt><dd>{data.scope.vehicle_id}</dd>
            <dt>Window</dt><dd>Part 3 archive</dd>
            <dt>Timezone</dt><dd>{data.scope.timestamp_timezone}</dd>
            <dt>Recording sessions</dt><dd>{s.recording_sessions_total} ({s.sessions_with_any_engine_on} with any engine-on)</dd>
          </dl>
          {s.short_engine_fragments.length > 0 && (
            <p className="small muted" style={{ marginBottom: 0 }}>
              {s.short_engine_fragments.length} short engine-on fragment outside the {s.engine_operation_sessions} sessions (
              {s.short_engine_fragments.map((f) => `${ts(f.session_start)}, ${duration(f.engine_on_duration_s)} engine-on, ${f.stationary_events} event`).join("; ")}
              ). {s.note}
            </p>
          )}
        </Panel>
      </div>
    </div>
  );
}
