"""Tests for GET /api/v1/token/iam — return raw IAM_TOKEN from cookie."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_get_iam_token_returns_cookie_value(app_with_testing_modules) -> None:
    client = TestClient(app_with_testing_modules)
    resp = client.get("/api/v1/token/iam", cookies={"IAM_TOKEN": "my-iam-token-123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"success": True, "iam_token": "my-iam-token-123"}


def test_get_iam_token_missing_cookie_returns_400(app_with_testing_modules) -> None:
    client = TestClient(app_with_testing_modules)
    resp = client.get("/api/v1/token/iam")
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert "IAM_TOKEN" in body["error"]


def test_get_iam_token_empty_cookie_returns_400(app_with_testing_modules) -> None:
    client = TestClient(app_with_testing_modules)
    resp = client.get("/api/v1/token/iam", cookies={"IAM_TOKEN": ""})
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False


def test_get_iam_token_cors_headers(app_with_testing_modules) -> None:
    client = TestClient(app_with_testing_modules)
    resp = client.get(
        "/api/v1/token/iam",
        cookies={"IAM_TOKEN": "token"},
        headers={"origin": "https://teamclaw.com"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://teamclaw.com"
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_get_iam_token_options_preflight(app_with_testing_modules) -> None:
    client = TestClient(app_with_testing_modules)
    resp = client.options(
        "/api/v1/token/iam",
        headers={"origin": "https://teamclaw.com"},
    )
    assert resp.status_code == 200
    assert "GET" in resp.headers["access-control-allow-methods"]
