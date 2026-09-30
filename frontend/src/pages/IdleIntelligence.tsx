import { useState } from "react";
import { Link } from "react-router-dom";
import AnalystPanel from "../components/AnalystPanel";
import { DurationDistribution, SessionStationaryChart, C } from "../components/charts";
import { ErrorBox, EventTable, Kpi, Loading, Panel } from "../components/ui";
import { api } from "../lib/api";
import { duration, int, neighbour, num, ts, useApi } from "../lib/format";

export default function IdleIntelligence() {
  const { data, error } = useApi(api.idleSummary, []);
  const [showAnalyst, setShowAnalyst] = useState(false);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const g = data.attention_groups;
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

      <div className="grid g4">
        <Kpi label="Stationary engine-on events" value={int(data.events_total)} sub="all detected periods ≥ 2 s" to="/idle/events" />
        <Kpi label="Stationary engine-on time" swatch={C.stationary} value={num(data.stationary_engine_on_hours, 2)} unit="h" />
        <Kpi label="Fuel while stationary" swatch={C.stationary} value={num(data.stationary_fuel_consumed_l, 2)} unit="L" sub="observed" />
        <Kpi label="Events > 15 min" value={int(data.highlighted_events.length)} sub="highlighted below" />
      </div>

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
              style={{ color: "inherit", textDecoration: "none", borderColor: "#5a3526" }}>
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
