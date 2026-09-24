import os
from pathlib import Path
import subprocess
import sys

import pytest

PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLATFORM))
from clawevolve_runtime.runner_environment import resolve_runner_environment


def test_selection_is_explicit_and_does_not_treat_dev_as_local():
    assert not resolve_runner_environment({"NODE_ENV": "dev"}).allow_loopback_download()
    assert resolve_runner_environment({"SECBAAS_SANDBOX_BACKEND": "local_proc"}).allow_loopback_download()
    assert not resolve_runner_environment({"CLAWEVOLVE_RUNNER_ENVIRONMENT": "container", "SECBAAS_SANDBOX_BACKEND": "local_proc"}).allow_loopback_download()
    with pytest.raises(ValueError):
        resolve_runner_environment({"CLAWEVOLVE_RUNNER_ENVIRONMENT": "unknown"})


def test_local_requires_existing_workspace_inside_selected_bot(tmp_path):
    state = tmp_path / "bot state"
    workspace = state / "work space"
    workspace.mkdir(parents=True)
    env = {"CLAWEVOLVE_RUNNER_ENVIRONMENT": "local", "OPENCLAW_STATE_DIR": str(state), "OPENCLAW_WORKSPACE_DIR": str(workspace)}
    startup = resolve_runner_environment(env).startup_environment()
    assert startup["OPENCLAW_WORKSPACE"] == str(workspace)
    assert startup["OPENCLAW_STATE_DIR"] == str(state)
    assert startup["OPENCLAW_CONFIG_PATH"] == str(state / "openclaw.json")
    for invalid in ({"OPENCLAW_STATE_DIR": ""}, {"OPENCLAW_WORKSPACE_DIR": str(tmp_path)}, {"OPENCLAW_WORKSPACE_DIR": str(state / "absent")}):
        with pytest.raises((ValueError, FileNotFoundError)):
            resolve_runner_environment(env | invalid).startup_environment()


def test_shared_launcher_executes_local_handler_without_container_maintenance(tmp_path):
    state = tmp_path / "bot state"
    workspace = state / "work space"
    workspace.mkdir(parents=True)
    output = tmp_path / "result.txt"
    logfile = tmp_path / "runner.log"
    launcher = PLATFORM.parent / "scripts/clawevolve_task_launcher.sh"
    env = dict(os.environ) | {"CLAWEVOLVE_RUNNER_ENVIRONMENT": "local", "OPENCLAW_STATE_DIR": str(state), "OPENCLAW_WORKSPACE_DIR": str(workspace)}
    result = subprocess.run(["bash", str(launcher), "--task-id", "EV-TEST", "--step-id", "STEP-TEST", "--log-file", str(logfile), "--", "bash", "-c", 'printf "%s" "$OPENCLAW_WORKSPACE" > "$1"', "handler", str(output)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert output.read_text() == str(workspace)
    assert "runtime ready; starting handler" in logfile.read_text()
    assert not (workspace / ".runtime_maintenance_v1.json").exists()


def test_container_keeps_admin_requirement_and_container_defaults():
    env = resolve_runner_environment({})
    startup = env.startup_environment()
    assert startup["OPENCLAW_WORKSPACE"] == "/home/admin/.openclaw/workspace"
    assert startup["OPENCLAW_HOME"] == "/home/admin/.openclaw"
    assert startup["OPENCLAW_STATE_DIR"] == "/home/admin/.openclaw"
    assert startup["OPENCLAW_CONFIG_PATH"] == "/home/admin/.openclaw/openclaw.json"
    env.validate_user("admin")
    with pytest.raises(ValueError):
        env.validate_user("other")
    assert env.bench_environment(Path("/workspace")) is None


def test_container_uses_one_state_root_independent_of_task_workspace():
    env = resolve_runner_environment({
        "OPENCLAW_WORKSPACE": "/runtime/bot/clawevolve_workspaces/EV-1/workspace",
        "OPENCLAW_STATE_DIR": "/runtime/bot",
        "OPENCLAW_HOME": "/legacy/state-alias",
    })

    startup = env.startup_environment()

    assert startup["OPENCLAW_HOME"] == "/runtime/bot"
    assert startup["OPENCLAW_STATE_DIR"] == "/runtime/bot"
    assert startup["OPENCLAW_CONFIG_PATH"] == "/runtime/bot/openclaw.json"


def test_container_maintenance_uses_explicit_state_and_config_paths():
    source = (PLATFORM / "clawevolve_runtime" / "container_runner_environment.sh").read_text()

    assert '${OPENCLAW_HOME}/openclaw.json' not in source
    assert '${OPENCLAW_CONFIG_PATH:-${OPENCLAW_STATE_DIR}/openclaw.json}' in source
    assert '--openclaw-home "$OPENCLAW_STATE_DIR"' in source


def test_container_preparation_preserves_arguments_and_exit_status(monkeypatch, tmp_path):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 17)
    monkeypatch.setattr(subprocess, "run", run)
    env = resolve_runner_environment({"PATH": "/bin"})
    args = ["--task-id", "EV-1", "--step-id", "STEP-1", "--log-file", str(tmp_path / "log"), "--runtime-maintenance", "true", "--preflight-active-evolve-guard", "true", "--refresh-gateway-before-handler", "false"]
    assert env.prepare_task(args, tmp_path) == 17
    assert calls[0][1]["env"]["TASK_ID"] == "EV-1"
    assert calls[0][1]["env"]["SCRIPT_DIR"] == str(tmp_path)
    assert calls[0][1]["env"]["PREFLIGHT_ACTIVE_EVOLVE_GUARD"] == "true"
    with pytest.raises(ValueError):
        env.prepare_task(args + ["--unknown", "value"], tmp_path)
