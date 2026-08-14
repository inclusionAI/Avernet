"""resolve_extra_modules — singlebox corp overlay resolution (B8).

Covers the SINGLEBOX branch and the ModuleNotFoundError fallback so
Avernet CI meets the changed-line coverage gate (>= 80%).
"""
import pytest

from agentclaw.community.di.modules_bootstrap import resolve_extra_modules
from agentclaw.community.di.profile import DeployProfile


@pytest.mark.parametrize(
    "profile",
    [DeployProfile.TEST, DeployProfile.CORP, DeployProfile.CORP_TEST, DeployProfile.COMMUNITY],
)
def test_non_singlebox_profiles_return_none(profile):
    assert resolve_extra_modules(profile) is None


def test_singlebox_without_corp_returns_none():
    """Community build (Avernet CI) has no agentclaw.corp — ModuleNotFoundError path."""
    result = resolve_extra_modules(DeployProfile.SINGLEBOX)
    assert result is None


def test_singlebox_with_corp_returns_modules(monkeypatch):
    """Simulate OCB monorepo where agentclaw.corp is importable."""
    import importlib
    from types import ModuleType
    from injector import Module as Mod

    class _FakeOverlayModule(Mod):
        pass

    _fake_bootstrap = ModuleType("agentclaw.corp.di.corp_bootstrap")
    _fake_bootstrap.get_singlebox_overlay_modules = lambda: [_FakeOverlayModule()]

    original = importlib.import_module

    def _fake_import(name, *args, **kwargs):
        if name == "agentclaw.corp.di.corp_bootstrap":
            return _fake_bootstrap
        return original(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", _fake_import)

    result = resolve_extra_modules(DeployProfile.SINGLEBOX)
    assert result is not None
    assert len(result) == 1
    assert isinstance(result[0], _FakeOverlayModule)