"""Tests for BaasService start-command composition.

Covers the install_engine step that was added so the BaaS path mirrors the
arca path (script split out of start_service.sh, marker file coordinates with
setup_supervisor_sync_service.sh).
"""
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
        install_idx = cmd.rindex("install_engine.sh")
        start_idx = cmd.rindex("start_service.sh")
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
        between = cmd[cmd.rindex("install_engine.sh") : cmd.rindex("start_service.sh")]
        assert "&&" in between


class TestBootstrapReadinessGuard:
    def test_waits_for_container_init_before_bootstrap_decision(self):
        cmd = _make_service()._get_start_cmd(
            bot_id="bot-1",
            owner_id="owner-1",
            entity_id="entity-1",
            entity_type="user",
            migration_pat="",
            bot_type="personal",
            engine="claude_code",
            stage="online",
            version="1",
        )

        marker_idx = cmd.index("/var/run/agentclaw/.install_dependency_file")
        lock_idx = cmd.index(
            "_agentclaw_lock_dir=/var/run/agentclaw/.baas-bootstrap.lock"
        )
        repo_validation_idx = cmd.rindex("if _agentclaw_checkout_valid")
        bootstrap_idx = cmd.rindex("bootstrap_minimal.sh")
        install_idx = cmd.rindex("install_engine.sh")

        assert marker_idx < lock_idx < repo_validation_idx < bootstrap_idx < install_idx

    def test_reuses_complete_release_checkout_instead_of_unconditionally_bootstrapping(
        self,
    ):
        cmd = _make_service()._get_bootstrap_cmp()

        assert "git -C" in cmd
        assert "describe --tags --exact-match" in cmd
        assert "_agentclaw_release_tag_valid" in cmd
        assert "*_dev" in cmd
        assert "*_pre" in cmd
        assert "openClawEnterprise.properties" in cmd
        assert "cmp -s" in cmd
        assert "/home/admin/bin/install_engine.sh" in cmd
        assert "/home/admin/bin/start_service.sh" in cmd
        assert "reusing completed bootstrap checkout" in cmd
        assert "else su admin -c 'bash /home/admin/bin/bootstrap_minimal.sh'" in cmd

    def test_release_tag_validation_rejects_unknown_runtime_environment(self):
        cmd = _make_service()._get_bootstrap_cmp()

        assert (
            'prod) case "$_agentclaw_release_tag" in *_dev|*_pre) '
            "return 1;; *) return 0;; esac;;" in cmd
        )
        assert "*) return 1;; esac;" in cmd

    def test_serializes_checkout_validation_and_compensation(self):
        cmd = _make_service()._get_bootstrap_cmp()

        assert 'mkdir "$_agentclaw_lock_dir"' in cmd
        assert 'kill -0 "$_agentclaw_lock_pid"' in cmd
        assert "_agentclaw_cleanup_lock" in cmd
        assert "bootstrap lock timed out" in cmd

    def test_fails_closed_when_container_init_does_not_finish(self):
        cmd = _make_service()._get_bootstrap_cmp()

        assert "/proc/1/comm" in cmd
        assert "seq 1 120" in cmd
        assert "container initialization timed out" in cmd
        assert "exit 1" in cmd


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
