from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.models import AllocatedDevice
from agentclaw.community.core.devices.services.baas_container_init import (
    BaasContainerInitializer,
    _deserialize_symbol,
)


def _device(*, symbol: str | None = None) -> AllocatedDevice:
    props = {
        "entity_id": "u-1",
        "entity_type": "staff",
        "client_id": "client-1",
    }
    if symbol is not None:
        props["symbol"] = symbol
    return AllocatedDevice(
        device_id="BOT-1",
        device_provider="baas",
        device_props=props,
    )


def _initializer() -> tuple[BaasContainerInitializer, MagicMock]:
    baas = MagicMock()
    baas.exec_command_on_bot.return_value = {
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
    }
    return BaasContainerInitializer(baas), baas


def test_run_serializes_required_steps_before_starting_services() -> None:
    initializer, baas = _initializer()
    symbol = json.dumps(
        [{"source": "./skills-local/demo", "target": "/runtime/skills/demo"}]
    )

    initializer.run(
        bot_uuid="BOT-1",
        device=_device(symbol=symbol),
        engine="openclaw",
        bot_type="desktop",
        bot_id="bot-1",
        owner_id="u-1",
        callback_token="callback-token",
        admins=["admin-1", "admin-2"],
    )

    calls = baas.exec_command_on_bot.call_args_list
    commands = [call.kwargs["cmd"] for call in calls]
    assert "install_dependency_file" in commands[0]
    assert "bootstrap_minimal.sh" in commands[0]
    assert "nohup" not in commands[1]
    assert "if [ -f /home/admin/bin/install_engine.sh ]" in commands[1]
    assert "install_engine.sh" in commands[1]
    assert "nohup" not in commands[2]
    assert "setup_supervisor_sync_service.sh openclaw" in commands[2]
    assert "nohup" not in commands[3]
    assert "setup_engine_dirs.sh openclaw" in commands[3]
    assert "skill-symlinks.conf" in commands[4]
    assert "supervisorctl pid" in commands[5]
    assert "nohup /home/admin/bin/start_service.sh" in commands[6]
    assert "--entity_id u-1 --entity_type staff" in commands[6]
    assert "--admins admin-1,admin-2" in commands[6]
    assert "starting_watchdog.sh" in commands[7]
    assert [call.kwargs["timeout_seconds"] for call in calls] == [
        300,
        300,
        120,
        120,
        30,
        70,
        30,
        30,
    ]


@pytest.mark.parametrize(
    ("failed_step", "expected_calls"),
    [
        ("bootstrap", 1),
        ("install_engine", 2),
        ("setup_supervisor_sync_service", 3),
        ("wait_supervisor_ready", 5),
        ("start_service", 6),
    ],
)
def test_required_step_failure_stops_later_steps(
    failed_step: str,
    expected_calls: int,
) -> None:
    initializer, baas = _initializer()

    def execute(**kwargs):
        command = kwargs["cmd"]
        matches = {
            "bootstrap": "bootstrap_minimal.sh",
            "install_engine": "install_engine.sh",
            "setup_supervisor_sync_service": "setup_supervisor_sync_service.sh",
            "wait_supervisor_ready": "supervisorctl pid",
            "start_service": "start_service.sh",
        }
        if matches[failed_step] in command:
            return {"exit_code": 9, "stderr": f"{failed_step} failed"}
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    baas.exec_command_on_bot.side_effect = execute

    with pytest.raises(RuntimeError, match=failed_step):
        initializer.run(
            bot_uuid="BOT-1",
            device=_device(),
            engine="openclaw",
            bot_type="desktop",
            bot_id="bot-1",
            owner_id="u-1",
            callback_token="callback-token",
            admins=None,
        )

    assert baas.exec_command_on_bot.call_count == expected_calls
    commands = [call.kwargs["cmd"] for call in baas.exec_command_on_bot.call_args_list]
    assert not any("starting_watchdog.sh" in command for command in commands)


def test_engine_dir_failure_remains_non_fatal() -> None:
    initializer, baas = _initializer()

    def execute(**kwargs):
        if "setup_engine_dirs.sh" in kwargs["cmd"]:
            return {"exit_code": 7, "stderr": "legacy image has no setup"}
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    baas.exec_command_on_bot.side_effect = execute

    initializer.run(
        bot_uuid="BOT-1",
        device=_device(),
        engine="openclaw",
        bot_type="desktop",
        bot_id="bot-1",
        owner_id="u-1",
        callback_token="callback-token",
        admins=None,
    )

    commands = [call.kwargs["cmd"] for call in baas.exec_command_on_bot.call_args_list]
    assert any("supervisorctl pid" in command for command in commands)
    assert any("start_service.sh" in command for command in commands)
    assert any("starting_watchdog.sh" in command for command in commands)


def test_exec_checked_rejects_missing_exit_code_and_non_dict_results() -> None:
    initializer, baas = _initializer()
    baas.exec_command_on_bot.side_effect = [{}, None]

    with pytest.raises(RuntimeError, match="exit_code=-1"):
        initializer._exec_checked(
            bot_uuid="BOT-1",
            cmd="first",
            timeout_seconds=1,
            step="first",
        )
    with pytest.raises(RuntimeError, match="exit_code=-1"):
        initializer._exec_checked(
            bot_uuid="BOT-1",
            cmd="second",
            timeout_seconds=1,
            step="second",
        )


def test_start_service_omits_optional_arguments_when_absent() -> None:
    initializer, baas = _initializer()

    initializer._start_baas_sandbox_service(
        bot_uuid="BOT-1",
        client_id="client-1",
        engine="openclaw",
    )

    start_command = baas.exec_command_on_bot.call_args_list[0].kwargs["cmd"]
    assert "--bot_type" not in start_command
    assert "--bot_id" not in start_command
    assert "--owner_id" not in start_command
    assert "--entity_id" not in start_command
    assert "--stage" not in start_command
    assert "--admins" not in start_command


def test_empty_symbol_skips_config_and_deserializer_handles_invalid_values() -> None:
    initializer, baas = _initializer()

    initializer._create_baas_skill_symlink_conf("BOT-1", None)

    baas.exec_command_on_bot.assert_not_called()
    assert _deserialize_symbol(None) == []
    assert _deserialize_symbol([]) == []  # type: ignore[arg-type]
    assert _deserialize_symbol("not-json") == []
