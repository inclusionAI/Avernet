"""Unit tests for the neutral frontend-url module (data-driven, not a plugin).

Covers ConfigFrontendUrlProvider (empty static / holder-injection priority /
static fallback) and resolve_static_frontend_url's env branches.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.task_discovery.frontend_url import (
    ConfigFrontendUrlProvider,
    FrontendUrlConfig,
    resolve_static_frontend_url,
)
from agentclaw.community.core.task.task_discovery.session_initiator import (
    FrontendUrlHolder,
)


@pytest.fixture(autouse=True)
def _clean_holder():
    """Reset the runtime holder around every test (class-level global)."""
    FrontendUrlHolder._url = ""
    yield
    FrontendUrlHolder._url = ""


class TestGet:
    def test_empty_static_returns_empty(self):
        assert ConfigFrontendUrlProvider().get() == ""

    def test_static_value_returned(self):
        assert ConfigFrontendUrlProvider("http://static:8000").get() == (
            "http://static:8000"
        )

    def test_runtime_holder_injection_wins(self):
        prov = ConfigFrontendUrlProvider("http://static:8000")
        FrontendUrlHolder.set("http://injected:7777")
        assert prov.get() == "http://injected:7777"

    def test_holder_empty_falls_back_to_static(self):
        prov = ConfigFrontendUrlProvider("http://static:8000")
        assert FrontendUrlHolder.get() == ""
        assert prov.get() == "http://static:8000"


class TestResolveStatic:
    CFG = FrontendUrlConfig(
        url="http://dev.example.com",
        url_pre="http://pre.example.com",
        url_prod="http://prod.example.com",
    )

    @pytest.mark.parametrize(
        "env,expected",
        [
            ("dev", "http://dev.example.com"),
            ("pre", "http://pre.example.com"),
            ("prod", "http://prod.example.com"),
            ("gray", "http://dev.example.com"),  # 未识别 env → 默认 url
        ],
    )
    def test_env_branches(self, env, expected):
        assert resolve_static_frontend_url(self.CFG, env) == expected

    def test_pre_falls_back_to_default_when_pre_empty(self):
        cfg = FrontendUrlConfig(url="http://default.example.com", url_pre="")
        assert resolve_static_frontend_url(cfg, "pre") == "http://default.example.com"

    def test_prod_falls_back_to_default_when_prod_empty(self):
        cfg = FrontendUrlConfig(url="http://default.example.com", url_prod="")
        assert resolve_static_frontend_url(cfg, "prod") == "http://default.example.com"

    def test_all_empty_returns_empty(self):
        assert resolve_static_frontend_url(FrontendUrlConfig(), "prod") == ""