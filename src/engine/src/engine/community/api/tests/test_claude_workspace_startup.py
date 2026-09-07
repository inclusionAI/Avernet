"""Startup command contract: never guess or silently move an existing cwd."""

import os
from pathlib import Path
import subprocess
import sys


SCRIPT = (
    next(p for p in Path(__file__).resolve().parents if (p / "docker").is_dir())
    / "docker/agent/resolve_claude_workspace.py"
)


def run(home, **values):
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "CLAUDE_CODE_DEFAULT_CWD",
            "RELAY_DEFAULT_CWD",
            "CLAUDE_CODE_INITIAL_CWD",
        }
    }
    env.update(values)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--home", str(home)],
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
