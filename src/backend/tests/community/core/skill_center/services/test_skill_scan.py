"""Tests for agentclaw.community.core.skill_center.services.skill_scan.SkillScanService.

Focuses on logic that does NOT require the ant_skills_scan_sdk package
(config loading, lifecycle, helper methods, update_skill_metadata_by_git_path).
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.skill_center.services.skill_scan import (
    SkillScanService,
)


def _make_skill_scan_service(**kwargs):
    """Construct SkillScanService with required mocks defaulted to MagicMock."""
    kwargs.setdefault("cache_plugin", MagicMock())
    kwargs.setdefault("skill_repository", MagicMock())
    kwargs.setdefault("skill_center_sync_service", MagicMock())
    kwargs.setdefault("scanner", MagicMock())
    return SkillScanService(**kwargs)


# ---------------------------------------------------------------------------
# _load_config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_defaults_applied(self):
        with patch(
            "agentclaw.community.core.skill_center.services.skill_scan.get_config",
            side_effect=ImportError("no sofapy"),
            create=True,
        ):
            svc = _make_skill_scan_service()
        assert svc._config["enabled"] is True

    def test_override_config_takes_priority(self):
        svc = _make_skill_scan_service(config={"enabled": False, "scan_interval_hours": 5})
        assert svc._config["enabled"] is False
        assert svc._config["scan_interval_hours"] == 5

    def test_sofa_config_exception_uses_defaults(self):
        """If sofapy_base import raises, we fall back to DEFAULT_CONFIG."""
        # Import sofapy_base.app.config raises — simulated via sys.modules injection.
        # Only inject the modules that aren't already present, and remove exactly
        # those afterwards: when sofapy is absent (community), leaving the fake
        # sofapy_base.app.config in sys.modules poisons later tests that do a real
        # `import sofapy_base.app.config` (e.g. test_patch_sofapy). When sofapy is
        # installed (corp), setdefault was a no-op and we must not evict the real one.
        import sys
        fake_mod = MagicMock()
        fake_mod.get_config.side_effect = Exception("config unavailable")
        _injected = []
        for _name, _stub in (
            ("sofapy_base", MagicMock()),
            ("sofapy_base.app", MagicMock()),
            ("sofapy_base.app.config", fake_mod),
        ):
            if _name not in sys.modules:
                sys.modules[_name] = _stub
                _injected.append(_name)
        try:
            svc = _make_skill_scan_service()
        finally:
            for _name in _injected:
                sys.modules.pop(_name, None)
        assert svc._config.get("enabled") is not None


# ---------------------------------------------------------------------------
# Service lifecycle
# ---------------------------------------------------------------------------

class TestServiceLifecycle:
    def test_initial_state(self):
        svc = _make_skill_scan_service()
        assert svc._started is False
        assert svc._sdk is None
        assert not svc.is_running()

    def test_start_when_disabled_returns_false(self):
        svc = _make_skill_scan_service(config={"enabled": False})
        result = svc.start()
        assert result is False
        assert svc._started is False

    def test_start_already_started_returns_true(self):
        svc = _make_skill_scan_service()
        svc._started = True
        result = svc.start()
        assert result is True

    def test_start_scanner_unavailable_returns_false(self):
        svc = _make_skill_scan_service()
        svc._scanner.create_sdk.return_value = None
        result = svc.start()
        assert result is False
        assert svc._started is False

    def test_start_scanner_error_returns_false(self):
        svc = _make_skill_scan_service()
        svc._scanner.create_sdk.side_effect = RuntimeError("boom")
        result = svc.start()
        assert result is False

    def test_start_with_scanner_sdk_succeeds(self):
        svc = _make_skill_scan_service()
        sdk = MagicMock()
        svc._scanner.create_sdk.return_value = sdk
        result = svc.start()
        assert result is True
        assert svc._started is True
        assert svc._sdk is sdk

    def test_stop_when_not_started(self):
        svc = _make_skill_scan_service()
        result = svc.stop()
        assert result is True

    def test_stop_clears_started_flag(self):
        svc = _make_skill_scan_service()
        svc._started = True
        svc._sdk = MagicMock()
        result = svc.stop()
        assert result is True
        assert svc._started is False
        assert svc._sdk is None

    def test_stop_joins_daily_task_thread(self):

        svc = _make_skill_scan_service()
        svc._started = True
        # Create a thread that will finish quickly
        evt = MagicMock()
        evt.set = MagicMock()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        mock_thread.join = MagicMock()
        svc._daily_task_thread = mock_thread
        svc._daily_task_stop_event = evt

        svc.stop()

        evt.set.assert_called_once()
        mock_thread.join.assert_called_once()

    def test_is_running_false_when_not_started(self):
        svc = _make_skill_scan_service()
        assert svc.is_running() is False

    def test_is_running_true_when_started(self):
        svc = _make_skill_scan_service()
        svc._started = True
        assert svc.is_running() is True


# ---------------------------------------------------------------------------
# _ensure_started
# ---------------------------------------------------------------------------

class TestEnsureStarted:
    def test_raises_when_not_started(self):
        svc = _make_skill_scan_service()
        with pytest.raises(RuntimeError, match="not started"):
            svc._ensure_started()

    def test_no_error_when_started(self):
        svc = _make_skill_scan_service()
        svc._started = True
        svc._ensure_started()  # Should not raise


# ---------------------------------------------------------------------------
# start_daily_task environment gate
# ---------------------------------------------------------------------------

class TestStartDailyTask:
    def test_non_pre_prod_env_returns_false(self):
        svc = _make_skill_scan_service()
        svc._started = True
        with patch("agentclaw.community.utils.env_utils.get_current_env_with_gray", return_value="dev"):
            result = svc.start_daily_task("http://git.example.com/repo.tar.gz")
        assert result is False

    def test_not_started_raises(self):
        svc = _make_skill_scan_service()
        with patch("agentclaw.community.utils.env_utils.get_current_env_with_gray", return_value="pre"):
            with pytest.raises(RuntimeError, match="not started"):
                svc.start_daily_task("http://git.example.com/repo.tar.gz")

    def test_already_running_returns_true(self):
        svc = _make_skill_scan_service()
        svc._started = True
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        svc._daily_task_thread = mock_thread

        with patch("agentclaw.community.utils.env_utils.get_current_env_with_gray", return_value="pre"):
            result = svc.start_daily_task("http://git.example.com/repo.tar.gz")
        assert result is True


# ---------------------------------------------------------------------------
# stop_daily_task
# ---------------------------------------------------------------------------

class TestStopDailyTask:
    def test_no_thread_returns_true(self):
        svc = _make_skill_scan_service()
        result = svc.stop_daily_task()
        assert result is True

    def test_dead_thread_returns_true(self):
        svc = _make_skill_scan_service()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        svc._daily_task_thread = mock_thread
        result = svc.stop_daily_task()
        assert result is True

    def test_live_thread_gets_stopped(self):
        svc = _make_skill_scan_service()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        svc._daily_task_thread = mock_thread
        result = svc.stop_daily_task()
        svc._daily_task_stop_event.set()
        mock_thread.join.assert_called_once()
        assert result is True


# ---------------------------------------------------------------------------
# scan_skill — error handling
# ---------------------------------------------------------------------------

class TestScanSkill:
    def test_raises_when_not_started(self):
        svc = _make_skill_scan_service()
        with pytest.raises(RuntimeError, match="not started"):
            svc.scan_skill("/some/path")

    def test_raises_file_not_found(self, tmp_path):
        svc = _make_skill_scan_service()
        svc._started = True
        svc._sdk = MagicMock()
        with pytest.raises(FileNotFoundError):
            svc.scan_skill(str(tmp_path / "nonexistent.py"))

    def test_sdk_exception_propagates(self, tmp_path):
        svc = _make_skill_scan_service()
        svc._started = True
        skill_file = tmp_path / "skill.py"
        skill_file.write_text("# skill")
        svc._sdk = MagicMock()
        svc._sdk.scan.side_effect = RuntimeError("sdk failure")
        with pytest.raises(RuntimeError, match="sdk failure"):
            svc.scan_skill(str(skill_file))


# ---------------------------------------------------------------------------
# _filter_mcp_dependencies
# ---------------------------------------------------------------------------

class TestFilterMcpDependencies:
    def test_none_returns_empty(self):
        svc = _make_skill_scan_service()
        assert svc._filter_mcp_dependencies(None) == []

    def test_empty_list_returns_empty(self):
        svc = _make_skill_scan_service()
        assert svc._filter_mcp_dependencies([]) == []

    def test_dict_form_keeps_three_fields(self):
        svc = _make_skill_scan_service()
        deps = [{"code": "srv1", "name": "Service1", "url": "http://x", "extra": "drop"}]
        result = svc._filter_mcp_dependencies(deps)
        assert result == [{"code": "srv1", "name": "Service1", "url": "http://x"}]

    def test_object_form_extracts_attributes(self):
        svc = _make_skill_scan_service()
        obj = MagicMock()
        obj.code = "srv2"
        obj.name = "Service2"
        obj.url = "http://y"
        result = svc._filter_mcp_dependencies([obj])
        assert result == [{"code": "srv2", "name": "Service2", "url": "http://y"}]

    def test_missing_fields_default_to_empty_string(self):
        svc = _make_skill_scan_service()
        deps = [{"code": "only-code"}]
        result = svc._filter_mcp_dependencies(deps)
        assert result[0]["name"] == ""
        assert result[0]["url"] == ""

    def test_mixed_forms(self):
        svc = _make_skill_scan_service()
        obj = MagicMock()
        obj.code = "srv3"
        obj.name = "S3"
        obj.url = ""
        deps = [
            {"code": "dict-srv", "name": "Dict", "url": "http://d"},
            obj,
        ]
        result = svc._filter_mcp_dependencies(deps)
        assert len(result) == 2
        assert result[0]["code"] == "dict-srv"
        assert result[1]["code"] == "srv3"


# ---------------------------------------------------------------------------
# update_skill_metadata_by_git_path
# ---------------------------------------------------------------------------

class TestUpdateSkillMetadataByGitPath:
    def test_empty_git_path_raises(self):
        svc = _make_skill_scan_service()
        with pytest.raises(ValueError, match="git_path cannot be empty"):
            svc.update_skill_metadata_by_git_path(git_path="", risk_tags=[])

    def test_no_update_params_raises(self):
        svc = _make_skill_scan_service()
        with pytest.raises(ValueError, match="At least one of"):
            svc.update_skill_metadata_by_git_path(git_path="git://a/b")

    def test_skill_not_found_returns_none(self):
        mock_repo = MagicMock()
        svc = _make_skill_scan_service(skill_repository=mock_repo)
        mock_repo.get_by_git_path.return_value = None

        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="dev"):
                result = svc.update_skill_metadata_by_git_path(
                    git_path="git://cat/missing-skill",
                    risk_tags=[],
                )
        assert result is None

    def test_updates_risk_tags(self):
        mock_repo = MagicMock()
        svc = _make_skill_scan_service(skill_repository=mock_repo)
        mock_repo.get_by_git_path.return_value = {"id": "42", "name": "skill-x"}
        mock_repo.update_risk_tags.return_value = {"id": "42", "risk_tags": ["tag1"]}

        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="dev"):
                result = svc.update_skill_metadata_by_git_path(
                    git_path="git://cat/skill-x",
                    risk_tags=[{"type": "tag1"}],
                )
        mock_repo.update_risk_tags.assert_called_once_with("42", [{"type": "tag1"}])
        assert result is not None

    def test_updates_mcp_dependencies(self):
        mock_repo = MagicMock()
        svc = _make_skill_scan_service(skill_repository=mock_repo)
        mock_repo.get_by_git_path.return_value = {"id": "10", "name": "skill-y"}
        mock_repo.update_mcp_dependencies.return_value = {"id": "10"}

        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="dev"):
                result = svc.update_skill_metadata_by_git_path(
                    git_path="git://cat/skill-y",
                    mcp_dependencies=[{"code": "svc-a", "name": "A", "url": ""}],
                )
        mock_repo.update_mcp_dependencies.assert_called_once()
        assert result is not None

    def test_prod_env_clears_risk_tags(self):
        mock_repo = MagicMock()
        svc = _make_skill_scan_service(skill_repository=mock_repo)
        mock_repo.get_by_git_path.return_value = {"id": "99", "name": "sk"}
        mock_repo.update_risk_tags.return_value = {"id": "99"}

        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="prod"):
                svc.update_skill_metadata_by_git_path(
                    git_path="git://cat/sk",
                    risk_tags=[{"type": "sensitive-tag"}],
                )
        # In prod env, risk_tags should be cleared to []
        mock_repo.update_risk_tags.assert_called_once_with("99", [])

    def test_update_risk_tags_failure_returns_none(self):
        mock_repo = MagicMock()
        svc = _make_skill_scan_service(skill_repository=mock_repo)
        mock_repo.get_by_git_path.return_value = {"id": "7", "name": "sk"}
        mock_repo.update_risk_tags.return_value = None  # failure

        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="dev"):
                result = svc.update_skill_metadata_by_git_path(
                    git_path="git://cat/sk",
                    risk_tags=[],
                )
        assert result is None

    def test_exception_returns_none(self):
        mock_repo = MagicMock()
        mock_repo.get_by_git_path.side_effect = RuntimeError("db down")
        svc = _make_skill_scan_service(skill_repository=mock_repo)
        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="dev"):
            result = svc.update_skill_metadata_by_git_path(
                git_path="git://cat/sk",
                risk_tags=[],
            )
        assert result is None


# ---------------------------------------------------------------------------
# _calculate_seconds_until_target_time
# ---------------------------------------------------------------------------

class TestCalculateSecondsUntilTargetTime:
    def test_returns_positive_seconds(self):
        svc = _make_skill_scan_service()
        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="dev"):
            secs = svc._calculate_seconds_until_target_time()
        assert secs > 0

    def test_prod_adds_2_hour_offset(self):
        svc = _make_skill_scan_service()
        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="prod"):
            secs_prod = svc._calculate_seconds_until_target_time()
        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="dev"):
            secs_dev = svc._calculate_seconds_until_target_time()
        # Both should be positive and within 24h
        assert 0 < secs_prod < 86400
        assert 0 < secs_dev < 86400


# ---------------------------------------------------------------------------
# _get_actual_task_time
# ---------------------------------------------------------------------------

class TestGetActualTaskTime:
    def test_non_prod_returns_configured_hour(self):
        svc = _make_skill_scan_service()
        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="pre"):
            hour, minute = svc._get_actual_task_time()
        from agentclaw.community.core.skill_center.services.skill_scan import DAILY_TASK_HOUR, DAILY_TASK_MINUTE
        assert hour == DAILY_TASK_HOUR
        assert minute == DAILY_TASK_MINUTE

    def test_prod_adds_2_hours(self):
        svc = _make_skill_scan_service()
        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="prod"):
            hour, minute = svc._get_actual_task_time()
        from agentclaw.community.core.skill_center.services.skill_scan import DAILY_TASK_HOUR
        assert hour == (DAILY_TASK_HOUR + 2) % 24


# ---------------------------------------------------------------------------
# scan_skill — successful case
# ---------------------------------------------------------------------------

class TestScanSkillSuccess:
    def test_calls_sdk_scan(self, tmp_path):
        svc = _make_skill_scan_service()
        svc._started = True
        skill_file = tmp_path / "skill.py"
        skill_file.write_text("# skill")
        mock_result = MagicMock()
        svc._sdk = MagicMock()
        svc._sdk.scan.return_value = mock_result

        result = svc.scan_skill(str(skill_file))
        svc._sdk.scan.assert_called_once_with(str(skill_file))
        assert result is mock_result
