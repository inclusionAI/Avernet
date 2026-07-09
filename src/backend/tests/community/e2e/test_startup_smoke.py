"""E0 — startup smoke guard (global, all modules share this).

Imports the REAL app (``agentclaw.community.adapters.http.app:app``) and runs a real
HTTP health check. The import triggers ``build_injector`` (genuine DI
assembly), which is exactly what a unit test using
``app_with_testing_modules`` bypasses.

NOTE — eager_check_critical_bindings is NOT exercised here: in
``app.py`` it is guarded by ``if get_current_env() in ("pre", "prod")``,
so under the test environment (SERVER_ENV unset → "dev") that code-path
is skipped. This test validates DI assembly only, NOT eager bindings.

NOTE — conftest.py already imports ``agentclaw.community.adapters.http.app`` at
session startup (``_wire_app_injector_for_tests()`` runs at module level),
so Python's import cache means the ``from agentclaw.community.adapters.http.app
import app`` line below does not re-execute the composition root. The
incremental value of this test is therefore twofold:
(a) it is a visible, named ownership point for the real-app import;
(b) the GET /api/health call (200 + status==ok) is additional coverage that
    conftest's pure import does not provide.

This is the import-real-app shape (acceptance E0, Option B): fast, stable,
CI-friendly. It does NOT test lifespan hooks (a subprocess-boots-main.py
test is the higher-fidelity follow-up).

Runs in default CI (TestClient + SQLite, no full stack) — the e2e marker was
removed since this is in-memory and never required MOSN/ZDAS/network.
"""
from __future__ import annotations

import pytest


def test_real_app_boots_and_health_responds():
    # Importing the real app triggers build_injector (real DI assembly).
    # eager_check_critical_bindings is skipped in dev/test (SERVER_ENV != pre/prod).
    # A failure here means single-box DI assembly is broken — the exact blind
    # spot app_with_testing_modules hides.
    from agentclaw.community.adapters.http.app import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200, f"/api/health → {resp.status_code}: {resp.text[:300]}"
    assert resp.json().get("status") == "ok", resp.json()
