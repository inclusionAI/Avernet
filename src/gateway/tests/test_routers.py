"""The app serves the generated OpenAPI document.

The document's *contents* come from the schema catalog (tested against a fixture
in ``test_served_openapi``); here we only assert the app serves it. With no
published artifact present the doc is valid but empty — that's expected.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from gateway.community.adapters.web.app import create_app


def test_openapi_document_served() -> None:
    resp = TestClient(create_app()).get("/openapi.json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["openapi"].startswith("3.")
    assert "paths" in body
