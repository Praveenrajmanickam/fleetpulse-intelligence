import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import AnalystPanel from "../components/AnalystPanel";
import { C } from "../components/charts";
import { Provenance } from "../components/foundation/Provenance";
import { PanelSkeleton } from "../components/foundation/Skeleton";
import { ErrorBox, Kpi, Loading } from "../components/ui";
import VehicleVisualization from "../components/vehicle/VehicleVisualization";
import type { SignalAnnotation } from "../components/vehicle/types";
import { api, signalsApi } from "../lib/api";
import { duration, int, num, ts, useApi } from "../lib/format";

export default function Overview() {
  const { data, error, reload } = useApi(api.overview, []);
  const summary = useApi(api.idleSummary, []);
  const health = useApi(signalsApi.health, []);
  const [showAnalyst, setShowAnalyst] = useState(false);
  const nav = useNavigate();
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return <Loading />;
  const k = data.kpis;
  const s = data.sessions;
  const sm = summary.data;
  const ref = sm?.highlighted_events[0];
  const amb = health.data?.signals.find((x) => x.name === "ambient_temp_c");
  const refCtx = ref ? { kind: "reference_event" as const, eventId: ref.event_id } : undefined;

  const annotations: SignalAnnotation[] = [];
  if (ref && refCtx) {
    annotations.push(
      { signal: "engine_speed_rpm", label: "Avg engine speed", value: num(ref.average_engine_rpm, 1), unit: "rpm", context: refCtx, provenance: "measured" },
      { signal: "fuel_rate_lph", label: "Fuel in event", value: num(ref.fuel_consumed_l, 3), unit: "L", context: refCtx, provenance: "measured" },
      { signal: "coolant_temp_c", label: "Coolant", value: `${num(ref.coolant_start_c, 0)} → ${num(ref.coolant_end_c, 0)}`, unit: "°C", context: refCtx, provenance: "measured" },
      { signal: "wheel_speed_kph", label: "Vehicle state", value: "Stationary", context: refCtx, provenance: "derived" },
    );
  }
  if (amb) {
    annotations.push({ signal: "ambient_temp_c", label: "Ambient temp", value: amb.state === "quality_warning" ? "Quality warning" : "Available",
      context: { kind: "dataset" }, provenance: "derived", quality: amb.state === "quality_warning" ? "warning" : "ok" });
  }
  const dist = sm?.duration_distribution ?? [];
  const maxEvents = Math.max(1, ...dist.map((d) => d.events));

  return (
    <div className="page overview">
      {showAnalyst && <AnalystPanel scope="overall" onClose={() => setShowAnalyst(false)} />}

      {/* ---------- hero: identity + KPIs | digital twin ---------- */}
      <section className="hero">
        <div className="hero-left">
          <div className="eyebrow">Connected vehicle intelligence</div>
          <h1 className="hero-title">FleetPulse Intelligence</h1>
          <p className="hero-sub">Connected Vehicle Operations &amp; Efficiency Intelligence</p>
          <div className="row" style={{ gap: 8, margin: "14px 0 22px" }}>
            <span className="scope-pill">Historical recording · not live telemetry</span>
            <span className="scope-pill">Real J1939/FMS CAN data</span>
          </div>
          <div className="hero-kpis">
            <Kpi label="Engine-on time" value={num(k.engine_on_hours, 2)} unit="h" provenance="measured" to="/sessions" />
            <Kpi label="Distance" value={num(k.distance_km, 1)} unit="km" provenance="measured" />
            <Kpi label="Observed fuel" value={num(k.fuel_consumed_l, 1)} unit="L" provenance="measured" />
            <Kpi label="Engine-operation sessions" value={int(s.engine_operation_sessions)} sub={`${s.recording_sessions_total} recording sessions in total`}
              provenance="derived" to="/sessions" />
          </div>
          <p className="small muted" style={{ marginTop: 16, maxWidth: 520 }}>
            One Renault T520 6x2 · {ts(data.scope.period_start, false)} → {ts(data.scope.period_end, false)} (logger clock, timezone unknown).
            Measured from the truck's FMS CAN interface and processed through Bronze → Silver → Gold.
          </p>
          <div className="row" style={{ marginTop: 18 }}>
            <Link className="btn primary" to="/vehicle">Open Digital Twin</Link>
            <button className="btn" onClick={() => setShowAnalyst(true)}>Ask FleetPulse AI</button>
          </div>
        </div>
        <div className="hero-right">
          <div className="twin-caption">
            <span className="eyebrow">Vehicle · Digital twin</span>
            <span className="small muted">
              {ref ? <>Values from the <Link to={`/idle/events/${encodeURIComponent(ref.event_id)}`}>reference event</Link> ({ts(ref.event_start, false)}, {duration(ref.duration_seconds)}). </> : null}
              Generic tractor illustration. Select a system to explore its signals.
            </span>
          </div>
          <VehicleVisualization variant="hero" annotations={annotations}
            onSystemSelect={(sys) => sys && nav(`/vehicle?system=${sys}`)}
            caption="Generic heavy-duty tractor. Systems link to the Digital Twin." />
        </div>
      </section>

      {/* ---------- observed pattern | event intelligence | investigation ---------- */}
      <section className="grid g3 story">
        <div className="panel pattern">
          <div className="panel-head"><h2>Observed operating pattern</h2><Provenance kind="measured" /></div>
          <div className="big-stats">
            <div><div className="big-num">{num(k.stationary_engine_on_hours, 2)}<small>h</small></div><div className="big-lbl">stationary with engine running</div></div>
            <div><div className="big-num">{num(k.stationary_pct_of_engine_on_time, 1)}<small>%</small></div><div className="big-lbl">of engine-on time</div></div>
            <div><div className="big-num">{num(k.stationary_fuel_consumed_l, 2)}<small>L</small></div><div className="big-lbl">observed fuel during this state ({num(k.stationary_fuel_pct, 2)} %)</div></div>
          </div>
          <div className="composition" role="img" aria-label={`Moving ${num(k.moving_hours, 2)} hours, stationary engine-on ${num(k.stationary_engine_on_hours, 2)} hours`}>
            <div style={{ width: `${(k.moving_hours / k.engine_on_hours) * 100}%`, background: C.moving }} title={`Moving ${num(k.moving_hours, 2)} h`} />
            <div style={{ width: `${(k.stationary_engine_on_hours / k.engine_on_hours) * 100}%`, background: C.stationary }} title={`Stationary ${num(k.stationary_engine_on_hours, 2)} h`} />
          </div>
          <div className="legend" style={{ marginTop: 8 }}>
            <span><i className="swatch" style={{ background: C.moving }} />Moving {num(k.moving_hours, 2)} h</span>
            <span><i className="swatch" style={{ background: C.stationary }} />Stationary, engine on {num(k.stationary_engine_on_hours, 2)} h</span>
          </div>
          <p className="note-line">Observed operating state · not automatically classified as waste. The reason for each stop is not in the telemetry.</p>
        </div>

        <div className="panel">
          <div className="panel-head"><h2>Event intelligence</h2><Provenance kind="derived" /></div>
          {!sm ? <PanelSkeleton height={160} /> : (
            <>
              <div className="big-num" style={{ margin: "6px 0 2px" }}>{int(sm.events_total)}</div>
              <div className="big-lbl">observed stationary engine-on events (≥ 2 s)</div>
              <div className="dist" role="list">
                {dist.map((d) => (
                  <div key={d.duration_class} className="dist-row" role="listitem">
                    <span className="dist-lbl mono">{d.label}</span>
                    <span className="dist-bar"><i style={{ width: `${Math.max(1.5, (d.events / maxEvents) * 100)}%`,
                      background: d.attention_group === "extended" ? C.stationary : C.neutral }} /></span>
                    <span className="dist-val mono">{int(d.events)}</span>
                  </div>
                ))}
              </div>
              <p className="note-line">Most events are short stops between driving. Grey = routine, orange = extended (≥ 5 min).</p>
            </>
          )}
        </div>

        <div className="panel">
          <div className="panel-head"><h2>Investigation</h2><Link to="/idle" className="small">Idle Intelligence →</Link></div>
          {!sm ? <PanelSkeleton height={160} /> : (
            <>
              <div className="inv-stats">
                <Link to="/idle/events?attention_group=extended" className="inv-stat">
                  <span className="big-num">{int(sm.attention_groups.extended.events)}</span><span className="big-lbl">extended events</span>
                </Link>
                <Link to="/sessions" className="inv-stat">
                  <span className="big-num">{int(s.sessions_with_over_15_min_stationary)}<small>/ {int(s.engine_operation_sessions)}</small></span>
                  <span className="big-lbl">sessions &gt; 15 min stationary</span>
                </Link>
              </div>
              <div className="inv-list">
                {sm.highlighted_events.map((e, i) => (
                  <Link key={e.event_id} to={`/idle/events/${encodeURIComponent(e.event_id)}`} className="inv-item">
                    <span className="mono small">{ts(e.event_start, false)}</span>
                    <span className="mono">{duration(e.duration_seconds)}</span>
                    <span className="mono muted small">{num(e.fuel_consumed_l, 3)} L</span>
                    {i === 0 && <span className="tag">Reference</span>}
                  </Link>
                ))}
              </div>
              <div className="row" style={{ marginTop: 12 }}>
                <Link className="btn small-btn" to="/vehicle/trace">Trace CAN → Gold</Link>
                <Link className="btn small-btn" to="/idle/events">Explore events</Link>
              </div>
            </>
          )}
        </div>
      </section>
    </div>
  );
}
