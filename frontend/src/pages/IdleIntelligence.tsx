import { useState } from "react";
import { Link } from "react-router-dom";
import AnalystPanel from "../components/AnalystPanel";
import { DurationDistribution, SessionStationaryChart, C } from "../components/charts";
import { Provenance } from "../components/foundation/Provenance";
import { ErrorBox, EventTable, Loading, Panel } from "../components/ui";
import { api } from "../lib/api";
import { duration, int, neighbour, num, ts, useApi } from "../lib/format";

export default function IdleIntelligence() {
  const { data, error, reload } = useApi(api.idleSummary, []);
  const [showAnalyst, setShowAnalyst] = useState(false);
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return <Loading />;
  const g = data.attention_groups;
  const rule = data.rule as Record<string, number | string>;
  const dist = (c: string) => int(data.duration_distribution.find((d) => d.duration_class === c)?.events ?? 0);
  return (
    <div className="page">
      {showAnalyst && <AnalystPanel scope="overall" onClose={() => setShowAnalyst(false)} />}
      <div className="page-head spread" style={{ alignItems: "flex-end" }}>
        <div>
          <div className="eyebrow">Solution module #1 · Idle Intelligence · rule {data.build.rule_version}</div>
          <h1>Stationary engine-on operation</h1>
          <p className="lede">{data.module_statement}</p>
        </div>
        <button className="btn primary" onClick={() => setShowAnalyst(true)}>Ask FleetPulse AI</button>
      </div>

      {/* story: routine -> extended -> investigation -> evidence */}
      <ol className="funnel" aria-label="From observations to evidence">
        <li className="funnel-step">
          <span className="f-k">Observed</span>
          <span className="f-n mono">{int(data.events_total)}</span>
          <span className="f-l">stationary engine-on events ≥ 2 s · {num(data.stationary_engine_on_hours, 2)} h · {num(data.stationary_fuel_consumed_l, 2)} L observed</span>
          <Provenance kind="measured" />
        </li>
        <li className="funnel-step routine">
          <span className="f-k">Routine observations</span>
          <span className="f-n mono">{int(g.routine_short.events)}</span>
          <span className="f-l">{dist("under_1_min")} under 1 min · {dist("1_to_5_min")} of 1–5 min. Short stops, reported for completeness, not flagged.</span>
          <Provenance kind="derived" />
        </li>
        <li className="funnel-step extended">
          <span className="f-k">Extended events</span>
          <span className="f-n mono">{int(g.extended.events)}</span>
          <span className="f-l">{dist("5_to_15_min")} of 5–15 min · {dist("over_15_min")} over 15 min. Worth reviewing with operational context.</span>
          <Link to="/idle/events?attention_group=extended" className="small">Open list →</Link>
        </li>
        <li className="funnel-step investigate">
          <span className="f-k">Investigation</span>
          <span className="f-n mono">{int(data.highlighted_events.length)}</span>
          <span className="f-l">longest events (&gt; 15 min) highlighted below. Reason unknown: no GPS, job or PTO context.</span>
        </li>
        <li className="funnel-step evidence">
          <span className="f-k">Evidence</span>
          <span className="f-n mono">CAN</span>
          <span className="f-l">each event traces to Silver telemetry and the source CAN frames.</span>
          <Link to="/vehicle/trace" className="small">Trace →</Link>
        </li>
      </ol>

      <details className="rule-panel">
        <summary>
          <span className="mono">idle-v1</span> detection rule · FleetPulse V1 defaults, configurable, derived from this dataset (not industry standards)
        </summary>
        <div className="rule-body">
          <div className="rule-expr mono">
            engine_speed_rpm ≥ {String(rule.engine_on_min_rpm ?? "—")} rpm<br />
            AND wheel_speed_kph &lt; {String(rule.stationary_max_kph ?? "—")} km/h<br />
            both <b>fresh</b> and <b>valid</b> on every 100 ms row<br />
            continuous for ≥ {String(rule.min_event_s ?? "—")} s within one recording session
          </div>
          <ul className="ans-list">
            <li>Broken by movement, engine off, unknown/stale signals, grid holes or session end.</li>
            <li>Fuel = LFE SPN 183 fuel rate integrated over the event; reconciled with the SPN 5054 counter per session.</li>
            <li>Tags are measured facts, not causes: cold engine (coolant &lt; {String(rule.cold_coolant_c ?? "—")} °C at start), raised engine speed (mean &gt; {String(rule.raised_idle_rpm ?? "—")} rpm).</li>
            <li>Duration classes: &lt; 1 min, 1–5 min, 5–15 min, &gt; 15 min. "Extended" = 5 min or longer.</li>
          </ul>
          <p className="small muted" style={{ margin: 0 }}>Rule values shown from the Gold build configuration ({data.build.gold_build_id}).</p>
        </div>
      </details>

      <div className="grid g2 section">
        <Panel title="Event length distribution" question="Are stationary periods mostly short stops, or long stands?">
          <DurationDistribution data={data.duration_distribution} />
        </Panel>
        <Panel title="Attention groups" question="Which events are routine, and which merit a look with operational context?">
          <div className="grid g2">
            <div className="kpi">
              <div className="kpi-label"><span className="swatch" style={{ background: C.neutral }} />{g.routine_short.label}</div>
              <div className="kpi-value">{int(g.routine_short.events)}</div>
              <div className="kpi-sub">{g.routine_short.description}</div>
            </div>
            <Link to="/idle/events?attention_group=extended" className="kpi link" style={{ color: "inherit", textDecoration: "none" }}>
              <div className="kpi-label"><span className="swatch" style={{ background: C.stationary }} />{g.extended.label}</div>
              <div className="kpi-value">{int(g.extended.events)}</div>
              <div className="kpi-sub">{g.extended.description}</div>
            </Link>
          </div>
          <p className="small muted" style={{ marginBottom: 0 }}>
            Grouping by Gold duration class only. Being in the extended group is not a judgement about the event.
          </p>
        </Panel>
      </div>

      <div className="section">
        <div className="section-title"><h2>Longest events · over 15 minutes</h2><span className="count">{data.highlighted_events.length} events</span></div>
        <div className="grid g3">
          {data.highlighted_events.map((e) => (
            <Link key={e.event_id} to={`/idle/events/${encodeURIComponent(e.event_id)}`} className="kpi link"
              style={{ color: "inherit", textDecoration: "none", borderColor: "rgba(217, 89, 38, 0.45)" }}>
              <div className="kpi-label"><span className="swatch" style={{ background: C.stationary }} />{ts(e.event_start)}</div>
              <div className="kpi-value">{duration(e.duration_seconds)}</div>
              <div className="kpi-sub mono">
                {num(e.fuel_consumed_l, 3)} L · {num(e.average_engine_rpm, 0)} rpm · coolant {num(e.coolant_start_c, 0)}→{num(e.coolant_end_c, 0)} °C
              </div>
              <div className="kpi-sub">{neighbour(e.preceded_by)} before · {neighbour(e.followed_by)} after · context unknown</div>
            </Link>
          ))}
        </div>
      </div>

      <div className="section">
        <div className="section-title">
          <h2>Extended events for investigation</h2><span className="count">{data.extended_events.length} events ≥ 5 min</span>
          <Link to="/idle/events" style={{ marginLeft: "auto" }} className="small">All {data.events_total} events in Event Explorer →</Link>
        </div>
        <EventTable events={data.extended_events} showSession compact />
      </div>

      <div className="section">
        <Panel title="Stationary vs moving time by session" question="Which engine-operation sessions carried the most stationary engine-on time?">
          <SessionStationaryChart sessions={data.sessions} />
        </Panel>
      </div>
    </div>
  );
}
