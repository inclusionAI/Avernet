"""Tests for BaasService start-command composition.

Covers the install_engine step that was added so the BaaS path mirrors the
arca path (script split out of start_service.sh, marker file coordinates with
setup_supervisor_sync_service.sh).
"""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.plugins.local.http_client import LocalHttpClient


def _make_service() -> BaasService:
    """Build a ``BaasService`` with mocks — all deps are required now."""
    return BaasService(
        startup_script_reader=MagicMock(**{"get_body.return_value": ""}),
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
        """Every existing bot must compose exactly the string it composes today.

        Reconstructed from the four helpers rather than compared against
        another call to the same function — comparing ``_cmd("")`` with
        ``_cmd()`` passes even if both are wrong.
        """
        svc = _make_service()
        expected = (
            f"{svc._get_bootstrap_cmp()} && ({svc._get_install_engine_cmd()}) && "
            f" {svc._get_start_sandbox_service_cmd(
                'openclaw', '/tmp/src', 'agent', 'bot-1', 'owner-1',
                'entity-1', 'user', 'online', '1', False, None,
            )} && {svc._get_start_watchdog_cmd()}"
        )
        assert self._cmd("") == expected

    def test_no_script_adds_nothing_from_the_user_stage(self):
        cmd = self._cmd("")
        assert "__OCB_RC" not in cmd
        assert "base64 -d" not in cmd
        assert "mktemp" not in cmd
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
            STARTUP_SCRIPT_KILL_GRACE_SECONDS,
            STARTUP_SCRIPT_TIMEOUT_SECONDS,
        )

        assert (
            f"timeout -k {STARTUP_SCRIPT_KILL_GRACE_SECONDS} "
            f"{STARTUP_SCRIPT_TIMEOUT_SECONDS} bash"
        ) in self._cmd("echo hi")

    def test_the_timeout_cannot_be_defeated_by_trapping_term(self):
        """``timeout N`` alone sends TERM and then waits forever if the script
        traps it — and nothing upstream bounds the hook (BaaS ignores
        ``after_create_hook_wait_seconds`` and the wrapper is nohup'd), so this
        one flag is the whole guarantee that a start terminates."""
        from agentclaw.community.core.service_bot.services.baas_service import (
            STARTUP_SCRIPT_KILL_GRACE_SECONDS,
        )

        cmd = self._cmd("trap '' TERM; sleep 100000\n")
        assert "timeout -k " in cmd
        assert STARTUP_SCRIPT_KILL_GRACE_SECONDS > 0

    def test_timeout_leaves_headroom_in_the_create_budget(self):
        """The device only reports once this sequence exits; the poller gives
        up at 600s (_CREATE_PUBLISH_TIMEOUT_SECONDS).

        Two bounds, because there are two cases. A script that exits or accepts
        TERM is capped at the deadline, and that is the one sized at half the
        budget so the platform steps and the callback's retries have the other
        half. A script that *traps* TERM runs to the KILL instead; that case
        only has to remain bounded and clear of the poller, not stay under
        half.
        """
        from agentclaw.community.core.service_bot.services.baas_service import (
            STARTUP_SCRIPT_KILL_GRACE_SECONDS,
            STARTUP_SCRIPT_TIMEOUT_SECONDS,
        )
        from agentclaw.community.core.devices.services.baas_publish_task_handlers import (
            _CREATE_PUBLISH_TIMEOUT_SECONDS,
        )

        assert STARTUP_SCRIPT_TIMEOUT_SECONDS <= _CREATE_PUBLISH_TIMEOUT_SECONDS / 2

        hard_cap = STARTUP_SCRIPT_TIMEOUT_SECONDS + STARTUP_SCRIPT_KILL_GRACE_SECONDS
        assert hard_cap < _CREATE_PUBLISH_TIMEOUT_SECONDS

    def test_output_goes_to_its_own_log(self):
        cmd = self._cmd("echo hi")
        assert "/home/admin/logs/startup_script.log" in cmd
        assert "/home/admin/logs/install_engine.log" in cmd  # platform's, untouched

    def test_user_stage_comes_after_the_whole_platform_chain(self):
        cmd = self._cmd("echo hi")
        assert cmd.index("start_service.sh") < cmd.index("base64 -d")


class TestStartupScriptReachesEveryStartPath:
    """A script is written *after* a bot exists, so restart is the path that
    actually delivers it. Threading a parameter through callers is how that
    path gets missed — resolution happens centrally instead."""

    _BOT = {
        "id": 501,
        "bot_id": "bot-1",
        "bot_name": "b",
        "entity_id": "ent-1",
        "entity_type": "staff",
        "active_engine": "openclaw",
        "bot_type": "personal",
    }

    def _service_with_script(self, body: str = "echo provisioned"):
        svc = _make_service()
        reader = MagicMock()
        reader.get_body.return_value = body
        svc._startup_script_reader = reader
        return svc, reader

    def _hook(self, svc, **kwargs) -> str:
        payload = svc._build_create_bot_payload(
            bot=dict(self._BOT),
            owner_id="owner-1",
            request_id="req-1",
            device_count=1,
            migration_path="",
            **kwargs,
        )
        return payload["config"]["deploy_config"]["after_create_cmd_hook"]

    def test_payload_builder_resolves_the_script_when_not_passed(self):
        import base64 as _b64

        svc, reader = self._service_with_script()
        hook = self._hook(svc)
        assert _b64.b64encode(b"echo provisioned").decode() in hook
        reader.get_body.assert_called_once_with(entity_id="ent-1", bot_id="bot-1")

    def test_explicit_empty_string_wins_over_the_reader(self):
        """An explicit argument is a deliberate override, not 'unset'."""
        svc, reader = self._service_with_script()
        hook = self._hook(svc, startup_script="")
        assert "__OCB_RC" not in hook
        reader.get_body.assert_not_called()

    def test_a_bot_with_nothing_stored_composes_todays_chain(self):
        """The rollback path: clearing the script restores the old command."""
        assert "__OCB_RC" not in self._hook(_make_service())

    def test_the_reader_is_required_at_construction(self):
        """Optional, a composition that forgets to wire the reader constructs
        fine and then skips the stored script on every start — invisibly, which
        is the exact failure this feature exists to prevent. Required, the same
        mistake is a TypeError naming the argument."""
        with pytest.raises(TypeError, match="startup_script_reader"):
            BaasService(
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

    def test_a_lookup_failure_fails_the_start_rather_than_omitting_the_script(self):
        """Swallowing this produced a bot that starts, reports ready, and is
        silently unprovisioned — indistinguishable from one with no script.

        "Degrades rather than blocks" is about the script's own execution, not
        about quietly dropping a script that exists.
        """
        svc = _make_service()
        reader = MagicMock()
        reader.get_body.side_effect = RuntimeError("db down")
        svc._startup_script_reader = reader

        with pytest.raises(RuntimeError):
            svc._resolve_startup_script(entity_id="ent-1", bot_id="bot-1")

    def test_a_bot_with_no_identity_is_not_a_failure(self):
        """``""`` still means "no script" for the remaining non-error case.

        An unwired reader is no longer one of them — it cannot be constructed.
        """
        svc, reader = self._service_with_script()
        assert (
            svc._resolve_startup_script(entity_id="", bot_id="bot-1") == ""
        )
        assert (
            svc._resolve_startup_script(entity_id="ent-1", bot_id="") == ""
        )
        reader.get_body.assert_not_called()

    def test_a_bot_being_created_resolves_to_no_script(self):
        """The device-allocation callers hand-build a bot dict and call this
        *before* any ac_bots row exists.

        Nothing can have stored a script for a bot that does not exist yet — a
        write requires the bot to already be there — so the ordinary read
        answers "" without the caller needing a special case. Pinned because an
        earlier version demanded an id here and broke every singlebox and BaaS
        device bot creation.
        """
        svc, reader = self._service_with_script()
        reader.get_body.return_value = ""

        assert (
            svc._resolve_startup_script(entity_id="ent-1", bot_id="bot-1") == ""
        )

    def test_an_existing_bot_allocated_a_device_still_gets_its_script(self):
        """The device-allocation callers hand-build a bot dict, with no
        ``ac_bots`` row to read an id from, and the same allocation runs for a
        bot that already exists (``bot_id or "default"``). They can still use
        the ordinary read: ``(entity_id, bot_id)`` is the whole key, and it
        names one bot for the life of the data.
        """
        svc, reader = self._service_with_script("echo provisioned")

        assert (
            svc._resolve_startup_script(entity_id="ent-1", bot_id="bot-1")
            == "echo provisioned"
        )
        reader.get_body.assert_called_once_with(entity_id="ent-1", bot_id="bot-1")

    def test_missing_entity_id_resolves_to_no_script(self):
        svc, _ = self._service_with_script()
        assert svc._resolve_startup_script(entity_id="", bot_id="bot-1") == ""


class TestStartupScriptPrivilege:
    """The hook runs as root; the user stage must not."""

    def _cmd(self, body: str = "echo hi") -> str:
        return _make_service()._get_start_cmd(
            bot_id="bot-1", owner_id="owner-1", entity_id="entity-1",
            entity_type="user", migration_pat="/tmp/src", bot_type="agent",
            engine="openclaw", stage="online", version="1", startup_script=body,
        )

    def test_user_stage_runs_as_admin_like_every_platform_step(self):
        assert "su admin -c '" in self._cmd()

    def test_drop_path_is_not_world_writable(self):
        """A /tmp drop path lets another user pre-create the file and race the
        write, turning the exec into someone else's script."""
        cmd = self._cmd()
        stage = cmd.split("__OCB_RC=$?")[1]
        assert "mktemp" in stage      # unpredictable, admin-owned
        assert 'rm -f "$f"' in stage  # and removed after the run
        assert "/home/admin/.ocb" not in stage  # not persisted on NAS

    def test_su_wrapper_cannot_be_closed_by_the_body(self):
        """Everything inside the single-quoted su string is base64 or a fixed
        path, so a body full of quotes cannot break out of it."""
        body = "echo 'quoted'; rm -rf /"
        cmd = self._cmd(body)
        # Slice from the user stage: the platform's bootstrap step is itself a
        # `su admin -c '...'`, so the first match is not the one under test.
        user_stage = cmd[cmd.index("__OCB_RC=$?") :]
        start = user_stage.index("su admin -c '") + len("su admin -c '")
        inner = user_stage[start : user_stage.index("' || true")]
        assert "'" not in inner
        assert "rm -rf" not in inner  # the body survives only as base64


class TestPayloadLogRedaction:
    """Create payloads are logged at INFO and carry the user's script."""

    def _payload(self, hook: str) -> dict:
        return {
            "name": "b",
            "config": {
                "entity_id": "ent-1",
                "deploy_config": {
                    "after_create_cmd_hook": hook,
                    "ttl_in_minutes": 60,
                },
            },
        }

    def test_only_the_user_stage_is_elided(self):
        from agentclaw.community.core.service_bot.services.baas_service import (
            _redact_payload_for_log,
        )

        hook = _make_service()._get_start_cmd(
            bot_id="bot-1", owner_id="owner-1", entity_id="entity-1",
            entity_type="user", migration_pat="/tmp/src", bot_type="agent",
            engine="openclaw", stage="online", version="1",
            startup_script="curl -H 'Authorization: Bearer SECRET_TOKEN' x",
        )
        payload = self._payload(hook)
        redacted = _redact_payload_for_log(payload)
        logged = redacted["config"]["deploy_config"]["after_create_cmd_hook"]

        import base64 as _b64
        encoded = _b64.b64encode(
            b"curl -H 'Authorization: Bearer SECRET_TOKEN' x"
        ).decode()
        assert encoded not in str(redacted)
        assert "<startup script elided" in logged
        # The reported size is the caller's script, not the wrapper.
        assert "46 bytes" in logged
        # Everything the platform authored survives — the boot chain and the
        # scaffolding around the user stage.
        assert "bootstrap_minimal.sh" in logged
        assert "start_service.sh" in logged
        assert "su admin -c" in logged
        assert 'if [ "$__OCB_RC" -eq 0 ]' in logged
        assert "exit $__OCB_RC" in logged
        # Original untouched, siblings preserved.
        assert redacted["config"]["deploy_config"]["ttl_in_minutes"] == 60
        assert encoded in payload["config"]["deploy_config"]["after_create_cmd_hook"]

    def test_hook_without_a_user_stage_is_logged_verbatim(self):
        """A no-script bot's payload must log exactly as it does today."""
        from agentclaw.community.core.service_bot.services.baas_service import (
            _redact_payload_for_log,
        )

        payload = self._payload("bootstrap && start_service.sh")
        assert _redact_payload_for_log(payload) == payload

    def test_payload_without_a_deploy_config_passes_through(self):
        from agentclaw.community.core.service_bot.services.baas_service import (
            _redact_payload_for_log,
        )

        payload = {"name": "b", "operator": "u1"}
        assert _redact_payload_for_log(payload) == payload
