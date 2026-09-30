import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError, isUnavailable } from "../../lib/api";

/** Technical details are available only in development builds, never as the primary message. */
const SHOW_DETAILS = import.meta.env.DEV;

function StatusIcon({ tone }: { tone: "down" | "missing" | "error" }) {
  const stroke = tone === "missing" ? "var(--muted)" : tone === "down" ? "var(--attention)" : "var(--critical)";
  return (
    <svg className="state-icon" viewBox="0 0 40 40" aria-hidden="true">
      <rect x="1" y="1" width="38" height="38" rx="9" fill="var(--sunken)" stroke="var(--border-strong)" />
      {tone === "down" ? (
        <g stroke={stroke} strokeWidth="2" fill="none" strokeLinecap="round">
          <path d="M11 22a13 13 0 0 1 18 0" opacity="0.45" />
          <path d="M15 26a7 7 0 0 1 10 0" />
          <path d="M12 12l16 16" />
        </g>
      ) : tone === "missing" ? (
        <g stroke={stroke} strokeWidth="2" fill="none" strokeLinecap="round">
          <circle cx="18" cy="18" r="7" />
          <path d="M23.5 23.5 29 29" />
        </g>
      ) : (
        <g stroke={stroke} strokeWidth="2" fill="none" strokeLinecap="round">
          <path d="M20 11v11" />
          <path d="M20 28h.01" />
        </g>
      )}
    </svg>
  );
}

function Details({ error }: { error?: Error }) {
  if (!SHOW_DETAILS || !error) return null;
  return (
    <details>
      <summary>Technical details (development build)</summary>
      <pre>{error instanceof ApiError ? `HTTP ${error.status || "—"} · ${error.kind}\n` : ""}{error.message}</pre>
    </details>
  );
}

/** The backend could not be reached. The UI itself is fine; say so plainly and offer a retry. */
export function ApiUnavailable({ onRetry, error, inline = false }: { onRetry?: () => void; error?: Error; inline?: boolean }) {
  return (
    <section className={`state${inline ? " inline" : " center"}`} role="alert" aria-labelledby="api-down-title">
      <StatusIcon tone="down" />
      <div className="eyebrow">Connection</div>
      <h2 className="state-title" id="api-down-title">Backend API unavailable</h2>
      <ul className="state-lines">
        <li><span className="ok" aria-hidden="true">●</span> FleetPulse UI is running</li>
        <li><span className="bad" aria-hidden="true">●</span> Backend API unavailable</li>
        <li><span className="bad" aria-hidden="true">●</span> Operational intelligence could not be loaded</li>
      </ul>
      <p className="small muted" style={{ margin: "0 0 12px" }}>
        Start the API from the repository root (<span className="mono">uvicorn fleetpulse.api.app:app --port 8000</span>) and retry.
        No values are shown while Gold data is unavailable.
      </p>
      {onRetry && <button className="btn primary" onClick={onRetry}>Retry connection</button>}
      <Details error={error} />
    </section>
  );
}

/** A specific resource was not found (e.g. unknown event id). */
export function NotFoundState({ title, message, backTo, backLabel }: { title: string; message: string; backTo?: string; backLabel?: string }) {
  return (
    <section className="state inline" role="status">
      <StatusIcon tone="missing" />
      <h2 className="state-title">{title}</h2>
      <p className="text-2" style={{ margin: "4px 0 12px" }}>{message}</p>
      {backTo && <Link className="btn" to={backTo}>{backLabel ?? "Back"}</Link>}
    </section>
  );
}

/** Any failed request: routes "backend down" to ApiUnavailable, everything else to a calm error panel. */
export function QueryError({ error, onRetry, inline = true, wrap = true }: { error: Error; onRetry?: () => void; inline?: boolean; wrap?: boolean }) {
  const Wrap = ({ children }: { children: ReactNode }) => (wrap ? <div className="page">{children}</div> : <>{children}</>);
  if (isUnavailable(error)) return <Wrap><ApiUnavailable onRetry={onRetry} error={error} inline={inline} /></Wrap>;
  const notFound = error instanceof ApiError && error.kind === "not_found";
  return (
    <Wrap>
      <section className="state inline" role="alert">
        <StatusIcon tone={notFound ? "missing" : "error"} />
        <h2 className="state-title">{notFound ? "Not found" : "This view could not be loaded"}</h2>
        <p className="text-2" style={{ margin: "4px 0 12px" }}>
          {notFound ? error.message : "The API responded, but the request could not be completed."}
        </p>
        {onRetry && <button className="btn" onClick={onRetry}>Try again</button>}
        <Details error={error} />
      </section>
    </Wrap>
  );
}
