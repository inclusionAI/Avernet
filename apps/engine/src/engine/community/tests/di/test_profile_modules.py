from __future__ import annotations

from engine.community.di.profile import EngineProfile
from engine.community.di.runtime_mode import RuntimeConfig, RuntimeMode
from engine.community.tests._support import requires_corp


def _names(modules) -> set[str]:
    return {type(m).__name__ for m in modules}


class TestProfileModules:
    def test_community_column_has_no_corp_modules(self):
        from engine.community.di.profile_modules import modules_for

        names = _names(modules_for(EngineProfile.COMMUNITY))
        assert names
        assert not any(name.startswith("Corp") for name in names)

    def test_test_column_has_no_corp_modules(self):
        from engine.community.di.profile_modules import modules_for

        names = _names(modules_for(EngineProfile.TEST))
        assert names
        assert not any(name.startswith("Corp") for name in names)

    @requires_corp
    def test_corp_column_is_not_community_fallback(self):
        from engine.community.di.profile_modules import modules_for

        names = _names(modules_for(EngineProfile.CORP))
        assert names
        assert any(name.startswith("Corp") for name in names)
        assert "CommunityInfrastructureModule" not in names

    def test_community_injector_builds(self):
        from engine.community.di.container import build_injector

        injector = build_injector(
            config=RuntimeConfig(runtime=RuntimeMode.LOCAL, profile=EngineProfile.COMMUNITY)
        )
        assert injector is not None
