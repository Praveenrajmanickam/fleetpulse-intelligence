import { Link, useSearchParams } from "react-router-dom";
import AnalystPanel from "../components/AnalystPanel";
import { Panel } from "../components/ui";

export default function AiAnalyst() {
  const [sp] = useSearchParams();
  return (
    <div className="page">
      <div className="page-head">
        <div className="eyebrow">FleetPulse AI Analyst · V1</div>
        <h1>Evidence-grounded vehicle intelligence</h1>
        <p className="lede">
          An explanatory layer over deterministic FleetPulse analytics. It is not the measurement engine: every figure comes
          from Gold, answers are checked against the evidence before they are shown, and unsupported claims (fuel waste, driver
          fault, savings, the cause of a stop) are blocked.
        </p>
      </div>
      <div className="grid g-2-1">
        <AnalystPanel scope="overall" inline initialQuestion={sp.get("q")} />
        <div className="grid" style={{ alignContent: "start" }}>
          <Panel title="Ask in context">
            <p className="small" style={{ color: "#c3c2b7", marginTop: 0 }}>
              Open the analyst from an <Link to="/idle/events">event</Link>, a <Link to="/sessions">session</Link> or the{" "}
              <Link to="/idle/scenario">Impact Scenario</Link> to ground it on that evidence.
            </p>
          </Panel>
          <Panel title="How answers are built">
            <ol className="ans-list" style={{ paddingLeft: 18 }}>
              <li>FleetPulse resolves the scope and retrieves compact Gold evidence (no raw CAN, no telemetry tables).</li>
              <li>Scenarios are calculated by the Gold scenario engine first.</li>
              <li>The provider explains the evidence.</li>
              <li>FleetPulse validates numbers, timestamps and claims; a failed answer is retried once, then replaced by a deterministic summary.</li>
              <li>Every request is written to the audit log.</li>
            </ol>
          </Panel>
        </div>
      </div>
    </div>
  );
}
