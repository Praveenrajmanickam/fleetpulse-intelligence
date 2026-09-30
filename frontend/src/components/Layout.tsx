import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api, isUnavailable } from "../lib/api";
import { ts, useApi } from "../lib/format";
import { ApiUnavailable } from "./foundation/ApiState";
import { ErrorBoundary } from "./foundation/ErrorBoundary";

/** Navigation entries. `status` marks modules that are not implemented yet; they are shown, never linked. */
type NavItem = { label: string; to?: string; end?: boolean; sub?: boolean; status?: "soon" | "future"; badge?: string };
const NAV: { section: string; items: NavItem[] }[] = [
  { section: "Overview", items: [{ label: "Overview", to: "/", end: true }] },
  {
    section: "Vehicle",
    items: [
      { label: "Vehicle Digital Twin", to: "/vehicle", end: true },
      { label: "Sensor Explorer", to: "/vehicle/sensors" },
      { label: "Signal Health", to: "/vehicle/signal-health" },
      { label: "CAN → Gold Trace", to: "/vehicle/trace" },
    ],
  },
  {
    section: "Intelligence",
    items: [
      { label: "Idle Intelligence", to: "/idle", end: true },
      { label: "Event Explorer", to: "/idle/events", sub: true },
      { label: "Impact Scenario", to: "/idle/scenario", sub: true },
      { label: "Fuel Intelligence", status: "future" },
      { label: "Engine Intelligence", status: "future" },
    ],
  },
  { section: "Operations", items: [{ label: "Session Explorer", to: "/sessions" }] },
  { section: "Assistant", items: [{ label: "FleetPulse AI Analyst", to: "/ai", badge: "V1" }] },
];

const STATUS_LABEL = { soon: "Soon", future: "Future" } as const;
const STATUS_TITLE = {
  soon: "In development for dashboard V2 - not available yet",
  future: "Future module - not implemented",
} as const;

function BrandGlyph() {
  return (
    <svg className="brand-glyph" viewBox="0 0 26 26" aria-hidden="true">
      <rect x="0.5" y="0.5" width="25" height="25" rx="6" fill="#0e1216" stroke="var(--border-strong)" />
      <path d="M4 14h4.5l2-5 3.5 10 2.5-7 1.5 2H22" fill="none" stroke="var(--accent)" strokeWidth="1.6"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function Layout() {
  const meta = useApi(api.meta, []);
  const location = useLocation();
  const [attempt, setAttempt] = useState(0);
  const m = meta.data;
  const down = !!meta.error && isUnavailable(meta.error);
  const retry = () => {
    meta.reload();
    setAttempt((a) => a + 1);
  };

  return (
    <div className="shell">
      <a className="skip-link" href="#main">Skip to content</a>
      <aside className="sidebar" aria-label="Primary">
        <div className="brand">
          <div className="brand-mark">
            <BrandGlyph />
            <div className="brand-name">FLEETPULSE<span>INTELLIGENCE</span></div>
          </div>
          <div className="brand-tag">Connected Vehicle Operations &amp; Efficiency Intelligence</div>
        </div>
        <nav className="nav" aria-label="Main navigation">
          {NAV.map((group) => (
            <div key={group.section} role="group" aria-labelledby={`nav-${group.section}`}>
              <div className="nav-section" id={`nav-${group.section}`}>{group.section}</div>
              {group.items.map((item) =>
                item.to ? (
                  <NavLink key={item.label} to={item.to} end={item.end} className={item.sub ? "sub" : undefined}>
                    {item.label}
                    {item.badge && <span className="soon">{item.badge}</span>}
                  </NavLink>
                ) : (
                  <div key={item.label} className={`nav-disabled${item.sub ? " sub" : ""}`} aria-disabled="true"
                    title={STATUS_TITLE[item.status ?? "future"]}>
                    <span>{item.label}<span className="sr-only"> ({STATUS_TITLE[item.status ?? "future"]})</span></span>
                    <span className="soon" aria-hidden="true">{STATUS_LABEL[item.status ?? "future"]}</span>
                  </div>
                ),
              )}
            </div>
          ))}
        </nav>
        <div className="footer">
          <div className={`conn ${m ? "ok" : down ? "down" : ""}`} role="status" aria-live="polite">
            <span className="dot" aria-hidden="true" />
            {m ? "API connected" : down ? "API unavailable" : meta.error ? "API error" : "Connecting…"}
          </div>
          {m && (
            <>
              <div>Gold <b>{m.build.gold_build_id}</b></div>
              <div>Rule <b>{m.build.rule_version}</b> · built {m.build.built_at_utc.slice(0, 16).replace("T", " ")} UTC</div>
            </>
          )}
        </div>
      </aside>
      <div className="main">
        <div className="scopebar" aria-label="Data scope">
          <span className="scope-pill">One vehicle</span>
          <span className="scope-pill">Part 3 observation window</span>
          {m ? (
            <>
              <span>{m.scope.vehicle_id}</span>
              <span className="mono">{ts(m.scope.period_start, false)} → {ts(m.scope.period_end, false)}</span>
              <span className="muted">Historical recording · logger wall clock, timezone unknown · not a fleet-wide measurement</span>
            </>
          ) : (
            <span className="muted">Historical recording · not live telemetry</span>
          )}
        </div>
        <main id="main" tabIndex={-1} style={{ outline: "none" }}>
          {down ? (
            <div className="page">
              <ApiUnavailable onRetry={retry} error={meta.error} />
            </div>
          ) : (
            <ErrorBoundary resetKey={location.pathname}>
              <div key={`${location.pathname}#${attempt}`} className="page-enter">
                <Outlet />
              </div>
            </ErrorBoundary>
          )}
        </main>
      </div>
    </div>
  );
}
