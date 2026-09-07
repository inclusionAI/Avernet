"""Startup command contract: never guess or silently move an existing cwd."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = (
    next(p for p in Path(__file__).resolve().parents if (p / "docker").is_dir())
    / "docker/agent/resolve_claude_workspace.py"
)


def run(home, *, pids=(), **values):
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "CLAUDE_CODE_DEFAULT_CWD",
            "RELAY_DEFAULT_CWD",
            "CLAUDE_CODE_INITIAL_CWD",
            "OPENCLAW_WORKSPACE_DIR",
        }
    }
    env.update(values)
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--home",
            str(home),
            *[part for pid in pids for part in ("--running-pid", str(pid))],
        ],
        env=env,
        capture_output=True,
        text=True,
    )


def test_new_bot_requires_explicit_creation_default(tmp_path):
    result = run(tmp_path)
    assert result.returncode != 0
    result = run(tmp_path, CLAUDE_CODE_INITIAL_CWD="/home/admin/.claude_code/workspace")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "/home/admin/.claude_code/workspace"


def test_existing_bot_retains_saved_cwd(tmp_path):
    content = "export CLAUDE_CODE_DEFAULT_CWD=/home/admin/.openclaw/workspace\n"
    (tmp_path / ".adaptorEnv").write_text(content)
    result = run(tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "/home/admin/.openclaw/workspace"
    assert (tmp_path / ".adaptorEnv").read_text() == content


def test_conflicting_existing_configuration_is_untouched(tmp_path):
    (tmp_path / ".adaptorEnv").write_text("export CLAUDE_CODE_DEFAULT_CWD=/old\n")
    (tmp_path / ".relayEnv").write_text("export RELAY_DEFAULT_CWD=/different\n")
    result = run(tmp_path)
    assert result.returncode != 0
    assert "conflict" in result.stderr.lower()
    assert (
        tmp_path / ".relayEnv"
    ).read_text() == "export RELAY_DEFAULT_CWD=/different\n"


def test_explicit_configuration_with_spaces(tmp_path):
    result = run(tmp_path, CLAUDE_CODE_DEFAULT_CWD="/home/admin/project files")
    assert result.returncode == 0
    assert result.stdout.strip() == "/home/admin/project files"


def test_new_default_is_resolved_by_engine_layout(tmp_path):
    result = run(tmp_path, CLAUDE_CODE_INITIAL_CWD="default")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(tmp_path / ".claude_code/workspace")


def test_running_process_disagreement_does_not_change_configuration_or_stop_process(
    tmp_path,
):
    content = "export CLAUDE_CODE_DEFAULT_CWD=/saved\n"
    (tmp_path / ".adaptorEnv").write_text(content)
    env = dict(
        os.environ,
        CLAUDE_CODE_DEFAULT_CWD="/running",
        RELAY_DEFAULT_CWD="/running",
        OPENCLAW_WORKSPACE_DIR="/running",
    )
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], env=env
    )
    try:
        result = run(tmp_path, pids=[process.pid])
        assert result.returncode != 0
        assert "conflict" in result.stderr.lower()
        assert (tmp_path / ".adaptorEnv").read_text() == content
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_running_process_agrees_with_saved_configuration(tmp_path):
    (tmp_path / ".adaptorEnv").write_text("export CLAUDE_CODE_DEFAULT_CWD=/retained\n")
    env = dict(
        os.environ,
        CLAUDE_CODE_DEFAULT_CWD="/retained",
        RELAY_DEFAULT_CWD="/retained",
        OPENCLAW_WORKSPACE_DIR="/retained",
    )
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], env=env
    )
    try:
        result = run(tmp_path, pids=[process.pid])
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "/retained"
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_invalid_or_missing_legacy_configuration_is_not_treated_as_new(tmp_path):
    for invalid in ["relative", "/", "/home/../other", "/home/$VARIABLE"]:
        assert run(tmp_path, CLAUDE_CODE_DEFAULT_CWD=invalid).returncode != 0
    (tmp_path / ".adaptorEnv").write_text("export ENGINE=claude_code\n")
    assert run(tmp_path, CLAUDE_CODE_INITIAL_CWD="default").returncode != 0


def test_explicit_and_saved_configuration_conflict(tmp_path):
    (tmp_path / ".adaptorEnv").write_text("export CLAUDE_CODE_DEFAULT_CWD=/retained\n")
    result = run(tmp_path, CLAUDE_CODE_DEFAULT_CWD="/changed")
    assert result.returncode != 0
    assert "conflict" in result.stderr.lower()


def test_start_script_rejects_running_conflict_before_rewriting_or_restarting(tmp_path):
    import shutil

    home = tmp_path / "home"
    home.mkdir()
    (home / "logs").mkdir()
    saved = "export CLAUDE_CODE_DEFAULT_CWD=/saved\n"
    (home / ".adaptorEnv").write_text(saved)
    (home / ".relayEnv").write_text("export RELAY_DEFAULT_CWD=/saved\n")
    # Relocate only image filesystem paths; execute the actual shell control
    # flow with a real running worker and a recording supervisor boundary.
    source = SCRIPT.parent / "start_claude_code.sh"
    script = tmp_path / "start_claude_code.sh"
    script.write_text(
        source.read_text()
        .replace("/home/admin", str(home))
        .replace("/opt/.venv/bin/python", sys.executable)
    )
    shutil.copyfile(SCRIPT, tmp_path / SCRIPT.name)
    shutil.copyfile(SCRIPT.parent / "util.sh", tmp_path / "util.sh")
    commands = tmp_path / "supervisor-calls"
    env = dict(
        os.environ,
        CLAUDE_CODE_DEFAULT_CWD="/running",
        RELAY_DEFAULT_CWD="/running",
        OPENCLAW_WORKSPACE_DIR="/running",
    )
    worker = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], env=env
    )
    sudo = tmp_path / "sudo"
    sudo.write_text(
        f'#!/bin/sh\necho "$*" >> "{commands}"\ncase "$*" in\n*"pid engine") echo {worker.pid} ;;\n*) echo 0 ;;\nesac\n'
    )
    sudo.chmod(0o755)
    launch_env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "CLAUDE_CODE_DEFAULT_CWD",
            "RELAY_DEFAULT_CWD",
            "OPENCLAW_WORKSPACE_DIR",
            "CLAUDE_CODE_INITIAL_CWD",
        }
    }
    launch_env["PATH"] = str(tmp_path) + os.pathsep + launch_env["PATH"]
    try:
        result = subprocess.run(
            ["bash", str(script)],
            env=launch_env,
            text=True,
            capture_output=True,
            timeout=15,
        )
        assert result.returncode != 0
        assert "conflict" in result.stderr.lower(), result.stdout + result.stderr
        assert (home / ".adaptorEnv").read_text() == saved
        assert (home / ".relayEnv").read_text() == "export RELAY_DEFAULT_CWD=/saved\n"
        assert worker.poll() is None
        assert all(" pid " in line for line in commands.read_text().splitlines())
    finally:
        worker.terminate()
        worker.wait(timeout=5)


@pytest.mark.parametrize("saved_cwd", ["/retained", "/conflicting"])
def test_supervisor_shell_checks_uvicorn_worker_environment(tmp_path, saved_cwd):
    # Stub only uvicorn's server loop, preserving the production shell ->
    # python -m uvicorn command line and real inherited process environment.
    (tmp_path / "uvicorn.py").write_text(
        "import time; print('ready', flush=True); time.sleep(30)"
    )
    (tmp_path / ".adaptorEnv").write_text(
        "export CLAUDE_CODE_DEFAULT_CWD=" + saved_cwd + "\n"
    )
    env = dict(
        os.environ,
        PYTHONPATH=str(tmp_path),
        CLAUDE_CODE_DEFAULT_CWD="/retained",
        RELAY_DEFAULT_CWD="/retained",
        OPENCLAW_WORKSPACE_DIR="/retained",
    )
    parent = subprocess.Popen(
        [
            "bash",
            "-c",
            '"$1" -m uvicorn engine.community.api.app:app || exit 1',
            "worker",
            sys.executable,
        ],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )
    import psutil

    try:
        assert parent.stdout.readline().strip() == "ready"
        workers = psutil.Process(parent.pid).children(recursive=True)
        assert workers
        result = run(tmp_path, pids=[parent.pid])
        if saved_cwd == "/retained":
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == "/retained"
        else:
            assert result.returncode != 0
            assert "conflict" in result.stderr.lower()
        assert parent.poll() is None
    finally:
        for worker in psutil.Process(parent.pid).children(recursive=True):
            worker.terminate()
        parent.terminate()
        parent.wait(timeout=5)
