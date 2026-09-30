import { Fragment, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../lib/format";
import { ConfidenceBadge, ErrorBox, InterpretationBadge, Loading } from "./ui";

/** Deterministic evidence object for one event - what a future AI analyst will be given. No LLM call. */
export default function EvidenceDrawer({ eventId, onClose }: { eventId: string; onClose: () => void }) {
  const { data, error, reload } = useApi(() => api.evidence(eventId), [eventId]);
  const [raw, setRaw] = useState(false);
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Evidence object">
        <div className="spread" style={{ marginBottom: 14 }}>
          <div>
            <div className="eyebrow">Evidence object · deterministic</div>
            <h2 style={{ marginTop: 4 }}>What the telemetry supports</h2>
          </div>
          <button className="btn" onClick={onClose}>Close</button>
        </div>
        {error && <ErrorBox error={error} onRetry={reload} embedded />}
        {!data && !error && <Loading embedded />}
        {data && (() => {
          const ev = data.evidence;
          return (
            <>
              <p className="small muted">{data.provenance}</p>
              <div className="ev-block">
                <h3>Measured</h3>
                <dl className="dl">
                  {Object.entries(ev.measured).map(([k, v]) => (
                    <Fragment key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{Array.isArray(v) ? v.join(", ") || "—" : String(v ?? "—")}</dd></Fragment>
                  ))}
                </dl>
              </div>
              <div className="ev-block">
                <h3>Detection rule</h3>
                <p className="small" style={{ margin: 0, color: "var(--text-2)" }}>
                  <span className="mono">{ev.rule.version}</span> · {ev.rule.definition}
                  {ev.rule.thresholds_are_development_defaults && <span className="muted"> (thresholds are development defaults)</span>}
                </p>
              </div>
              <div className="ev-block">
                <h3>Confidence</h3>
                <div className="row" style={{ marginBottom: 6 }}>
                  <ConfidenceBadge level={ev.confidence.detection} />
                  <span className="small muted">{ev.confidence.detection_basis}</span>
                </div>
                <h3 style={{ marginTop: 14 }}>Interpretation</h3>
                <div className="row">
                  <InterpretationBadge status={ev.confidence.interpretation} />
                  <span className="small muted">{ev.confidence.interpretation_basis}</span>
                </div>
              </div>
              <div className="ev-block">
                <h3>Limitations</h3>
                <ul>{ev.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
              </div>
              <div className="ev-block">
                <h3>Claims the evidence supports</h3>
                <ul>{ev.allowed_claims.map((l) => <li key={l}>{l}</li>)}</ul>
              </div>
              <div className="ev-block">
                <h3>Claims the evidence does not support</h3>
                <div className="row" style={{ gap: 6 }}>
                  {ev.prohibited_claims.map((c) => <span className="tag mono" key={c}>{c}</span>)}
                </div>
              </div>
              <div className="ev-block">
                <button className="btn" onClick={() => setRaw(!raw)}>{raw ? "Hide" : "Show"} raw JSON</button>
                {raw && <pre>{JSON.stringify(ev, null, 2)}</pre>}
              </div>
              <div className="row">
                <span className="small muted">This is the deterministic evidence the FleetPulse AI Analyst is grounded on. It does not depend on the analyst.</span>
              </div>
            </>
          );
        })()}
      </aside>
    </>
  );
}
