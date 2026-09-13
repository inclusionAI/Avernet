from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SUPPORTED_PYTHON = (
    shutil.which("python3.12") or shutil.which("python3") or sys.executable
)


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("CLAWEVOLVE_INVOCATION_CWD", None)
    env["PYTHON_BIN"] = SUPPORTED_PYTHON
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_python_entrypoint_matches_shell_entrypoint(tmp_path: Path) -> None:
    python_result = _run(
        [SUPPORTED_PYTHON, str(SKILL_DIR / "scripts" / "run.py"), "--help"],
        tmp_path,
    )
    shell_result = _run(
        ["bash", str(SKILL_DIR / "scripts" / "run.sh"), "--help"],
        tmp_path,
    )

    assert python_result.returncode == shell_result.returncode == 0
    assert python_result.stdout == shell_result.stdout
    assert python_result.stderr == shell_result.stderr
    assert "--intent" in python_result.stdout
