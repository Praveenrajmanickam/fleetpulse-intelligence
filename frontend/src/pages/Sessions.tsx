import { useNavigate, useSearchParams } from "react-router-dom";
import { activateOnKey, ErrorBox, Loading } from "../components/ui";
import { api } from "../lib/api";
import { duration, int, num, ts, useApi } from "../lib/format";

export default function Sessions() {
  const [sp, setSp] = useSearchParams();
  const scope = sp.get("scope") === "all" ? "all" : "engine_operation";
  const { data, error, reload } = useApi(() => api.sessions(scope), [scope]);
  const nav = useNavigate();
  return (
    <div className="page">
      <div className="page-head spread" style={{ alignItems: "flex-end" }}>
        <div>
          <div className="eyebrow">Session Explorer</div>
          <h1>Recording sessions</h1>
          <p className="lede">
            A recording session is a continuous stretch of logged data (split at gaps &gt; 1 s). It is not a trip or a shift.
          </p>
        </div>
        <label className="field">Scope
          <select value={scope} onChange={(e) => setSp(e.target.value === "all" ? { scope: "all" } : {}, { replace: true })}>
            <option value="engine_operation">Engine-operation sessions (≥ 0.1 h engine-on)</option>
            <option value="all">All recording sessions</option>
          </select>
        </label>
      </div>
      {error && <ErrorBox error={error} onRetry={reload} embedded />}
      {!data && !error && <Loading embedded />}
      {data && (
        <>
          <p className="small muted">{int(data.total)} sessions · {data.definition}</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Start</th>
                  <th className="num">Engine on</th>
                  <th className="num">Moving</th>
                  <th className="num">Stationary</th>
                  <th className="num">Stationary %</th>
                  <th className="num">Distance km</th>
                  <th className="num">Fuel L</th>
                  <th className="num">Stationary fuel L</th>
                  <th className="num">Events</th>
                  <th className="num">Longest event</th>
                  <th className="num">&gt; 15 min</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((s) => (
                  <tr key={s.recording_session_id} className="clickable" tabIndex={0} aria-label={`Open recording session starting ${ts(s.session_start)}`}
                    onClick={() => nav(`/sessions/${encodeURIComponent(s.recording_session_id)}`)}
                    onKeyDown={activateOnKey(() => nav(`/sessions/${encodeURIComponent(s.recording_session_id)}`))}>
                    <td className="strong mono">{ts(s.session_start)}</td>
                    <td className="num strong">{duration(s.engine_on_duration_s)}</td>
                    <td className="num">{duration(s.moving_duration_s)}</td>
                    <td className="num">{duration(s.stationary_engine_on_duration_s)}</td>
                    <td className="num">{num(s.stationary_pct_of_engine_on_time, 1)}</td>
                    <td className="num">{num(s.distance_km, 1)}</td>
                    <td className="num">{num(s.fuel_consumed_l, 2)}</td>
                    <td className="num">{num(s.fuel_while_stationary_l, 2)}</td>
                    <td className="num">{int(s.stationary_events)}</td>
                    <td className="num">{duration(s.longest_stationary_event_s)}</td>
                    <td className="num">{int(s.stationary_events_over_15_min)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
