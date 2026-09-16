"""Execute the container dispatcher in a sandbox to verify managed identity."""

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def dispatcher(tmp_path):
    repo = next(parent for parent in Path(__file__).parents if (parent / "docker/agent/start_service.sh").is_file())
    home = tmp_path / "home"
    script = tmp_path / "start_service.sh"
    # Redirect only filesystem roots; execute the real argument/credential logic.
    script.write_text((repo / "docker/agent/start_service.sh").read_text().replace(
        "/home/admin", str(home)
    ).replace("/var/run/agentclaw", str(tmp_path / "run")))
    (tmp_path / "util.sh").write_text(
        'set_log_file() { :; }\n'
        'section() { :; }\n'
        'warn() { echo "$*"; }\n'
        'info() { echo "$*"; }\n'
        'success() { echo "$*"; }\n'
        'fail() { echo "$*" >&2; }\n'
    )
    child = tmp_path / "start_openclaw.sh"
    child.write_text("#!/bin/bash\nexit 0\n")
    child.chmod(0o700)
    return script, home


@pytest.mark.parametrize("version", ["3", "V3"])
def test_startup_preserves_exact_service_bot_identity(dispatcher, version):
    script, home = dispatcher
    result = subprocess.run([
        "bash", str(script), "--bot_id", "service-bot", "--entity_id", "entity-a",
        "--version", version, "--stage", "online", "--token", "private-token",
    ], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    credentials = (home / ".credentials").read_text()
    assert "ENTITY_ID=entity-a\n" in credentials
    assert f"VERSION={version}\n" in credentials
    assert "BOT_ID=service-bot\n" in credentials
    assert "STAGE=online\n" in credentials
    assert "private-token" not in result.stdout + result.stderr
    assert "entity_id=entity-a" in result.stdout
    assert (home / ".credentials").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("arguments", [
    ["--entity_id", "entity\nBOT_ID=other"],
    ["--entity_id", "entity\rBOT_ID=other"],
    ["--version", "V0"], ["--version", "3\nTOKEN=other"],
    ["--version"], ["--entity_id"],
])
def test_startup_rejects_invalid_identity_before_credentials_write(dispatcher, arguments):
    script, home = dispatcher
    result = subprocess.run(["bash", str(script), *arguments], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert not (home / ".credentials").exists()


def test_startup_remains_compatible_without_service_identity(dispatcher):
    script, home = dispatcher
    result = subprocess.run(["bash", str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "ENTITY_ID=\nVERSION=\n" in (home / ".credentials").read_text()
