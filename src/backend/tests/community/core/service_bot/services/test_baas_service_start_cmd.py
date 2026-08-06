"""Tests for BaasService start-command composition.

Covers the install_engine step that was added so the BaaS path mirrors the
arca path (script split out of start_service.sh, marker file coordinates with
setup_supervisor_sync_service.sh).
"""
import subprocess
from unittest.mock import MagicMock

from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.plugins.local.http_client import LocalHttpClient


def _make_service() -> BaasService:
    """Build a ``BaasService`` with mocks — all deps are required now."""
    return BaasService(
        baas_api_base="http://test",
        tenant="test",
        template_uuid="test",
        bot_repo=MagicMock(),
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(),
        storage_path=MagicMock(),
        device_binding_repo=MagicMock(),
        default_ttl_minutes=10080,
        sandbox_registry=MagicMock(),
        http_client=LocalHttpClient(),
        general_http_client=LocalHttpClient(base_url=""),
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
        outbound_rule_provider=MagicMock(),
    )


class TestGetInstallEngineCmd:
    @staticmethod
    def _run_with_paths(cmd, *, script_path, log_path, prefix=""):
        test_cmd = cmd.replace(
            "/home/admin/bin/install_engine.sh",
            str(script_path),
        ).replace(
            "/home/admin/logs/install_engine.log",
            str(log_path),
        )
        return subprocess.run(
            ["bash", "-c", f"{prefix}{test_cmd}"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_uses_install_engine_script_path(self):
        cmd = _make_service()._get_install_engine_cmd()
        assert "/home/admin/bin/install_engine.sh" in cmd

    def test_has_existence_guard(self):
        cmd = _make_service()._get_install_engine_cmd()
        assert "if [ -f /home/admin/bin/install_engine.sh ]" in cmd
        assert "skip" in cmd

    def test_runs_synchronously_no_nohup(self):
        # BaaS chains commands with &&; install_engine must finish (and write
        # its marker) before setup_supervisor_sync_service.sh runs.
        cmd = _make_service()._get_install_engine_cmd()
        assert "nohup" not in cmd
        # No fire-and-forget backgrounding (`bash ... &`). 2>&1 is a redirect,
        # not a background, so it's fine.
        assert "& " not in cmd
        assert not cmd.rstrip().endswith("&")

    def test_redirects_to_log(self):
        cmd = _make_service()._get_install_engine_cmd()
        assert "/home/admin/logs/install_engine.log" in cmd

    def test_success_keeps_zero_exit_and_does_not_echo_log(self, tmp_path):
        script_path = tmp_path / "install_engine.sh"
        log_path = tmp_path / "install_engine.log"
        script_path.write_text("echo install-ok\nexit 0\n", encoding="utf-8")

        result = self._run_with_paths(
            _make_service()._get_install_engine_cmd(),
            script_path=script_path,
            log_path=log_path,
        )

        assert result.returncode == 0
        assert result.stderr == ""
        assert log_path.read_text(encoding="utf-8") == "install-ok\n"

    def test_failure_returns_original_exit_and_echoes_log_tail(self, tmp_path):
        script_path = tmp_path / "install_engine.sh"
        log_path = tmp_path / "install_engine.log"
        script_path.write_text("echo precise-failure\nexit 7\n", encoding="utf-8")

        result = self._run_with_paths(
            _make_service()._get_install_engine_cmd(),
            script_path=script_path,
            log_path=log_path,
        )

        assert result.returncode == 7
        assert "[install_engine] failed; last log lines:" in result.stderr
        assert "precise-failure" in result.stderr

    def test_tail_failure_cannot_replace_install_exit(self, tmp_path):
        script_path = tmp_path / "install_engine.sh"
        log_path = tmp_path / "install_engine.log"
        script_path.write_text("echo original-failure\nexit 9\n", encoding="utf-8")

        result = self._run_with_paths(
            _make_service()._get_install_engine_cmd(),
            script_path=script_path,
            log_path=log_path,
            prefix="tail() { return 42; }; ",
        )

        assert result.returncode == 9


class TestGetStartCmdOrdering:
    def test_install_engine_between_bootstrap_and_start(self):
        cmd = _make_service()._get_start_cmd(
            bot_id="bot-1",
            owner_id="owner-1",
            entity_id="entity-1",
            entity_type="user",
            migration_pat="/tmp/src",
            bot_type="agent",
            engine="openclaw",
            stage="online",
            version="1",
        )
        bootstrap_idx = cmd.index("bootstrap_minimal.sh")
        install_idx = cmd.index("install_engine.sh")
        start_idx = cmd.index("start_service.sh")
        assert bootstrap_idx < install_idx < start_idx

    def test_steps_chained_with_and(self):
        cmd = _make_service()._get_start_cmd(
            bot_id="bot-1",
            owner_id="owner-1",
            entity_id="entity-1",
            entity_type="user",
            migration_pat="/tmp/src",
            bot_type="agent",
            engine="openclaw",
            stage="online",
            version="1",
        )
        # Each adjacent pair of steps is joined by &&; spot-check the new edge.
        assert "&&" in cmd
        # install_engine sits in its own subshell group, then && start_cmd.
        assert "install_engine.sh" in cmd
        # The substring between install_engine and start_service must
        # contain a chained `&&` (not `;` or a backgrounded `&`).
        between = cmd[cmd.index("install_engine.sh") : cmd.index("start_service.sh")]
        assert "&&" in between


class TestGetStartSandboxServiceCmdNasFlag:
    def test_includes_use_nas_when_home_dir_storage_enabled(self):
        service = _make_service()
        service._get_set_read_only_rule = MagicMock(return_value="")

        cmd = service._get_start_sandbox_service_cmd(
            engine="openclaw",
            migration_path="/tmp/src",
            bot_type="agent",
            bot_id="bot-1",
            owner_id="owner-1",
            entity_id="entity-1",
            entity_type="staff",
            stage="online",
            version="1",
            mount_home_dir_storage=True,
        )

        assert " --useNas true" in cmd


class TestGetStartSandboxServiceCmdSourceDir:
    def test_omits_source_dir_when_migration_path_empty(self):
        service = _make_service()
        service._get_set_read_only_rule = MagicMock(return_value="")

        cmd = service._get_start_sandbox_service_cmd(
            engine="openclaw",
            migration_path="",
            bot_type="personal",
            bot_id="bot-1",
            owner_id="owner-1",
            entity_id="entity-1",
            entity_type="staff",
            stage="online",
            version="1",
            mount_home_dir_storage=False,
        )

        assert " --source_dir " not in cmd
        assert " --bot_id bot-1" in cmd
