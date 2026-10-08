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


def test_internal_openapi_document_and_docs_are_served_separately() -> None:
    client = TestClient(create_app())

    internal = client.get("/internal-openapi.json")
    assert internal.status_code == 200
    internal_paths = internal.json()["paths"]
    assert "/api/v1/collaboration/sessions/{session_id}/files" in internal_paths
    assert not any(
        path.startswith("/openapi/v1/collaboration") for path in internal_paths
    )

    docs = client.get("/internal-docs")
    assert docs.status_code == 200
    assert "/internal-openapi.json" in docs.text

    public_paths = client.get("/openapi.json").json()["paths"]
    assert "/openapi/v1/collaboration/bots/mine" in public_paths
    assert not any(path.startswith("/api/v1/collaboration") for path in public_paths)
