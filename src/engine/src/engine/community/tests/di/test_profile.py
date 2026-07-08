from __future__ import annotations

import pytest

from engine.community.di.profile import EngineProfile


class TestEngineProfileDetect:
    def test_default_is_community(self, monkeypatch):
        monkeypatch.delenv("ENGINE_PROFILE", raising=False)
        monkeypatch.delenv("DEPLOY_PROFILE", raising=False)
        assert EngineProfile.detect() is EngineProfile.COMMUNITY

    def test_engine_profile_env(self, monkeypatch):
        monkeypatch.setenv("ENGINE_PROFILE", "corp")
        assert EngineProfile.detect() is EngineProfile.CORP

    def test_deploy_profile_fallback(self, monkeypatch):
        monkeypatch.delenv("ENGINE_PROFILE", raising=False)
        monkeypatch.setenv("DEPLOY_PROFILE", "test")
        assert EngineProfile.detect() is EngineProfile.TEST

    def test_engine_profile_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("ENGINE_PROFILE", "community")
        monkeypatch.setenv("DEPLOY_PROFILE", "corp")
        assert EngineProfile.detect() is EngineProfile.COMMUNITY

    def test_unknown_value_raises(self, monkeypatch):
        monkeypatch.setenv("ENGINE_PROFILE", "bogus")
        with pytest.raises(RuntimeError, match="Unknown ENGINE_PROFILE"):
            EngineProfile.detect()

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("ENGINE_PROFILE", "  Corp  ")
        assert EngineProfile.detect() is EngineProfile.CORP

    def test_singlebox_deploy_profile_maps_to_community(self, monkeypatch):
        # Backend singlebox mode exports DEPLOY_PROFILE=singlebox, which the
        # engine inherits when spawned; it must wire community, not crash.
        monkeypatch.delenv("ENGINE_PROFILE", raising=False)
        monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
        assert EngineProfile.detect() is EngineProfile.COMMUNITY

    def test_singlebox_engine_profile_maps_to_community(self, monkeypatch):
        monkeypatch.setenv("ENGINE_PROFILE", "SingleBox")
        assert EngineProfile.detect() is EngineProfile.COMMUNITY
