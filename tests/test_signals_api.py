"""Dashboard V2 signal endpoints: catalog, data/signal health, traceability."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from fleetpulse.api.app import create_app  # noqa: E402
from fleetpulse.api.signals import HEALTH_STATEMENT  # noqa: E402
from fleetpulse.j1939.definitions import MESSAGES, SIGNALS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data" / "gold_part3" / "gold"
SILVER = ROOT / "data" / "gold_part3" / "silver"
BRONZE = ROOT / "data" / "gold_part3" / "bronze" / "bronze_can_frames"
LONGEST = "turku-renault-t520-6x2-20210119T092357960305-idle-092402900000"

pytestmark = pytest.mark.skipif(not (GOLD / "_builds").exists() or not SILVER.exists(),
                                reason="Part 3 Gold/Silver build not present")


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(GOLD, SILVER, ai_provider=None))


def test_catalog_is_the_verified_definitions(client):
    d = client.get("/api/v1/signals").json()
    assert d["count"] == len(SIGNALS) == 12
    by = {i["name"]: i for i in d["items"]}
    assert set(by) == {s.name for s in SIGNALS}
    for s in SIGNALS:
        i = by[s.name]
        assert (i["pgn"], i["spn"]) == (s.pgn, s.spn)
        assert i["reference"] == s.reference
        if s.kind.value == "measured":
            assert i["decode"]["resolution"] == s.resolution and i["decode"]["offset"] == s.offset
    es = by["engine_speed_rpm"]
    assert es["message"] == "EEC1" and es["message_name"] == "Electronic Engine Controller 1"
    assert es["decode"]["position"] == "byte 4-5" and es["decode"]["resolution"] == 0.125
    assert "little-endian" in es["decode"]["byte_order"] and es["system"] == "engine"
    assert {i["system"] for i in d["items"]} == {"engine", "fuel", "cooling", "driver", "motion", "environment"}
    assert any(c["level"] == "quality_warning" for c in by["ambient_temp_c"]["caveats"])
    assert any(c["level"] == "unavailable" for c in by["clutch_switch"]["caveats"])
    assert all(c["source"].startswith("docs/") for i in d["items"] for c in i["caveats"])
    assert all("{" not in i["gold_use"]["text"] for i in d["items"])  # thresholds filled from the Gold rule
    assert "300 rpm" in es["gold_use"]["text"]


def test_health_scope_and_disclaimer(client):
    h = client.get("/api/v1/signals/health").json()
    assert h["statement"] == HEALTH_STATEMENT and "not a statement about the mechanical" in h["statement"]
    sc = h["scope"]
    assert sc["vehicles"] == 1 and sc["dataset"] == "Part 3" and sc["historical"] is True
    assert "not current or live" in sc["note"].lower()
    assert h["rows_analysed"] == 803_880
    assert 0 < h["engine_running_rows"] < h["rows_analysed"]
    assert sc["recording_sessions"] == 46


def test_health_counts_reconcile(client):
    h = client.get("/api/v1/signals/health").json()
    assert len(h["signals"]) == 12
    for s in h["signals"]:
        for scope, n in (("all_rows", h["rows_analysed"]), ("engine_running_rows", h["engine_running_rows"])):
            c = s["scopes"][scope]
            assert c["rows"] == n
            assert sum(c["status_counts"].values()) == n
            assert sum(c["freshness_counts"].values()) == n
            assert c["available_rows"] <= c["status_counts"].get("valid", 0)
    states = {s["name"]: s["state"] for s in h["signals"]}
    assert states["clutch_switch"] == "unavailable"
    assert states["ambient_temp_c"] == "quality_warning"
    assert states["engine_speed_rpm"] == "available"
    assert set(states.values()) <= set(h["state_rules"])
    assert "healthy" not in str(h).lower().replace("signal health", "")


@pytest.mark.skipif(not BRONZE.exists(), reason="Bronze frames not present")
@pytest.mark.parametrize("name", [s.name for s in SIGNALS])
def test_trace_is_real_and_consistent_for_every_signal(client, name):
    r = client.get(f"/api/v1/signals/{name}/trace")
    assert r.status_code == 200, r.text
    d = r.json()
    st = d["steps"]
    assert d["anchor"]["event_id"] == LONGEST and d["anchor"]["default_reference_event"] is True
    raw = st["raw_can"]
    assert raw["source_file"].startswith("part_3/") and raw["source_line"] > 0 and len(raw["data"]) == 8
    assert raw["can_id_hex"] == f"0x{raw['can_id']:08X}"
    spec = next(s for s in SIGNALS if s.name == name)
    assert st["j1939"]["pgn"] == spec.pgn and st["spn"]["spn"] == spec.spn
    # the production decoder, the Silver signal event (same source line) and the telemetry row agree
    assert d["checks"]["decoder_matches_silver_event"] is True
    assert d["checks"]["decoder_matches_telemetry"] is True
    assert st["silver_signal_event"]["source_file"] == raw["source_file"]
    assert st["silver_signal_event"]["source_line"] == raw["source_line"]
    assert st["silver_telemetry"]["age_ms"] <= st["silver_telemetry"]["freshness_limit_ms"]
    assert st["gold_event"]["event_id"] == LONGEST


@pytest.mark.skipif(not BRONZE.exists(), reason="Bronze frames not present")
def test_trace_engine_speed_arithmetic(client):
    st = client.get("/api/v1/signals/engine_speed_rpm/trace", params={"event_id": LONGEST}).json()["steps"]
    lo, hi = st["decode"]["bytes"][0]["value"], st["decode"]["bytes"][1]["value"]
    assert st["decode"]["raw"] == lo + 256 * hi
    assert st["physical"]["value"] == pytest.approx(st["decode"]["raw"] * 0.125)
    assert st["j1939"]["message"] == "EEC1" and st["j1939"]["pgn_hex"] == "0xF004"
    assert 300 <= st["physical"]["value"] < 700  # a stationary engine-on event: idle speed
    assert st["gold_event"]["signal_role"]["kind"] == "state_rule"


@pytest.mark.skipif(not BRONZE.exists(), reason="Bronze frames not present")
def test_trace_unavailable_signal_is_reported_not_invented(client):
    st = client.get("/api/v1/signals/clutch_switch/trace").json()["steps"]
    assert st["physical"]["status"] == "not_available" and st["physical"]["value"] is None
    assert st["silver_telemetry"]["value"] is None


@pytest.mark.parametrize("path,code", [
    ("/api/v1/signals/not_a_signal/trace", 404),
    (f"/api/v1/signals/engine_speed_rpm/trace?event_id=nope", 404),
])
def test_trace_errors(client, path, code):
    assert client.get(path).status_code == code


def test_messages_cover_catalog():
    assert sum(len(m.signals) for m in MESSAGES) == 12
