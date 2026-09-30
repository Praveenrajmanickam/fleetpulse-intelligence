import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { aiApi, type AiStatus, type AnalystScope, type AskBody, type AskResponse } from "../lib/api";
import { shortSession } from "../lib/format";
import { Provenance, type ProvenanceKind } from "./foundation/Provenance";
import { Badge, ConfidenceBadge, InterpretationBadge } from "./ui";

const SUGGESTIONS: Record<AnalystScope, string[]> = {
  event: ["Explain this event", "Why was it flagged?", "Can we call this fuel waste?", "What additional data would help?",
    "How much fuel was consumed?"],
  session: ["Summarize this session", "How much stationary operation occurred?", "Which events should I investigate?",
    "Where was stationary fuel consumption concentrated?"],
  overall: ["Summarize vehicle operation", "What should be investigated first?", "Explain stationary fuel consumption",
    "How common were extended events?", "What is the strongest operational finding?"],
  scenario: ["Explain this scenario", "What does this estimate not show?"],
};

const SCOPE_LABEL: Record<AnalystScope, string> = {
  event: "This event", session: "This recording session", overall: "Vehicle · Part 3 observation window", scenario: "This scenario",
};

interface Turn { q: string; r?: AskResponse; error?: string }

export interface AnalystContext {
  scope: AnalystScope;
  eventId?: string;
  sessionId?: string;
  scenario?: AskBody["scenario"];
}

function SourceBadge({ r }: { r: AskResponse }) {
  if (r.answer_source === "mock") return <Badge tone="warning">Development mock · not an LLM</Badge>;
  if (r.answer_source === "deterministic_fallback") return <Badge tone="info">Deterministic FleetPulse summary</Badge>;
  return <Badge tone="neutral">AI interpretation · {r.model}</Badge>;
}

const STATUS_NOTE: Record<AskResponse["status"], string> = {
  answered: "Validated against the evidence.",
  answered_after_correction: "Validated after one corrective retry.",
  fallback_validation_failed: "The model answer failed validation; FleetPulse returned a deterministic summary instead.",
  fallback_provider_error: "The analyst provider did not respond; FleetPulse returned a deterministic summary instead.",
};

function evidenceLink(id: string) {
  const [kind, ...rest] = id.split(":");
  const ref = rest.join(":");
  if (kind === "event") return <Link to={`/idle/events/${encodeURIComponent(ref)}`}>event {ref.split("-idle-")[1] ?? ref}</Link>;
  if (kind === "session") return <Link to={`/sessions/${encodeURIComponent(ref)}`}>session {shortSession(ref)}</Link>;
  return <span className="mono">{id}</span>;
}

function Section({ n, title, prov, children, open = true }: { n: number; title: string; prov?: ProvenanceKind; children: ReactNode; open?: boolean }) {
  return (
    <details className="ans-sec" open={open}>
      <summary>
        <span className="ans-n mono">{n}</span>
        <span className="ans-t">{title}</span>
        {prov && <Provenance kind={prov} />}
      </summary>
      <div className="ans-body">{children}</div>
    </details>
  );
}

function AnswerCard({ r }: { r: AskResponse }) {
  const [showRaw, setShowRaw] = useState(false);
  const interp = r.answer_source === "llm" ? `AI interpretation · ${r.model}` : r.answer_source === "mock"
    ? "Mock analyst answer · development output, not an LLM" : "FleetPulse deterministic summary";
  return (
    <div className="answer">
      {r.is_mock && <div className="mock-banner" role="note">Development mock · not an LLM</div>}
      <div className="row" style={{ marginBottom: 10 }}>
        {!r.is_mock && <SourceBadge r={r} />}
        <ConfidenceBadge level={r.confidence.detection} />
        <InterpretationBadge status={r.confidence.interpretation} />
      </div>

      <Section n={1} title="Measured by FleetPulse" prov="measured">
        <table className="facts">
          <tbody>
            {r.measured_facts.map((f) => (
              <tr key={f.id}>
                <td>{f.label}</td>
                <td className="mono">{f.display}</td>
                <td className="muted small">{f.source === "gold" ? "Gold" : f.source === "derived_from_gold" ? "derived from Gold" : "Gold scenario engine"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section n={2} title="Interpretation" prov={r.answer_source === "llm" ? "ai" : "derived"}>
        <div className="ans-label">{interp}</div>
        <p className="ans-text">{r.answer}</p>
        <div className="ans-label" style={{ marginTop: 12 }}>FleetPulse classification</div>
        <p className="small text-2" style={{ margin: 0 }}>{r.fleetpulse_interpretation.join(" ")}</p>
      </Section>

      <Section n={3} title="Limitations">
        <ul className="ans-list">{r.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
      </Section>

      <Section n={4} title="What to investigate next">
        <ul className="ans-list">{r.suggested_investigations.map((l) => <li key={l}>{l}</li>)}</ul>
      </Section>

      <Section n={5} title="Evidence" open={false}>
        <div className="ev-ids">{r.evidence_ids.map((id) => <span key={id}>{evidenceLink(id)}</span>)}</div>
        <dl className="dl" style={{ marginTop: 10 }}>
          <dt>Gold build</dt><dd>{r.gold_build_id}</dd>
          <dt>Rule / policy</dt><dd>{r.rule_version} · {r.policy_version}</dd>
          {r.scenario_inputs_source && (<><dt>Scenario inputs</dt><dd>{r.scenario_inputs_source.replace(/_/g, " ")}</dd></>)}
        </dl>
        <button className="btn small-btn" style={{ marginTop: 8 }} onClick={() => setShowRaw(!showRaw)}>{showRaw ? "Hide" : "Show"} evidence package JSON</button>
        {showRaw && <pre>{JSON.stringify(r.evidence_context, null, 2)}</pre>}
      </Section>

      <Section n={6} title="Validation" open={false}>
        <p className={`small ${r.validation.passed ? "" : "text-2"}`} style={{ margin: 0 }}>
          <span aria-hidden="true">{r.validation.passed ? "✓ " : "↺ "}</span>{STATUS_NOTE[r.status]}
        </p>
        <dl className="dl" style={{ marginTop: 8 }}>
          <dt>Provider</dt><dd>{r.provider} · {r.model}{r.is_mock ? " (mock)" : ""}</dd>
          <dt>Attempts</dt><dd>{r.validation.attempts}</dd>
          <dt>Request</dt><dd>{r.request_id}</dd>
          {r.input_flags.length > 0 && (<><dt>Input flags</dt><dd>{r.input_flags.join(", ")}</dd></>)}
        </dl>
        {r.validation.issues.length > 0 && <ul className="ans-list" style={{ marginTop: 6 }}>{r.validation.issues.map((x) => <li key={x}>{x}</li>)}</ul>}
      </Section>
    </div>
  );
}

/** FleetPulse AI Analyst. Stateless per question: every answer is grounded only on the evidence for its scope. */
export default function AnalystPanel(props: AnalystContext & { onClose?: () => void; inline?: boolean; initialQuestion?: string | null }) {
  const [status, setStatus] = useState<AiStatus>();
  const [statusErr, setStatusErr] = useState<string>();
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    aiApi.status().then(setStatus).catch((e) => setStatusErr(e.message));
  }, []);
  const asked = useRef(false);
  useEffect(() => {
    if (status?.available && props.initialQuestion && !asked.current) {
      asked.current = true;
      ask(props.initialQuestion.slice(0, status.max_question_chars));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);
  useEffect(() => {
    if (turns.length) endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  }, [turns.length]);

  const max = status?.max_question_chars ?? 1000;
  const available = !!status?.available;
  const ask = async (question: string) => {
    if (!question.trim() || busy || !available) return;
    setBusy(true);
    setQ("");
    setTurns((t) => [...t, { q: question }]);
    try {
      const r = await aiApi.ask({ question, scope: props.scope, event_id: props.eventId, session_id: props.sessionId, scenario: props.scenario });
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, r } : x)));
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, error: (e as Error).message } : x)));
    } finally {
      setBusy(false);
    }
  };

  const body = (
    <>
      <div className="spread" style={{ marginBottom: 6 }}>
        <div>
          <div className="eyebrow">Evidence-grounded vehicle intelligence</div>
          <h2 style={{ marginTop: 4, fontSize: 18 }}>FleetPulse AI Analyst</h2>
        </div>
        {props.onClose && <button className="btn" onClick={props.onClose}>Close</button>}
      </div>
      <div className="row small" style={{ marginBottom: 10 }}>
        <span className="scope-pill">{SCOPE_LABEL[props.scope]}</span>
        {status && (status.available
          ? (status.is_mock ? <Badge tone="warning">Development mock · not an LLM</Badge> : <Badge tone="good">{status.provider} · {status.model}</Badge>)
          : <Badge tone="serious">Analyst unavailable</Badge>)}
      </div>
      <p className="statement small" style={{ marginTop: 0 }}>
        The AI explains; FleetPulse computes. Every number is taken from Gold evidence and checked before it is shown.
        The analyst cannot see why the vehicle was stationary.
      </p>
      {statusErr && <p className="error" style={{ padding: 0 }}>Analyst status unavailable: {statusErr}</p>}
      {status && !status.available && (
        <p className="statement caution small">{status.message} The dashboard and the deterministic evidence remain available.</p>
      )}

      <div className="turns">
        {turns.map((t, i) => (
          <div key={i} className="turn">
            <div className="q">{t.q}</div>
            {t.r && <AnswerCard r={t.r} />}
            {t.error && <p className="error" style={{ padding: 0 }}>{t.error}</p>}
            {!t.r && !t.error && <p className="muted small">Retrieving Gold evidence and validating the answer…</p>}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <div className="suggest">
        {SUGGESTIONS[props.scope].map((s) => (
          <button key={s} className="chip" disabled={!available || busy} onClick={() => ask(s)}>{s}</button>
        ))}
      </div>
      <form className="ask-row" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
        <input value={q} maxLength={max} disabled={!available || busy}
          placeholder={available ? "Ask about this evidence…" : "Analyst provider unavailable"} onChange={(e) => setQ(e.target.value)} />
        <button className="btn primary" disabled={!available || busy || !q.trim()}>{busy ? "…" : "Ask"}</button>
      </form>
      <div className="small muted" style={{ textAlign: "right" }}>{q.length} / {max}</div>
    </>
  );

  if (props.inline) return <section className="panel analyst-inline">{body}</section>;
  return (
    <>
      <div className="drawer-backdrop" onClick={props.onClose} />
      <aside className="drawer analyst" role="dialog" aria-label="FleetPulse AI Analyst">{body}</aside>
    </>
  );
}
