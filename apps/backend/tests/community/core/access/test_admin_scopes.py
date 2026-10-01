"""Unit tests for the config-driven admin-scope helpers.

Mirrors the token fix's ``_uct_auth_header`` tests: monkeypatch the lazy
``sofa_config`` proxy and assert each scope resolves from ``user_config.admin``,
with a fail-closed empty frozenset on missing/blank/non-list/absent config.
"""
from agentclaw.community.core.access.admin_scopes import (
    collaborator_admin,
    device_admin,
    disk_usage_admin,
    harness_admin,
    skill_admin,
    super_admin,
)


class _FakeConfig:
    """Minimal stand-in for the lazy ``sofa_config`` proxy."""

    def __init__(self, user_config):
        self.user_config = user_config


def _patch_config(monkeypatch, user_config):
    # The helpers do ``from ...config.sofa import sofa_config`` at call time, so
    # patching the module attribute is picked up on the next call.
    monkeypatch.setattr(
        "agentclaw.community.core.config.sofa.sofa_config",
        _FakeConfig(user_config),
    )


def test_scopes_absent_when_no_admin_block(monkeypatch):
    _patch_config(monkeypatch, {})
    assert super_admin() == frozenset()
    assert disk_usage_admin() == frozenset()
    assert harness_admin() == frozenset()
    assert skill_admin() == frozenset()
    assert collaborator_admin() == frozenset()
    assert device_admin() == frozenset()


def test_device_admin_reads_its_list(monkeypatch):
    _patch_config(monkeypatch, {"admin": {"device_admin": ["100000", "100002"]}})
    assert device_admin() == frozenset({"100000", "100002"})
    # Isolated from other scopes.
    assert super_admin() == frozenset()
    assert collaborator_admin() == frozenset()


def test_collaborator_admin_reads_its_list(monkeypatch):
    _patch_config(monkeypatch, {"admin": {"collaborator_admin": ["100000", "100001"]}})
    assert collaborator_admin() == frozenset({"100000", "100001"})
    # Isolated from other scopes.
    assert super_admin() == frozenset()


def test_each_scope_reads_its_configured_list(monkeypatch):
    _patch_config(
        monkeypatch,
        {
            "admin": {
                "super_admin": ["100000", "100001"],
                "disk_usage_admin": ["100002"],
                "harness_admin": ["100003"],
                "skill_admin": ["100004", "100005"],
            }
        },
    )
    assert super_admin() == frozenset({"100000", "100001"})
    assert disk_usage_admin() == frozenset({"100002"})
    assert harness_admin() == frozenset({"100003"})
    assert skill_admin() == frozenset({"100004", "100005"})


def test_scope_isolation(monkeypatch):
    # An id authorized for one scope is not silently granted another.
    _patch_config(monkeypatch, {"admin": {"super_admin": ["100000"]}})
    assert "100000" in super_admin()
    assert "100000" not in disk_usage_admin()
    assert "100000" not in harness_admin()
    assert "100000" not in skill_admin()


def test_blank_and_nonlist_values_yield_empty(monkeypatch):
    _patch_config(monkeypatch, {"admin": {"super_admin": None}})
    assert super_admin() == frozenset()
    _patch_config(monkeypatch, {"admin": {"super_admin": "100000"}})  # str, not list
    assert super_admin() == frozenset()
    _patch_config(monkeypatch, {"admin": {"super_admin": []}})
    assert super_admin() == frozenset()


def test_ids_coerced_to_str(monkeypatch):
    _patch_config(monkeypatch, {"admin": {"super_admin": [100000, 100001]}})
    assert super_admin() == frozenset({"100000", "100001"})


def test_none_user_config_yields_empty(monkeypatch):
    # ``getattr(sofa_config, "user_config", None)`` returning None must fail closed.
    _patch_config(monkeypatch, None)
    assert super_admin() == frozenset()


def test_malformed_admin_block_fails_closed(monkeypatch):
    # A non-dict ``admin`` block makes ``block.get(key)`` raise inside the read;
    # the defensive except must swallow it and DENY (empty frozenset), never grant.
    _patch_config(monkeypatch, {"admin": "oops-not-a-dict"})
    assert super_admin() == frozenset()
    assert disk_usage_admin() == frozenset()


def test_config_read_exception_fails_closed(monkeypatch):
    # If reading config raises outright, the gate must still deny.
    class _Boom:
        @property
        def user_config(self):
            raise RuntimeError("config backend unreachable")

    monkeypatch.setattr(
        "agentclaw.community.core.config.sofa.sofa_config", _Boom()
    )
    assert super_admin() == frozenset()
    assert skill_admin() == frozenset()
