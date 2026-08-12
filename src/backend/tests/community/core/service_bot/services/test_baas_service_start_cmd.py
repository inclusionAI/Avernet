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


class TestStartupScriptSegment:
    """The per-bot startup script appended to the boot chain (issue #926)."""

    _ARGS = dict(
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

    def _cmd(self, startup_script: str = "") -> str:
        return _make_service()._get_start_cmd(
            **self._ARGS, startup_script=startup_script
        )

    # --- the no-script guarantee -------------------------------------------

    def test_no_script_is_byte_identical_to_the_bare_chain(self):
        """Every existing bot must compose exactly the string it composes today."""
        assert self._cmd("") == self._cmd()

    def test_no_script_adds_nothing_from_the_user_stage(self):
        cmd = self._cmd("")
        assert "__OCB_RC" not in cmd
        assert "base64 -d" not in cmd
        assert "ocb_startup_script.sh" not in cmd
        assert "\n" not in cmd  # still the single-line string it always was

    # --- exit-status handling ----------------------------------------------

    def test_platform_status_is_captured_before_the_user_stage(self):
        cmd = self._cmd("echo hi")
        rc_idx = cmd.index("__OCB_RC=$?")
        user_idx = cmd.index("base64 -d")
        assert rc_idx < user_idx

    def test_platform_status_is_reasserted_as_the_final_exit(self):
        """Without this, `|| true` would report SUCCESS for a failed boot."""
        cmd = self._cmd("echo hi")
        assert cmd.rstrip().endswith("exit $__OCB_RC")

    def test_user_stage_cannot_be_the_last_command(self):
        cmd = self._cmd("echo hi")
        assert cmd.index("|| true") < cmd.index("exit $__OCB_RC")

    def test_user_stage_is_skipped_when_the_boot_chain_failed(self):
        cmd = self._cmd("echo hi")
        assert 'if [ "$__OCB_RC" -eq 0 ]; then' in cmd
        guard_idx = cmd.index('if [ "$__OCB_RC" -eq 0 ]')
        assert guard_idx < cmd.index("base64 -d")

    def test_user_stage_failure_is_swallowed(self):
        assert "|| true" in self._cmd("exit 127")

    # --- transfer safety ----------------------------------------------------

    def test_body_is_base64_encoded_not_interpolated(self):
        cmd = self._cmd("echo hello")
        assert "echo hello" not in cmd
        import base64 as _b64
        assert _b64.b64encode(b"echo hello").decode() in cmd

    def test_body_with_shell_metacharacters_round_trips_byte_exact(self):
        import base64 as _b64

        body = "#!/bin/bash\necho '$(id)' \"unbalanced\nHOOK_SCRIPT_EOF\n"
        cmd = self._cmd(body)
        encoded = _b64.b64encode(body.encode()).decode()
        assert encoded in cmd
        assert _b64.b64decode(encoded).decode() == body

    def test_encoded_body_contains_no_shell_metacharacters(self):
        """base64's alphabet is inert in shell — that is why it is used here."""
        import base64 as _b64
        import re

        body = "rm -rf / ; $(curl evil) `id` 'quote\" \\backslash\n"
        cmd = self._cmd(body)
        encoded = _b64.b64encode(body.encode()).decode()
        assert re.fullmatch(r"[A-Za-z0-9+/=]+", encoded)
        assert encoded in cmd

    def test_placeholder_shaped_body_is_not_substitutable_by_baas(self):
        """BaaS regex-substitutes {token}/{client_id} across the whole hook.

        The platform steps rely on that — they ship those placeholders on
        purpose. The user body must not: base64 has no braces, so a caller's
        ``{token}`` survives as data instead of being rewritten to a device id
        in transit. Assert on the user segment, not the whole command.
        """
        import base64 as _b64

        body = "echo {token} {client_id} {unknown}\n"
        cmd = self._cmd(body)
        segment = cmd[cmd.index('if [ "$__OCB_RC" -eq 0 ]') :]
        assert "{token}" not in segment
        assert "{client_id}" not in segment
        assert _b64.b64encode(body.encode()).decode() in segment

    def test_multibyte_body_round_trips(self):
        import base64 as _b64

        body = "echo 你好世界\n"
        cmd = self._cmd(body)
        encoded = _b64.b64encode(body.encode("utf-8")).decode()
        assert encoded in cmd
        assert _b64.b64decode(encoded).decode("utf-8") == body

    # --- runtime bounds -----------------------------------------------------

    def test_user_stage_runs_under_a_timeout(self):
        from agentclaw.community.core.service_bot.services.baas_service import (
            STARTUP_SCRIPT_TIMEOUT_SECONDS,
        )

        assert f"timeout {STARTUP_SCRIPT_TIMEOUT_SECONDS} bash" in self._cmd("echo hi")

    def test_timeout_leaves_headroom_in_the_create_budget(self):
        """The device only reports once this sequence exits; the poller gives
        up at 600s (_CREATE_PUBLISH_TIMEOUT_SECONDS)."""
        from agentclaw.community.core.service_bot.services.baas_service import (
            STARTUP_SCRIPT_TIMEOUT_SECONDS,
        )
        from agentclaw.community.core.devices.services.baas_publish_task_handlers import (
            _CREATE_PUBLISH_TIMEOUT_SECONDS,
        )

        assert STARTUP_SCRIPT_TIMEOUT_SECONDS < _CREATE_PUBLISH_TIMEOUT_SECONDS / 2 + 1

    def test_output_goes_to_its_own_log(self):
        cmd = self._cmd("echo hi")
        assert "/home/admin/logs/startup_script.log" in cmd
        assert "/home/admin/logs/install_engine.log" in cmd  # platform's, untouched

    def test_user_stage_comes_after_the_whole_platform_chain(self):
        cmd = self._cmd("echo hi")
        assert cmd.index("start_service.sh") < cmd.index("base64 -d")
