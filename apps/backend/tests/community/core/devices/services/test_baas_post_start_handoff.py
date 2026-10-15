"""Post-publish initialization must not run the one-shot startup twice."""

import json
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.models import AllocatedDevice
from agentclaw.community.core.devices.services.baas_container_init import BaasContainerInitializer


def run_handoff(service, *, engine="openclaw", kind="personal", props=None):
    initializer = BaasContainerInitializer(service)
    initializer.run(
        bot_uuid="BOT-test",
        device=AllocatedDevice(device_id="BOT-test", device_provider="baas", device_props=props or {}),
        engine=engine, bot_type=kind, bot_id="bot-test", owner_id="owner-test",
        callback_token="callback-fixture", startup_identity="publish-test", admins=None,
    )
    return [call.kwargs["cmd"] for call in service.exec_command_on_bot.call_args_list]


@pytest.mark.parametrize("engine,kind", [("openclaw", "personal"), ("openclaw", "service"), ("hermes", "personal")])
def test_handoff_preserves_configuration_and_watchdog_without_starting_service(engine, kind):
    service = MagicMock()
    service.exec_command_on_bot.return_value = {"exit_code": 0}
    commands = run_handoff(service, engine=engine, kind=kind)
    assert not any("/start_service.sh " in command for command in commands)
    assert not any(".starting_done" in command for command in commands)
    assert not any(".credentials" in command for command in commands)
    assert any("setup_supervisor_sync_service.sh" in command for command in commands)
    assert any("setup_engine_dirs.sh" in command for command in commands)
    assert not any("setup_skill_symlinks.sh" in command for command in commands)
    watchdog = [command for command in commands if "/starting_watchdog.sh " in command]
    assert len(watchdog) == 1
    assert "--client_id BOT-test" in watchdog[0]
    assert "--token callback-fixture" in watchdog[0]
    assert "--startup_identity" not in watchdog[0]


def test_handoff_preserves_current_pool_callback_identity():
    service = MagicMock()
    service.exec_command_on_bot.return_value = {"exit_code": 0}
    commands = run_handoff(service, props={"envs": {"AGENTCLAW_SKILLS_LAYOUT": "pool"}})
    assert "--startup_identity publish-test" in commands[-1]


@pytest.mark.parametrize("engine,script", [
    ("openclaw", "/home/admin/bin/setup_skill_symlinks.sh --engine openclaw"),
    ("hermes", "/home/admin/bin/setup_skill_symlinks.sh --engine hermes"),
    ("aicoding", "/home/admin/bin/aicoding/setup_skill_symlinks.sh"),
    ("claude_code", "/home/admin/bin/aicoding/setup_skill_symlinks.sh"),
])
def test_nonempty_symbol_is_written_then_applied_as_admin_before_watchdog(engine, script):
    service = MagicMock()
    service.exec_command_on_bot.return_value = {"exit_code": 0}
    symbol = json.dumps([{"source": "skills-local/example", "target": "example"}])
    commands = run_handoff(service, engine=engine, props={"symbol": symbol})
    write_index = next(i for i, cmd in enumerate(commands) if "EOFCONF" in cmd)
    apply_index = next(i for i, cmd in enumerate(commands) if script in cmd)
    assert write_index < apply_index < len(commands) - 1
    assert "su admin -c" in commands[apply_index]
    assert "nohup" not in commands[apply_index]
    assert "/starting_watchdog.sh " in commands[-1]


def test_symbol_application_failure_does_not_dispatch_success_watchdog():
    service = MagicMock()
    def execute(**kwargs):
        return {"exit_code": 1 if "setup_skill_symlinks.sh" in kwargs["cmd"] else 0}
    service.exec_command_on_bot.side_effect = execute
    symbol = json.dumps([{"source": "skills-local/example", "target": "example"}])
    with pytest.raises(RuntimeError, match="skill symlink application failed"):
        run_handoff(service, props={"symbol": symbol})
    assert not any("/starting_watchdog.sh " in call.kwargs["cmd"] for call in service.exec_command_on_bot.call_args_list)


def test_symbol_config_write_failure_prevents_application_and_confirmation():
    service = MagicMock()
    def execute(**kwargs):
        return {"exit_code": 1 if "EOFCONF" in kwargs["cmd"] else 0}
    service.exec_command_on_bot.side_effect = execute
    symbol = json.dumps([{"source": "skills-local/example", "target": "example"}])
    with pytest.raises(RuntimeError, match="symlink config write failed"):
        run_handoff(service, props={"symbol": symbol})
    commands = [call.kwargs["cmd"] for call in service.exec_command_on_bot.call_args_list]
    assert not any("setup_skill_symlinks.sh" in cmd or "/starting_watchdog.sh " in cmd for cmd in commands)
