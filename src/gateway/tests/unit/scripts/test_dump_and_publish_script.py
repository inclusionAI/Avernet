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
    assert sum(len(path_item) for path_item in document["paths"].values()) == 57
    assert (
        "post"
        in document["paths"]["/openapi/v1/collaboration/sessions/{session_id}/token"]
    )
    assert "get" in document["paths"]["/openapi/v1/collaboration/messages/ws"]
    assert (
        "get" in document["paths"]["/openapi/v1/collaboration/bots/{bot_id}/candidates"]
    )
    assert (
        "post"
        in document["paths"]["/openapi/v1/collaboration/friend-connections/requests"]
    )
    assert "delete" in document["paths"]["/openapi/v1/collaboration/friend-connections"]
    collection = document["paths"][
        "/openapi/v1/collaboration/sessions/{session_id}/collect"
    ]
    assert set(collection) == {"post", "delete"}
    assert collection["post"]["x-avernet-security"] == {
        "user": "required",
        "app": "required",
    }
    assert collection["delete"]["x-avernet-security"] == {
        "user": "required",
        "app": "required",
    }
    assert (tmp_path / "bcn-internal.openapi.json").exists()
    internal = json.loads(
        (tmp_path / "bcn-internal.openapi.json").read_text(encoding="utf-8")
    )
    assert sum(len(path_item) for path_item in internal["paths"].values()) == 22
    assert (
        "post" in internal["paths"]["/api/v1/collaboration/sessions/{session_id}/files"]
    )
    assert "post" in internal["paths"]["/api/v1/collaboration/definitions/validate"]
    # 21 → 22 with the rerun endpoint bcs added in #1645. This suite is
    # path-filtered on `src/gateway`, so it does not run on a bcs-only commit —
    # which is how a count this test pins goes stale on `dev` and only surfaces
    # on the next gateway PR. (It was bumped independently on both sides for
    # exactly that reason.) Naming the new operation, and not just the number,
    # is what makes the next drift readable.
    assert (
        "post"
        in internal["paths"]["/api/v1/collaboration/state-machine-runs/{run_id}/reruns"]
    )
    assert [tag["name"] for tag in document["tags"]] == [
        "Collaboration / Bots",
        "Collaboration / Friendships",
        "Collaboration / Groups",
        "Collaboration / Sessions",
        "Collaboration / Invitations",
        "Collaboration / Channels",
        "Collaboration / Event Subscriptions",
    ]
    assert document["paths"]["/openapi/v1/collaboration/sessions/{session_id}/token"][
        "post"
    ]["tags"] == ["Collaboration / Sessions"]
    assert document["paths"]["/openapi/v1/collaboration/messages/ws"]["get"][
        "tags"
    ] == ["Collaboration / Sessions"]
