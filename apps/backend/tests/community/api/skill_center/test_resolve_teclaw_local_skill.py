"""Unit tests for the router helper that resolves teclaw-ness for a bot's
local-skill device-fs paths (adapters/http/skill_center/skills.py)."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.adapters.http.skill_center.skills import _resolve_teclaw_local_skill
from agentclaw.community.core.config_compose.teclaw_paths import to_local_skill_engine_path

pytestmark = pytest.mark.unit


def test_teclaw_returns_adapter():
    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = SimpleNamespace(provider="teclaw")
    is_teclaw, adapter = _resolve_teclaw_local_skill(resolver, "b1", "owner1")
    assert is_teclaw is True
    assert adapter is to_local_skill_engine_path


def test_non_teclaw_returns_identity_none():
    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = SimpleNamespace(provider="arca")
    assert _resolve_teclaw_local_skill(resolver, "b1", "owner1") == (False, None)


def test_resolver_failure_falls_back_to_non_teclaw():
    resolver = MagicMock()
    resolver.resolve_for_bot.side_effect = RuntimeError("no active binding")
    assert _resolve_teclaw_local_skill(resolver, "b1", "owner1") == (False, None)
