"""harness e2e business flows (real endpoints, LOCAL+SQLITE).

HARNESS_LIFECYCLE_FLOWS is the single source of truth for this runner and
the E3 coverage guard.

harness is read-only in single box for the FlowCase paths. This file
additionally exercises a seeded round-trip: insert an
ac_harness_patch_template row directly via DatabasePlugin.session() raw
SQL, then query list/get-by-id and assert the API surfaces the seeded row.

Template ids are INTEGER autoincrement (NOT string tpl_xxx). Raw SQL
bypasses SQLAlchemy Python-side defaults, so the seed populates all NOT
NULL columns explicitly (name, layer, target, version, operations,
risk_level, status, env, gmt_create, gmt_modified — see
core/harness/sqlite_models.py::HarnessPatchTemplateModel).

POST /diagnose / /generate-patches / /apply / /rollback are NOT exercised
— LLM disabled in LOCAL without token, Arca file ops unreachable. See
findings/harness-llm-and-arca-unmocked.md.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community._flows.harness.api_lifecycle import HARNESS_LIFECYCLE_FLOWS
from tests.community.framework.flow_runner import run_flow


def _seed_harness_template(world, *, name: str) -> int:
    """Insert one ac_harness_patch_template row via raw SQL; return its
    autoincrement id.

    Raw SQL bypasses SQLAlchemy Python-side defaults, so the seed populates
    all NOT NULL columns explicitly. Schema reference:
    core/harness/sqlite_models.py::HarnessPatchTemplateModel.

    NOT NULL columns: name, layer, target, version, operations, risk_level,
    status, env, gmt_create, gmt_modified.
    Nullable: description, applicable_when.

    The list endpoint filters by env=get_current_env() (== "dev" in LOCAL),
    so we seed env="dev" to surface in /api/harness/templates.
    """
    plugin = world.get(DatabasePlugin)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with plugin.session() as s:
        s.execute(text("""
            INSERT INTO ac_harness_patch_template
              (name, layer, target, version, description, applicable_when,
               operations, risk_level, status, env, gmt_create, gmt_modified)
            VALUES (:name, :layer, :target, :version, :description, :applicable_when,
                    :operations, :risk_level, :status, :env, :gmt_create, :gmt_modified)
        """), {
            "name": name,
            "layer": "L1",
            "target": json.dumps({"files": [], "sections": []}),
            "version": 1,
            "description": f"E2E seeded template {name}",
            "applicable_when": None,
            "operations": json.dumps([]),
            "risk_level": "low",
            "status": "active",
            "env": "dev",
            "gmt_create": now,
            "gmt_modified": now,
        })
        s.commit()
        row = s.execute(
            text("SELECT id FROM ac_harness_patch_template WHERE name = :name"),
            {"name": name},
        ).first()
        return int(row[0])


@pytest.mark.e2e
@pytest.mark.parametrize("case", HARNESS_LIFECYCLE_FLOWS, ids=lambda c: c.name)
def test_harness_flow(case, app_with_testing_modules, world):
    """5 read-only flows: templates list/missing + patches/patch-records/diagnose-records."""
    ctx = run_flow(case, app_with_testing_modules, world)
    assert ctx is not None


@pytest.mark.e2e
def test_harness_template_seeded_get_roundtrip(app_with_testing_modules, world):
    """Seed an ac_harness_patch_template row; assert list + get-by-int-id
    surface it.

    Not a FlowCase because template_id is auto-incremented and can't be
    pinned in static flow data.
    """
    seeded_name = "E2E_Seeded_Template"
    seeded_id = _seed_harness_template(world, name=seeded_name)
    assert seeded_id >= 1

    client = TestClient(app_with_testing_modules, headers={"x-user-id": "e2e_user"})

    # 1) GET /api/harness/templates/{seeded_id} — must surface the seeded
    # row. Probe-confirmed envelope: TemplateDetailResponse → flat dict
    # with {success: True, id, name, ...}.
    r = client.get(f"/api/harness/templates/{seeded_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body
    assert body["id"] == seeded_id, body
    assert body["name"] == seeded_name, body

    # 2) GET /api/harness/templates — list contains the seeded template.
    # Probe-confirmed envelope: {success, total, items[]}.
    r = client.get("/api/harness/templates")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["total"] >= 1
    template_ids = [t["id"] for t in body["items"]]
    assert seeded_id in template_ids, body
