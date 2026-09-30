"""Dashboard V1 API contract tests (served from the Part 3 Gold build)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from fleetpulse.api import serving  # noqa: E402
from fleetpulse.api.app import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data" / "gold_part3" / "gold"
SILVER = ROOT / "data" / "gold_part3" / "silver"
LONGEST = "turku-renault-t520-6x2-20210119T092357960305-idle-092402900000"

pytestmark = pytest.mark.skipif(not (GOLD / "_builds").exists(), reason="Part 3 Gold build not present")


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(GOLD, SILVER if SILVER.exists() else None))


@pytest.fixture(scope="module")
def overview(client):
    r = client.get("/api/v1/overview")
    assert r.status_code == 200
    return r.json()


@pytest.fixture(scope="module")
def summary(client):
    r = client.get("/api/v1/idle/summary")
    assert r.status_code == 200
    return r.json()


def _all_strings(obj, skip_keys=()):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in skip_keys:
                yield from _all_strings(v, skip_keys)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_strings(v, skip_keys)


# --- overview ---------------------------------------------------------------------

def test_overview_serves_gold_values_unchanged(client, overview):
    op = client.app.state.repo.operating
    k = overview["kpis"]
    for key in k:
        assert k[key] == op[key], key
    assert round(k["engine_on_hours"], 2) == 21.98
    assert round(k["distance_km"], 1) == 604.7
    assert round(k["fuel_consumed_l"], 1) == 196.7
    assert round(k["moving_hours"], 2) == 13.64
    assert round(k["stationary_engine_on_hours"], 2) == 8.33
    assert round(k["stationary_pct_of_engine_on_time"], 1) == 37.9
    assert round(k["stationary_fuel_consumed_l"], 2) == 19.60
    assert round(k["stationary_fuel_pct"], 2) == 9.96


def test_overview_scope_is_one_vehicle(overview):
    s = overview["scope"]
    assert s["vehicles"] == 1 and s["observation_window"] == "Part 3"
    assert "Not a fleet-wide" in s["statement"]
    assert "unknown" in s["timestamp_timezone"]


def test_overview_sessions(overview):
    s = overview["sessions"]
    assert s["engine_operation_sessions"] == 17
    assert s["sessions_with_over_15_min_stationary"] == 11
    assert s["sessions_with_any_engine_on"] == 18
    assert s["recording_sessions_total"] == 46
    assert len(s["short_engine_fragments"]) == 1


def test_overview_carries_build_lineage(overview):
    b = overview["build"]
    assert b["gold_build_id"] and b["rule_version"] == "idle-v1"


# --- idle summary -----------------------------------------------------------------

def test_idle_summary_distribution(summary):
    assert summary["events_total"] == 880
    dist = {d["duration_class"]: d["events"] for d in summary["duration_distribution"]}
    assert dist == {"under_1_min": 778, "1_to_5_min": 86, "5_to_15_min": 13, "over_15_min": 3}
    assert [d["duration_class"] for d in summary["duration_distribution"]] == serving.DURATION_CLASS_ORDER


def test_idle_summary_attention_groups(summary):
    g = summary["attention_groups"]
    assert g["routine_short"]["events"] == 864 and g["extended"]["events"] == 16
    assert len(summary["extended_events"]) == 16
    assert all(e["attention_group"] == "extended" for e in summary["extended_events"])
    hl = summary["highlighted_events"]
    assert len(hl) == 3 and all(e["duration_class"] == "over_15_min" for e in hl)
    assert hl[0]["event_id"] == LONGEST


def test_idle_summary_sessions_are_engine_operation_sessions(summary):
    assert len(summary["sessions"]) == 17
    assert all(s["engine_on_duration_s"] >= serving.ENGINE_SESSION_MIN_ENGINE_ON_S for s in summary["sessions"])


# --- event list: pagination / filtering -------------------------------------------

def test_events_pagination_is_complete_and_disjoint(client):
    ids = []
    for page in range(1, 10):
        r = client.get("/api/v1/idle/events", params={"page": page, "page_size": 100, "sort": "event_start"}).json()
        assert r["total"] == 880 and r["pages"] == 9
        ids += [e["event_id"] for e in r["items"]]
    assert len(ids) == 880 and len(set(ids)) == 880
    last = client.get("/api/v1/idle/events", params={"page": 10, "page_size": 100}).json()
    assert last["items"] == []


def test_events_sorting(client):
    r = client.get("/api/v1/idle/events", params={"page_size": 5, "sort": "-duration_seconds"}).json()
    d = [e["duration_seconds"] for e in r["items"]]
    assert d == sorted(d, reverse=True) and r["items"][0]["event_id"] == LONGEST
    r = client.get("/api/v1/idle/events", params={"page_size": 5, "sort": "fuel_consumed_l"}).json()
    f = [e["fuel_consumed_l"] for e in r["items"]]
    assert f == sorted(f)


@pytest.mark.parametrize("params,check", [
    ({"duration_class": "over_15_min"}, lambda e: e["duration_class"] == "over_15_min"),
    ({"attention_group": "extended"}, lambda e: e["attention_group"] == "extended"),
    ({"min_duration_s": 60, "max_duration_s": 300}, lambda e: 60 <= e["duration_seconds"] <= 300),
    ({"min_fuel_l": 0.1}, lambda e: e["fuel_consumed_l"] >= 0.1),
    ({"cold_engine": "true"}, lambda e: "cold_engine_at_start" in e["descriptive_tags"]),
    ({"raised_rpm": "true"}, lambda e: "raised_engine_speed" in e["descriptive_tags"]),
    ({"preceded_by": "engine_off"}, lambda e: e["preceded_by"] == "engine_off"),
    ({"followed_by": "driving"}, lambda e: e["followed_by"] == "driving"),
])
def test_events_filters(client, params, check):
    r = client.get("/api/v1/idle/events", params={**params, "page_size": 500}).json()
    assert r["total"] > 0
    assert all(check(e) for e in r["items"])


def test_events_filter_counts_match_gold(client):
    ev = client.app.state.repo.events
    r = client.get("/api/v1/idle/events", params={"duration_class": ["5_to_15_min", "over_15_min"]}).json()
    assert r["total"] == 16
    sid = ev["recording_session_id"].iloc[0]
    r = client.get("/api/v1/idle/events", params={"session_id": sid}).json()
    assert r["total"] == int((ev["recording_session_id"] == sid).sum())
    cold_true = client.get("/api/v1/idle/events", params={"cold_engine": "true"}).json()["total"]
    cold_false = client.get("/api/v1/idle/events", params={"cold_engine": "false"}).json()["total"]
    assert cold_true + cold_false == 880


@pytest.mark.parametrize("params", [
    {"page": 0}, {"page_size": 0}, {"page_size": 10_000}, {"sort": "driver"},
    {"duration_class": "very_long"}, {"attention_group": "wasteful"}, {"min_fuel_l": -1},
])
def test_events_invalid_params_rejected(client, params):
    assert client.get("/api/v1/idle/events", params=params).status_code == 422


# --- event detail / evidence / invalid ids ----------------------------------------

def test_event_detail_longest(client):
    r = client.get(f"/api/v1/idle/events/{LONGEST}")
    assert r.status_code == 200
    d = r.json()
    e = d["event"]
    assert e["duration_seconds"] == pytest.approx(1169.2)
    assert round(e["fuel_consumed_l"], 3) == 0.626
    assert round(e["average_engine_rpm"], 1) == 600.1
    assert (e["coolant_start_c"], e["coolant_end_c"]) == (60.0, 63.0)
    assert (e["preceded_by"], e["followed_by"]) == ("engine_off", "driving")
    i = d["interpretation"]
    assert i["detection_confidence"] == "high" and i["interpretation_status"] == "context_unknown"
    assert i["detection_statement"] == "FleetPulse detected the vehicle state with high confidence."
    assert i["interpretation_statement"] == "The available telemetry does not identify why the vehicle remained stationary."
    assert e["gold_build_id"] and e["rule_version"] == "idle-v1"


@pytest.mark.skipif(not SILVER.exists(), reason="Silver not present")
def test_event_timeline_window(client):
    d = client.get(f"/api/v1/idle/events/{LONGEST}").json()["timeline"]
    assert d["available"] and 0 < len(d["points"]) <= 1500
    ts = [p["t"] for p in d["points"]]
    assert ts == sorted(ts)
    assert d["window_start"] <= d["event_start"] and d["window_end"] >= d["event_end"]
    assert ts[0] >= d["window_start"] and ts[-1] <= d["window_end"]


@pytest.mark.parametrize("path", [
    "/api/v1/idle/events/does-not-exist", "/api/v1/ai/evidence/does-not-exist",
    "/api/v1/sessions/does-not-exist", f"/api/v1/idle/events/{LONGEST}x",
])
def test_unknown_ids_return_404(client, path):
    r = client.get(path)
    assert r.status_code == 404 and "not found" in r.json()["detail"]


def test_evidence_stored_and_generated(client):
    stored = client.get(f"/api/v1/ai/evidence/{LONGEST}").json()
    assert stored["provenance"].startswith("stored")
    ev = stored["evidence"]
    for key in ("measured", "rule", "confidence", "limitations", "prohibited_claims"):
        assert key in ev
    repo = client.app.state.repo
    other = next(i for i in repo.events["event_id"] if i not in repo.evidence)
    gen = client.get(f"/api/v1/ai/evidence/{other}").json()
    assert gen["provenance"].startswith("generated")
    assert gen["evidence"]["evidence_id"] == other
    assert gen["evidence"]["confidence"]["interpretation"] == "context_unknown"
    assert set(gen["evidence"]) == set(ev)


# --- sessions ---------------------------------------------------------------------

def test_sessions_scopes(client):
    eng = client.get("/api/v1/sessions").json()
    assert eng["total"] == 17 and len(eng["items"]) == 17
    assert client.get("/api/v1/sessions", params={"scope": "all"}).json()["total"] == 46
    assert client.get("/api/v1/sessions", params={"scope": "fleet"}).status_code == 422


def test_session_detail_traceability(client):
    sid = LONGEST.rsplit("-idle-", 1)[0]
    d = client.get(f"/api/v1/sessions/{sid}").json()
    s = d["session"]
    assert s["recording_session_id"] == sid
    assert len(d["events"]) == s["stationary_events"]
    assert LONGEST in [e["event_id"] for e in d["events"]]
    if SILVER.exists():
        assert 0 < len(d["timeline"]["points"]) <= 2000


def test_session_event_counts_sum_to_gold(client):
    items = client.get("/api/v1/sessions", params={"scope": "all"}).json()["items"]
    assert sum(s["stationary_events"] for s in items) == 880


# --- scenario ---------------------------------------------------------------------

def test_scenario_uses_gold_module_and_observed_basis(client):
    r = client.post("/api/v1/idle/scenario", json={"avoidable_pct": 25, "fuel_price_per_l": 1.8})
    assert r.status_code == 200
    s = r.json()
    observed = client.app.state.repo.operating["stationary_fuel_consumed_l"]
    assert s["label"].startswith("SCENARIO / ESTIMATE")
    assert s["calculated_by"] == "fleetpulse.gold.scenario.idle_scenario"
    assert s["basis"]["basis"] == "all_stationary"
    assert s["scenario_avoidable_fuel_l"] == pytest.approx(observed * 0.25, abs=1e-3)
    assert s["scenario_cost_exposure"] == pytest.approx(observed * 0.25 * 1.8, abs=1e-3)


@pytest.mark.parametrize("body", [
    {}, {"avoidable_pct": 10}, {"fuel_price_per_l": 1.8},
    {"avoidable_pct": -1, "fuel_price_per_l": 1.8}, {"avoidable_pct": 101, "fuel_price_per_l": 1.8},
    {"avoidable_pct": 10, "fuel_price_per_l": -0.5}, {"avoidable_pct": "lots", "fuel_price_per_l": 1.8},
    {"avoidable_pct": 10, "fuel_price_per_l": 1.8, "basis": "events"},
    {"avoidable_pct": 10, "fuel_price_per_l": 1.8, "basis": "fleet"},
    {"avoidable_pct": 10, "fuel_price_per_l": 1.8,
     "extrapolation": {"observed_period_days": 10, "projection_days": 365, "assumption_note": ""}},
])
def test_scenario_requires_valid_user_inputs(client, body):
    assert client.post("/api/v1/idle/scenario", json=body).status_code == 422


def test_scenario_zero_and_full(client):
    zero = client.post("/api/v1/idle/scenario", json={"avoidable_pct": 0, "fuel_price_per_l": 1.8}).json()
    assert zero["scenario_avoidable_fuel_l"] == 0 and zero["scenario_cost_exposure"] == 0
    observed = client.app.state.repo.operating["stationary_fuel_consumed_l"]
    full = client.post("/api/v1/idle/scenario", json={"avoidable_pct": 100, "fuel_price_per_l": 2}).json()
    assert full["scenario_avoidable_fuel_l"] == pytest.approx(observed, abs=1e-3)


def test_scenario_on_selected_events(client):
    repo = client.app.state.repo
    hl = repo.events.nlargest(3, "duration_seconds")
    body = {"avoidable_pct": 50, "fuel_price_per_l": 2.0, "basis": "events", "event_ids": hl["event_id"].tolist()}
    s = client.post("/api/v1/idle/scenario", json=body).json()
    assert s["scenario_avoidable_fuel_l"] == pytest.approx(hl["fuel_consumed_l"].sum() * 0.5, abs=1e-3)
    body["event_ids"] = ["nope"]
    assert client.post("/api/v1/idle/scenario", json=body).status_code == 404


def test_scenario_extrapolation_is_labelled(client):
    body = {"avoidable_pct": 10, "fuel_price_per_l": 1.8,
            "extrapolation": {"observed_period_days": 16, "projection_days": 365,
                              "assumption_note": "operator assumes the observation window is representative"}}
    r = client.post("/api/v1/idle/scenario", json=body)
    assert r.status_code == 200
    s = r.json()
    p = s["projection"]
    assert p["label"].startswith("SCENARIO / ESTIMATE") and p["assumption_note"]
    assert p["projected_avoidable_fuel_l"] == pytest.approx(s["scenario_avoidable_fuel_l"] * 365 / 16, rel=1e-6)


# --- prohibited terminology --------------------------------------------------------

def test_fixed_statements_have_no_prohibited_terms():
    texts = [serving.INTERPRETATION_STATEMENT, serving.MODULE_STATEMENT, serving.SCOPE_STATEMENT,
             *serving.DETECTION_STATEMENT.values(),
             *(g["label"] for g in serving.ATTENTION_GROUPS.values()),
             *(g["description"] for g in serving.ATTENTION_GROUPS.values())]
    for t in texts:
        assert not serving.PROHIBITED_TERMS.search(t), t


def test_prohibited_terms_regex_catches_examples():
    for bad in ("fuel waste", "Wasted fuel", "avoidable idling", "cost savings", "driver fault", "cause of the stop"):
        assert serving.PROHIBITED_TERMS.search(bad), bad
    assert not serving.PROHIBITED_TERMS.search("reason for the stop is unknown")


def test_generated_interpretation_text_has_no_prohibited_terms(client, overview, summary):
    """Every string the API generates for display, excluding the machine-readable list of
    prohibited claim keys (which names them in order to forbid them)."""
    repo = client.app.state.repo
    payloads = [overview, summary, client.get("/api/v1/meta").json()]
    for eid in list(repo.evidence)[:5] + [LONGEST]:
        payloads.append({k: v for k, v in client.get(f"/api/v1/idle/events/{eid}").json().items() if k != "timeline"})
        payloads.append(client.get(f"/api/v1/ai/evidence/{eid}").json())
    for p in payloads:
        for text in _all_strings(p, skip_keys={"prohibited_claims", "event_id", "evidence_id",
                                               "recording_session_id", "session_first_source_file",
                                               "session_last_source_file"}):
            assert not serving.PROHIBITED_TERMS.search(text), text
