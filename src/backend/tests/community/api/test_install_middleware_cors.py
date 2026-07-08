"""``install_middleware`` sources the CORS allow-list from the injected
``CorsConfig`` (OSS-0 #3) — origins are no longer hardcoded in the module.

Proves both the exact-origin list and the regex list are honoured, and that an
origin outside both is rejected (no ``Access-Control-Allow-Origin`` echoed).
"""
from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from agentclaw.community.adapters.http.middleware import install_middleware
from agentclaw.community.di.config import CorsConfig
from agentclaw.community.plugins.local.tracer import NoopTracer


class _FakeAuth:
    async def resolve_user_from_request(self, ctx):
        return None


def _client(cors_config: CorsConfig | None) -> TestClient:
    app = FastAPI()
    install_middleware(
        app, auth_plugin=_FakeAuth(), tracer=NoopTracer(), cors_config=cors_config
    )

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    return TestClient(app)


def _acao(client: TestClient, origin: str) -> str | None:
    resp = client.get("/ok", headers={"Origin": origin})
    return resp.headers.get("access-control-allow-origin")


def test_exact_origin_from_config_is_allowed_and_others_rejected():
    client = _client(
        CorsConfig(allow_origins=["https://allowed.example.com"], allow_origin_regex=[])
    )
    assert _acao(client, "https://allowed.example.com") == "https://allowed.example.com"
    # An origin not in the configured list is not echoed back.
    assert _acao(client, "https://evil.example.com") in (None, "")


def test_regex_origin_from_config_is_allowed():
    client = _client(
        CorsConfig(
            allow_origins=[],
            allow_origin_regex=[r"https://.*\.trusted\.example\.com"],
        )
    )
    assert (
        _acao(client, "https://team.trusted.example.com")
        == "https://team.trusted.example.com"
    )
    assert _acao(client, "https://team.untrusted.example.com") in (None, "")


def test_none_falls_back_to_neutral_localhost_default():
    # A hand-rolled consumer passing no cors_config gets the neutral default.
    client = _client(None)
    assert _acao(client, "http://localhost:3000") == "http://localhost:3000"
    assert _acao(client, "https://agentclaw.teamclaw.com") in (None, "")
