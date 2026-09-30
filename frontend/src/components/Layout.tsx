import { NavLink, Outlet } from "react-router-dom";
import { api } from "../lib/api";
import { ts, useApi } from "../lib/format";

const Soon = ({ label }: { label: string }) => (
  <div className="nav-disabled" aria-disabled="true" title="Coming later">
    {label}<span className="soon">Later</span>
  </div>
);

export default function Layout() {
  const meta = useApi(api.meta, []);
  const m = meta.data;
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-name">FleetPulse <span>Intelligence</span></div>
          <div className="brand-tag">Connected Vehicle Operations &amp; Efficiency Intelligence</div>
        </div>
        <nav className="nav">
          <NavLink to="/" end>Overview</NavLink>

          <div className="nav-section">Solution modules</div>
          <NavLink to="/idle" end>Idle Intelligence</NavLink>
          <NavLink to="/idle/events" className="sub">Event Explorer</NavLink>
          <NavLink to="/idle/scenario" className="sub">Impact Scenario</NavLink>
          <Soon label="Fuel Intelligence" />
          <Soon label="Engine Intelligence" />
          <Soon label="Operations" />
          <Soon label="Events" />

          <div className="nav-section">Data</div>
          <NavLink to="/sessions">Session Explorer</NavLink>

          <div className="nav-section">Assistant</div>
          <NavLink to="/ai">
            FleetPulse AI Analyst<span className="soon">V1</span>
          </NavLink>
        </nav>
        <div className="footer">
          {m ? (
            <>
              <div>Gold <b>{m.build.gold_build_id}</b></div>
              <div>Rule <b>{m.build.rule_version}</b> · built {m.build.built_at_utc.slice(0, 16).replace("T", " ")} UTC</div>
              <div>Silver {m.build.silver_build_ids.length} build(s)</div>
            </>
          ) : (
            <div>{meta.error ? "API unavailable" : "…"}</div>
          )}
        </div>
      </aside>
      <div className="main">
        <div className="scopebar">
          <span className="scope-pill">ONE VEHICLE</span>
          <span className="scope-pill">PART 3 OBSERVATION WINDOW</span>
          {m && (
            <>
              <span>{m.scope.vehicle_id}</span>
              <span className="mono">{ts(m.scope.period_start, false)} → {ts(m.scope.period_end, false)}</span>
              <span className="muted">Timestamps: logger wall clock, timezone unknown · not a fleet-wide measurement</span>
            </>
          )}
        </div>
        <Outlet />
      </div>
    </div>
  );
}
