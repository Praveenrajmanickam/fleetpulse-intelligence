import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { aiApi, type AiStatus, type AnalystScope, type AskBody, type AskResponse } from "../lib/api";
import { shortSession } from "../lib/format";
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

function AnswerCard({ r }: { r: AskResponse }) {
  const [showEvidence, setShowEvidence] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  return (
    <div className="answer">
      <div className="row" style={{ marginBottom: 8 }}>
        <SourceBadge r={r} />
        <ConfidenceBadge level={r.confidence.detection} />
        <InterpretationBadge status={r.confidence.interpretation} />
      </div>
      <div className="ans-label">
        {r.answer_source === "llm" ? "AI interpretation" : r.answer_source === "mock"
          ? "Mock analyst answer · development output, not an LLM" : "FleetPulse deterministic summary"}
      </div>
      <p className="ans-text">{r.answer}</p>
      <p className="small muted" style={{ marginTop: 4 }}>{STATUS_NOTE[r.status]}</p>

      <div className="ans-label measured">Measured by FleetPulse</div>
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

      <div className="grid g2" style={{ marginTop: 12 }}>
        <div>
          <div className="ans-label">Limitations</div>
          <ul className="ans-list">{r.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
        </div>
        <div>
          <div className="ans-label">Possible next investigation</div>
          <ul className="ans-list">{r.suggested_investigations.map((l) => <li key={l}>{l}</li>)}</ul>
        </div>
      </div>

      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn small-btn" onClick={() => setShowEvidence(!showEvidence)}>{showEvidence ? "Hide" : "Inspect"} evidence used</button>
        <span className="small muted mono">{r.request_id}</span>
      </div>
      {showEvidence && (
        <div className="ev-used">
          <dl className="dl">
            <dt>Evidence</dt>
            <dd className="ev-ids">{r.evidence_ids.map((id) => <span key={id}>{evidenceLink(id)}</span>)}</dd>
            <dt>FleetPulse classification</dt><dd style={{ fontFamily: "inherit" }}>{r.fleetpulse_interpretation.join(" ")}</dd>
            <dt>Gold build</dt><dd>{r.gold_build_id}</dd>
            <dt>Rule / policy</dt><dd>{r.rule_version} · {r.policy_version}</dd>
            <dt>Provider</dt><dd>{r.provider} · {r.model}{r.is_mock ? " (mock)" : ""}</dd>
            <dt>Validation</dt><dd>{r.validation.passed ? "passed" : "not passed"} · {r.validation.attempts} attempt(s)
              {r.validation.issues.length > 0 && <div className="muted small">{r.validation.issues.join(" · ")}</div>}</dd>
            {r.input_flags.length > 0 && (<><dt>Input flags</dt><dd>{r.input_flags.join(", ")}</dd></>)}
            {r.scenario_inputs_source && (<><dt>Scenario inputs</dt><dd>{r.scenario_inputs_source.replace(/_/g, " ")}</dd></>)}
          </dl>
          <button className="btn small-btn" onClick={() => setShowRaw(!showRaw)}>{showRaw ? "Hide" : "Show"} evidence package JSON</button>
          {showRaw && <pre>{JSON.stringify(r.evidence_context, null, 2)}</pre>}
        </div>
      )}
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
    if (turns.length) endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
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
          ? (status.is_mock ? <Badge tone="warning">Mock provider · development</Badge> : <Badge tone="good">{status.provider} · {status.model}</Badge>)
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
