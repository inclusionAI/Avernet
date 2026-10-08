"""Smoke tests for background service modules (market_sync, skill_scan, git_sync)."""
from unittest.mock import MagicMock


def _make_market_sync():
    from agentclaw.community.core.skill_center.services.market_sync import MarketSyncService
    return MarketSyncService(cache_plugin=MagicMock(), skill_service_factory=MagicMock())


class TestMarketSyncService:
    """MarketSyncService basic tests."""

    def test_import_and_instantiate(self):
        svc = _make_market_sync()
        assert svc is not None
        assert svc._started is False

    def test_get_status(self):
        svc = _make_market_sync()
        status = svc.get_status()
        assert status["started"] is False
        assert status["running"] is False
        assert "interval_minutes" in status

    def test_is_running_when_not_started(self):
        svc = _make_market_sync()
        assert svc.is_running() is False


class TestSkillScanService:
    """SkillScanService basic tests."""

    def test_import(self):
        from agentclaw.community.core.skill_center.services.skill_scan import SkillScanService
        assert SkillScanService is not None

    def test_scan_skill_static(self):
        from agentclaw.community.core.skill_center.services.skill_scan import SkillScanService
        # scan_skill is a static/class method that parses a skill directory
        assert callable(getattr(SkillScanService, 'scan_skill', None))


class TestGitSyncService:
    """GitSyncService basic tests."""

    def test_import(self):
        from agentclaw.community.core.skill_center.services.git_sync import GitSyncService
        assert GitSyncService is not None

    def test_config_import(self):
        from agentclaw.community.core.skill_center.services.git_sync import GitSyncConfig
        assert GitSyncConfig is not None

    def test_rewrite_presigned_url_to_office_replaces_domain_and_scheme(self):
        from agentclaw.community.core.skill_center.services.git_sync import (
            _rewrite_presigned_url_to_office,
        )

        internal_url = (
            "http://store-internal.example.com"
            "/aidesktop/aidesktop_pre/bolt_shared/aiworkbench.tar.gz"
            "?Expires=1716259200&OSSAccessKeyId=xxx&Signature=yyy"
        )
        # The office endpoint is deployment config (git_sync.office_oss_endpoint),
        # passed in by GitSyncService from GitSyncConfig.
        result = _rewrite_presigned_url_to_office(
            internal_url, "store-office.example.com"
        )

        assert result.startswith("https://")
        assert "store-office.example.com" in result
        assert "/aidesktop/aidesktop_pre/bolt_shared/aiworkbench.tar.gz" in result
        assert "Expires=1716259200" in result
        assert "Signature=yyy" in result
        assert "-internal" not in result

    def test_rewrite_presigned_url_to_office_preserves_path_params_and_fragment(self):
        from agentclaw.community.core.skill_center.services.git_sync import (
            _rewrite_presigned_url_to_office,
        )

        url = "http://some-random-internal-domain.com/path;p=1?q=2#frag"
        result = _rewrite_presigned_url_to_office(url, "store-office.example.com")

        assert result == "https://store-office.example.com/path;p=1?q=2#frag"

    def test_rewrite_presigned_url_is_noop_when_endpoint_unset(self):
        from agentclaw.community.core.skill_center.services.git_sync import (
            _rewrite_presigned_url_to_office,
        )

        url = "http://internal.example.com/path?q=2"
        # Neutral default (community): no office endpoint → URL returned unchanged.
        assert _rewrite_presigned_url_to_office(url, "") == url
