"""Black-box tests for the OpenAPI dump-and-publish runner."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import venv
from pathlib import Path
from typing import Any

_GATEWAY_DIR = Path(__file__).resolve().parents[3]
_SCRIPT = _GATEWAY_DIR / "scripts" / "dump_and_publish.sh"
_BCS_DIR = _GATEWAY_DIR.parent / "bcs"
_spec = importlib.util.spec_from_file_location(
    "bcn_contract_validator", _BCS_DIR / "scripts" / "validate_openapi_contract.py"
)
assert _spec is not None and _spec.loader is not None
_contract_validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract_validator)
_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def _operation_catalog(document: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (path, method)
        for path, path_item in document["paths"].items()
        for method in path_item
        if method in _HTTP_METHODS
    }


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
    # Compare exact operation catalogs with this checkout's authoritative source:
    # additive contracts on the merge target must not stale a fixed count.
    # Path-level parameters are metadata, not operations.
    contract_root = _BCS_DIR / "api-contracts" / "v1"
    public_contract = _contract_validator.load_contract(contract_root)
    assert _operation_catalog(document) == _operation_catalog(public_contract)
    assert "get" in document["paths"]["/openapi/v1/collaboration/me"]
    assert (
        "get"
        in document["paths"][
            "/openapi/v1/collaboration/channels/conversations/by-session"
        ]
    )
    assert (
        "get"
        in document["paths"][
            "/openapi/v1/collaboration/bots/{bot_id}/eligible-candidates"
        ]
    )
    assert (
        "patch"
        in document["paths"][
            "/openapi/v1/collaboration/groups/{group_id}/participants/{actor_id}"
        ]
    )
    assert "post" in document["paths"]["/openapi/v1/collaboration/invite-codes/claim"]
    assert "get" in document["paths"]["/openapi/v1/auth/url"]
    assert "get" in document["paths"]["/openapi/v1/auth/callback/{provider}"]
    assert "get" in document["paths"]["/openapi/v1/auth/user"]
    assert "post" in document["paths"]["/openapi/v1/auth/refresh"]
    assert "post" in document["paths"]["/openapi/v1/auth/logout"]
    assert "get" in document["paths"]["/openapi/v1/collaboration/register/token"]
    assert "post" in document["paths"]["/openapi/v1/collaboration/register"]
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
    internal_contract = _contract_validator.load_contract(
        contract_root, entrypoint="internal.yaml"
    )
    assert _operation_catalog(internal) == _operation_catalog(internal_contract)
    revision_path = "/api/v1/collaboration/drafts/{draftId}/revisions/{revision}"
    assert "get" in internal["paths"][revision_path]
    assert "post" in internal["paths"][f"{revision_path}/validate"]
    provider_path = "/api/v1/collaboration/providers/by-slug/{slug}"
    assert set(internal["paths"][provider_path]) == {"get"}
    provider = internal["paths"][provider_path]["get"]
    assert provider["operationId"] == "get_provider_by_slug"
    assert provider["security"] == []
    assert provider["x-avernet-security"] == {}
    assert provider["x-avernet-raw-response"] is True
    assert provider_path not in document["paths"]
    agent_self_path = "/api/v1/collaboration/agents/me"
    assert set(internal["paths"][agent_self_path]) == {"get"}
    agent_self = internal["paths"][agent_self_path]["get"]
    assert agent_self["security"] == [{"AgentIdentityBearer": []}]
    assert agent_self["x-avernet-security"] == {}
    assert agent_self_path.replace("/api/", "/openapi/") not in document["paths"]
    bot_self_path = "/api/v1/collaboration/bots/me"
    assert set(internal["paths"][bot_self_path]) == {"get"}
    bot_self = internal["paths"][bot_self_path]["get"]
    assert bot_self["security"] == [{"AgentIdentityBearer": []}]
    assert bot_self["x-avernet-security"] == {}
    bearer = internal["components"]["securitySchemes"]["AgentIdentityBearer"]
    assert bearer["type"] == "http"
    assert bearer["scheme"] == "bearer"
    assert bot_self_path.replace("/api/", "/openapi/") not in document["paths"]
    assert (
        "post" in internal["paths"]["/api/v1/collaboration/sessions/{session_id}/files"]
    )
    assert "post" in internal["paths"]["/api/v1/collaboration/definitions/validate"]
    # Keep explicit operation assertions alongside the catalog comparison so catalog drift
    # identifies the missing route instead of only reporting a different total.
    assert (
        "post"
        in internal["paths"]["/api/v1/collaboration/state-machine-runs/{run_id}/reruns"]
    )
    assert document["tags"] == public_contract["tags"]
    assert "Collaboration / Users" in [tag["name"] for tag in document["tags"]]
    assert document["paths"]["/openapi/v1/collaboration/sessions/{session_id}/token"][
        "post"
    ]["tags"] == ["Collaboration / Sessions"]
    assert document["paths"]["/openapi/v1/collaboration/messages/ws"]["get"][
        "tags"
    ] == ["Collaboration / Sessions"]
