"""FleetPulse API v1 (FastAPI) - serves Gold Idle Intelligence to the dashboard.

    uvicorn fleetpulse.api.app:app --port 8000
    # env: FLEETPULSE_GOLD_ROOT (default data/gold_part3/gold),
    #      FLEETPULSE_SILVER_ROOT (default data/gold_part3/silver),
    #      FLEETPULSE_FRONTEND_DIST (default frontend/dist, served at / if present)

Gold is the source of truth. This layer selects, filters, paginates and counts
Gold rows; the only calculation is the Gold scenario module (fleetpulse.gold.scenario).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from fleetpulse.ai.audit import AuditStore, InMemoryAuditStore, audit_store_from_env
from fleetpulse.ai.evidence import EvidenceNotFound
from fleetpulse.ai.intents import MAX_QUESTION_CHARS
from fleetpulse.ai.policy import policy_document
from fleetpulse.ai.providers import AIProvider, ProviderUnavailable, resolve_provider, timeout_from_env
from fleetpulse.ai.service import AnalystRequestError, AnalystService, AskRequest
from fleetpulse.api import serving
from fleetpulse.api.repository import GoldRepository, records
from fleetpulse.gold import evidence as gold_evidence
from fleetpulse.gold.config import IdleRuleConfig
from fleetpulse.gold.scenario import Extrapolation, idle_scenario

EVENT_FIELDS = [
    "event_id", "recording_session_id", "event_start", "event_end", "duration_seconds", "duration_class",
    "fuel_consumed_l", "average_fuel_rate_lph", "median_fuel_rate_lph", "maximum_fuel_rate_lph",
    "average_engine_rpm", "average_engine_load_pct", "coolant_start_c", "coolant_end_c",
    "preceded_by", "followed_by", "descriptive_tags", "detection_confidence", "interpretation_status",
]
SESSION_FIELDS = [
    "recording_session_id", "session_start", "session_end", "session_duration_s", "engine_on_duration_s",
    "moving_duration_s", "stationary_engine_on_duration_s", "engine_on_speed_unknown_s", "distance_km",
    "distance_counter_km", "fuel_consumed_l", "fuel_counter_l", "fuel_while_moving_l", "fuel_while_stationary_l",
    "stationary_pct_of_engine_on_time", "stationary_events", "longest_stationary_event_s",
    "stationary_events_over_15_min", "fuel_coverage_pct",
]
SORTABLE = {"event_start", "duration_seconds", "fuel_consumed_l", "average_fuel_rate_lph", "average_engine_rpm",
            "coolant_start_c"}


class ExtrapolationIn(BaseModel):
    observed_period_days: float = Field(gt=0)
    projection_days: float = Field(gt=0)
    assumption_note: str = Field(min_length=1)


class ScenarioIn(BaseModel):
    """No defaults for the assumptions: the operator must supply them."""
    avoidable_pct: float = Field(ge=0, le=100, description="operator assumption, 0-100")
    fuel_price_per_l: float = Field(ge=0, description="operator input")
    currency: str = Field(default="EUR", min_length=1, max_length=8)
    basis: Literal["all_stationary", "events"] = "all_stationary"
    event_ids: list[str] | None = None
    extrapolation: ExtrapolationIn | None = None


class ScenarioAskIn(BaseModel):
    model_config = {"extra": "forbid"}
    avoidable_pct: float | None = Field(default=None, ge=0, le=100)
    fuel_price_per_l: float | None = Field(default=None, ge=0)
    currency: str = Field(default="EUR", min_length=1, max_length=8)
    event_ids: list[str] | None = Field(default=None, max_length=200)


class AskIn(BaseModel):
    """Analyst question. The question is untrusted input; identifiers select Gold evidence."""
    model_config = {"extra": "forbid"}
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    scope: Literal["event", "session", "overall", "scenario"]
    event_id: str | None = Field(default=None, max_length=300)
    session_id: str | None = Field(default=None, max_length=300)
    scenario: ScenarioAskIn | None = None


def create_app(gold_root: Path, silver_root: Path | None, frontend_dist: Path | None = None,
               ai_provider: AIProvider | None | str = "env", audit_store: AuditStore | None = None,
               ai_timeout_s: float | None = None) -> FastAPI:
    """ai_provider: an AIProvider, None (analyst unavailable) or "env" (resolve from FLEETPULSE_AI_* variables)."""
    repo = GoldRepository(gold_root, silver_root)
    if ai_provider == "env":
        provider, reason = resolve_provider()
    else:
        provider, reason = ai_provider, ("No AI provider configured." if ai_provider is None else "ok")
    analyst = AnalystService(repo, provider, audit_store or InMemoryAuditStore(),
                             timeout_s=ai_timeout_s or timeout_from_env(), unavailable_reason=reason)
    app = FastAPI(title="FleetPulse API", version="1.0.0",
                  description="Connected Vehicle Operations & Efficiency Intelligence - Gold serving API")
    app.state.repo = repo
    app.state.analyst = analyst

    def engine_sessions() -> pd.DataFrame:
        s = repo.sessions
        return s[s["engine_on_duration_s"] >= serving.ENGINE_SESSION_MIN_ENGINE_ON_S]

    def scope() -> dict:
        op = repo.operating
        return {"vehicles": 1, "vehicle_id": repo.events["vehicle_id"].iloc[0] if len(repo.events) else None,
                "observation_window": "Part 3", "statement": serving.SCOPE_STATEMENT,
                "period_start": op["period_start"], "period_end": op["period_end"],
                "timestamp_timezone": op["timestamp_timezone"], "gold_scope": op["scope"]}

    def event_view(e: dict) -> dict:
        out = {k: e.get(k) for k in EVENT_FIELDS}
        out["attention_group"] = serving.attention_group(e["duration_class"])
        out["evidence_stored"] = e["event_id"] in repo.evidence
        return out

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "gold_build_id": repo.manifest["gold_build_id"]}

    @app.get("/api/v1/meta")
    def meta():
        return {"build": repo.build_info(), "scope": scope(), "module_statement": serving.MODULE_STATEMENT,
                "modules": [
                    {"id": "overview", "label": "Overview", "available": True},
                    {"id": "idle", "label": "Idle Intelligence", "available": True},
                    {"id": "fuel", "label": "Fuel Intelligence", "available": False},
                    {"id": "engine", "label": "Engine Intelligence", "available": False},
                    {"id": "operations", "label": "Operations", "available": False},
                    {"id": "events", "label": "Events", "available": False},
                    {"id": "ai", "label": "AI Fleet Analyst", "available": False},
                ]}

    @app.get("/api/v1/overview")
    def overview():
        op = repo.operating
        es = engine_sessions()
        all_on = repo.sessions[repo.sessions["engine_on_duration_s"] > 0]
        fragments = all_on[all_on["engine_on_duration_s"] < serving.ENGINE_SESSION_MIN_ENGINE_ON_S]
        kpis = {k: op[k] for k in (
            "engine_on_hours", "moving_hours", "stationary_engine_on_hours", "stationary_pct_of_engine_on_time",
            "distance_km", "fuel_consumed_l", "stationary_fuel_consumed_l", "stationary_fuel_pct",
            "stationary_events", "stationary_events_over_15_min", "sessions_with_over_15_min_stationary")}
        return {
            "scope": scope(),
            "kpis": kpis,
            "sessions": {
                "engine_operation_sessions": int(len(es)),
                "engine_operation_definition": "recording sessions with >= 0.1 h engine-on time (Gold scope)",
                "sessions_with_over_15_min_stationary": op["sessions_with_over_15_min_stationary"],
                "recording_sessions_total": op["recording_sessions"],
                "sessions_with_any_engine_on": op["sessions_with_engine_on"],
                "short_engine_fragments": records(fragments[["recording_session_id", "session_start",
                                                              "engine_on_duration_s", "stationary_events",
                                                              "fuel_consumed_l"]]),
                "note": "Short engine-on fragments are included in every total above.",
            },
            "engine_time_composition_hours": [
                {"state": "moving", "hours": op["moving_hours"]},
                {"state": "stationary_engine_on", "hours": op["stationary_engine_on_hours"]},
            ],
            "fuel_composition_l": [
                {"state": "all_engine_on", "litres": op["fuel_consumed_l"]},
                {"state": "stationary_engine_on", "litres": op["stationary_fuel_consumed_l"]},
            ],
            "module_statement": serving.MODULE_STATEMENT,
            "build": repo.build_info(),
        }

    @app.get("/api/v1/idle/summary")
    def idle_summary():
        ev = repo.events
        counts = ev["duration_class"].value_counts()
        dist = [{"duration_class": c, "label": serving.DURATION_CLASS_LABELS[c], "events": int(counts.get(c, 0)),
                 "attention_group": serving.attention_group(c)} for c in serving.DURATION_CLASS_ORDER]
        extended = ev[ev["duration_class"].isin(serving.ATTENTION_GROUPS["extended"]["duration_classes"])]
        extended = extended.sort_values("duration_seconds", ascending=False)
        es = engine_sessions()
        return {
            "scope": scope(),
            "events_total": int(len(ev)),
            "stationary_engine_on_hours": repo.operating["stationary_engine_on_hours"],
            "stationary_fuel_consumed_l": repo.operating["stationary_fuel_consumed_l"],
            "duration_distribution": dist,
            "attention_groups": {k: {**v, "events": int(sum(counts.get(c, 0) for c in v["duration_classes"]))}
                                 for k, v in serving.ATTENTION_GROUPS.items()},
            "extended_events": [event_view(e) for e in records(extended)],
            "highlighted_events": [event_view(e) for e in records(extended[extended["duration_class"] == "over_15_min"])],
            "sessions": records(es[["recording_session_id", "session_start", "engine_on_duration_s",
                                    "moving_duration_s", "stationary_engine_on_duration_s",
                                    "stationary_pct_of_engine_on_time", "stationary_events",
                                    "longest_stationary_event_s", "fuel_while_stationary_l", "distance_km"]]),
            "module_statement": serving.MODULE_STATEMENT,
            "rule": repo.build_info()["rule_config"],
            "build": repo.build_info(),
        }

    @app.get("/api/v1/idle/events")
    def idle_events(
        page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
        session_id: str | None = None,
        duration_class: list[str] | None = Query(None),
        attention_group: Literal["routine_short", "extended"] | None = None,
        min_duration_s: float | None = Query(None, ge=0), max_duration_s: float | None = Query(None, ge=0),
        min_fuel_l: float | None = Query(None, ge=0), max_fuel_l: float | None = Query(None, ge=0),
        cold_engine: bool | None = None, raised_rpm: bool | None = None,
        preceded_by: str | None = None, followed_by: str | None = None,
        sort: str = "-duration_seconds",
    ):
        ev = repo.events
        m = pd.Series(True, index=ev.index)
        if session_id:
            m &= ev["recording_session_id"] == session_id
        if duration_class:
            bad = set(duration_class) - set(serving.DURATION_CLASS_ORDER)
            if bad:
                raise HTTPException(422, f"unknown duration_class {sorted(bad)}")
            m &= ev["duration_class"].isin(duration_class)
        if attention_group:
            m &= ev["duration_class"].isin(serving.ATTENTION_GROUPS[attention_group]["duration_classes"])
        if min_duration_s is not None:
            m &= ev["duration_seconds"] >= min_duration_s
        if max_duration_s is not None:
            m &= ev["duration_seconds"] <= max_duration_s
        if min_fuel_l is not None:
            m &= ev["fuel_consumed_l"] >= min_fuel_l
        if max_fuel_l is not None:
            m &= ev["fuel_consumed_l"] <= max_fuel_l
        tags = ev["descriptive_tags"].map(lambda t: set(t) if t is not None else set())
        if cold_engine is not None:
            m &= tags.map(lambda t: "cold_engine_at_start" in t) == cold_engine
        if raised_rpm is not None:
            m &= tags.map(lambda t: "raised_engine_speed" in t) == raised_rpm
        if preceded_by:
            m &= ev["preceded_by"] == preceded_by
        if followed_by:
            m &= ev["followed_by"] == followed_by
        key, desc = sort.lstrip("-"), sort.startswith("-")
        if key not in SORTABLE:
            raise HTTPException(422, f"sort must be one of {sorted(SORTABLE)} (prefix '-' for descending)")
        sel = ev[m].sort_values([key, "event_start"], ascending=[not desc, True])
        total = int(len(sel))
        items = sel.iloc[(page - 1) * page_size: page * page_size]
        return {"total": total, "page": page, "page_size": page_size,
                "pages": (total + page_size - 1) // page_size, "sort": sort,
                "items": [event_view(e) for e in records(items)],
                "filter_options": {"duration_class": serving.DURATION_CLASS_ORDER,
                                   "preceded_by": sorted(ev["preceded_by"].unique().tolist()),
                                   "followed_by": sorted(ev["followed_by"].unique().tolist())}}

    @app.get("/api/v1/idle/events/{event_id}")
    def idle_event(event_id: str, context_s: float = Query(120, ge=0, le=1800)):
        e = repo.event(event_id)
        if e is None:
            raise HTTPException(404, f"idle event {event_id!r} not found")
        s = repo.session(e["recording_session_id"])
        start = max(pd.Timestamp(e["event_start"]) - pd.Timedelta(seconds=context_s), pd.Timestamp(s["session_start"]))
        end = min(pd.Timestamp(e["event_end"]) + pd.Timedelta(seconds=context_s), pd.Timestamp(s["session_end"]))
        full = repo.events[repo.events["event_id"] == event_id]
        return {
            "event": {**event_view(e), **{k: v for k, v in records(full)[0].items() if k not in EVENT_FIELDS}},
            "interpretation": {
                "detection_confidence": e["detection_confidence"],
                "detection_statement": serving.DETECTION_STATEMENT[e["detection_confidence"]],
                "interpretation_status": e["interpretation_status"],
                "interpretation_statement": serving.INTERPRETATION_STATEMENT,
            },
            "session": {k: s[k] for k in ("recording_session_id", "session_start", "session_end")},
            "timeline": {**repo.timeline(e["recording_session_id"], start, end),
                         "window_start": start.isoformat(), "window_end": end.isoformat(),
                         "event_start": e["event_start"], "event_end": e["event_end"]},
            "scope": scope(),
            "build": repo.build_info(),
        }

    @app.get("/api/v1/sessions")
    def sessions(scope_filter: Literal["engine_operation", "all"] = Query("engine_operation", alias="scope")):
        s = engine_sessions() if scope_filter == "engine_operation" else repo.sessions
        return {"scope": scope_filter, "definition": "engine_operation = recording sessions with >= 0.1 h engine-on time",
                "total": int(len(s)), "items": records(s[SESSION_FIELDS])}

    @app.get("/api/v1/sessions/{session_id}")
    def session(session_id: str):
        s = repo.session(session_id)
        if s is None:
            raise HTTPException(404, f"recording session {session_id!r} not found")
        ev = repo.events[repo.events["recording_session_id"] == session_id]
        return {
            "session": {k: s[k] for k in SESSION_FIELDS},
            "events": [event_view(e) for e in records(ev)],
            "timeline": repo.timeline(session_id, pd.Timestamp(s["session_start"]), pd.Timestamp(s["session_end"]),
                                      max_points=2000),
            "scope": scope(),
            "build": repo.build_info(),
        }

    @app.post("/api/v1/idle/scenario")
    def scenario(body: ScenarioIn):
        if body.basis == "all_stationary":
            observed = repo.operating["stationary_fuel_consumed_l"]
            basis = {"basis": "all_stationary",
                     "description": "all stationary engine-on fuel in the Part 3 observation window (Gold operating summary)"}
        else:
            if not body.event_ids:
                raise HTTPException(422, "basis 'events' requires event_ids")
            ev = repo.events[repo.events["event_id"].isin(body.event_ids)]
            missing = sorted(set(body.event_ids) - set(ev["event_id"]))
            if missing:
                raise HTTPException(404, f"unknown event_ids {missing}")
            observed = float(ev["fuel_consumed_l"].sum())
            basis = {"basis": "events", "event_ids": body.event_ids,
                     "description": "observed fuel of the selected Gold idle events"}
        extra = Extrapolation(**body.extrapolation.model_dump()) if body.extrapolation else None
        try:
            result = idle_scenario(observed, body.avoidable_pct, body.fuel_price_per_l, body.currency, extra)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {**result, "basis": basis, "calculated_by": "fleetpulse.gold.scenario.idle_scenario",
                "build": repo.build_info()}

    @app.get("/api/v1/ai/evidence/{event_id}")
    def ai_evidence(event_id: str):
        if event_id in repo.evidence:
            return {"provenance": "stored (gold_idle_evidence)", "evidence": repo.evidence[event_id]}
        full = repo.events[repo.events["event_id"] == event_id]
        if full.empty:
            raise HTTPException(404, f"idle event {event_id!r} not found")
        cfg = IdleRuleConfig(**repo.build_info()["rule_config"])
        obj = gold_evidence.evidence_object(records(full)[0], cfg)
        return {"provenance": "generated on request from the Gold event row by fleetpulse.gold.evidence",
                "evidence": obj}

    # --- AI Analyst V1 (explanatory layer; FleetPulse computes) -----------------------
    @app.get("/api/v1/ai/status")
    def ai_status():
        return analyst.status()

    @app.get("/api/v1/ai/policy")
    def ai_policy():
        return policy_document()

    @app.post("/api/v1/ai/ask")
    def ai_ask(body: AskIn):
        req = AskRequest(question=body.question, scope=body.scope, event_id=body.event_id,
                         session_id=body.session_id, scenario=body.scenario.model_dump() if body.scenario else None)
        try:
            return analyst.ask(req)
        except ProviderUnavailable as exc:
            raise HTTPException(503, {"status": "provider_unavailable", "message": str(exc)}) from None
        except EvidenceNotFound as exc:
            raise HTTPException(404, str(exc).strip("'\"")) from None
        except AnalystRequestError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/v1/ai/audit")
    def ai_audit(limit: int = Query(20, ge=1, le=200)):
        return {"records": analyst.audit.recent(limit)}

    if frontend_dist and (frontend_dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            return FileResponse(frontend_dist / "index.html")

    return app


def _default_app() -> FastAPI:
    gold = Path(os.environ.get("FLEETPULSE_GOLD_ROOT", "data/gold_part3/gold"))
    silver = Path(os.environ.get("FLEETPULSE_SILVER_ROOT", "data/gold_part3/silver"))
    dist = Path(os.environ.get("FLEETPULSE_FRONTEND_DIST", "frontend/dist"))
    return create_app(gold, silver if silver.exists() else None, dist, audit_store=audit_store_from_env())


app = _default_app() if os.environ.get("FLEETPULSE_NO_DEFAULT_APP") != "1" else None
