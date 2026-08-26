"""The committed served-OpenAPI artifact matches what the gateway would serve.

``configs/schemas/served/gateway.openapi.json`` is the document a third-party
client reads — the only place the credential a caller must present is written
down. A stale copy is worse than none: it would state an auth contract the
running gateway no longer honours, and nothing else would catch it, because the
document is otherwise composed at request time.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_GATEWAY_DIR = Path(__file__).resolve().parents[3]
_SCRIPT = _GATEWAY_DIR / "scripts" / "dump_served_openapi.py"
_spec = importlib.util.spec_from_file_location("dump_served_openapi", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_ARTIFACT = _GATEWAY_DIR / "configs" / "schemas" / "served" / "gateway.openapi.json"
_METHODS = {"get", "post", "put", "patch", "delete"}


@pytest.fixture(scope="module")
def committed() -> dict:
    return json.loads(_ARTIFACT.read_text())


def test_artifact_is_current(committed: dict) -> None:
    assert committed == _mod.build_served_document(), (
        "The served OpenAPI artifact is stale. Regenerate it with "
        "`uv run python scripts/dump_served_openapi.py`."
    )


def test_every_operation_names_a_credential(committed: dict) -> None:
    # The whole point of the artifact: no operation may reach a client without
    # saying what to authenticate with.
    missing = [
        operation.get("operationId", f"{method} {path}")
        for path, item in committed["paths"].items()
        for method, operation in item.items()
        if method in _METHODS
        and isinstance(operation, dict)
        and "security" not in operation
    ]
    assert not missing, f"operations with no security block: {missing[:5]}"


def test_security_schemes_are_declared(committed: dict) -> None:
    schemes = committed["components"]["securitySchemes"]
    assert schemes, "no securitySchemes published"
    referenced = {
        name
        for item in committed["paths"].values()
        for method, operation in item.items()
        if method in _METHODS and isinstance(operation, dict)
        for alternative in operation.get("security", [])
        for name in alternative
    }
    assert referenced <= set(schemes), (
        f"security references undeclared schemes: {referenced - set(schemes)}"
    )
