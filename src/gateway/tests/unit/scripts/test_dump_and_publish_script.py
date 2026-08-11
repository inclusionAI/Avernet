"""Black-box tests for the OpenAPI dump-and-publish runner."""

from __future__ import annotations

import json
import os
import subprocess
import venv
from pathlib import Path

_GATEWAY_DIR = Path(__file__).resolve().parents[3]
_SCRIPT = _GATEWAY_DIR / "scripts" / "dump_and_publish.sh"


def test_bcn_dump_uses_the_gateway_managed_python_environment(tmp_path: Path) -> None:
    clean_venv = tmp_path / "clean-venv"
    venv.EnvBuilder(with_pip=False).create(clean_venv)

    env = os.environ.copy()
    env["PATH"] = f"{clean_venv / 'bin'}{os.pathsep}{env['PATH']}"
    env["PYTHONNOUSERSITE"] = "1"
    env["TMPDIR"] = str(tmp_path)

    result = subprocess.run(
        [
            "bash",
            str(_SCRIPT),
            "--skip",
            "backend",
            "--skip",
            "baas",
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    document = json.loads((tmp_path / "bcn.openapi.json").read_text(encoding="utf-8"))
    assert sum(len(path_item) for path_item in document["paths"].values()) == 32
    assert (
        "post"
        in document["paths"]["/openapi/v1/collaboration/sessions/{session_id}/token"]
    )
    assert "get" in document["paths"]["/openapi/v1/collaboration/messages/ws"]
    assert [tag["name"] for tag in document["tags"]] == [
        "Collaboration / Bots",
        "Collaboration / Friendships",
        "Collaboration / Groups",
        "Collaboration / Sessions",
        "Collaboration / Invitations",
    ]
    assert document["paths"]["/openapi/v1/collaboration/sessions/{session_id}/token"][
        "post"
    ]["tags"] == ["Collaboration / Sessions"]
    assert document["paths"]["/openapi/v1/collaboration/messages/ws"]["get"][
        "tags"
    ] == ["Collaboration / Sessions"]
