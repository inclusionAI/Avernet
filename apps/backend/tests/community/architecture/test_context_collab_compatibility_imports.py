"""Legacy collaboration entrypoints may reexport, but cannot own implementations."""

import ast
from pathlib import Path

import pytest

_COLLAB = (
    Path(__file__).resolve().parents[3]
    / "src/agentclaw/community/core/task/context_collab"
)


def _implementation_statements(source: str) -> list[ast.stmt]:
    body = ast.parse(source).body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return [node for node in body if not isinstance(node, (ast.Import, ast.ImportFrom))]


def test_legacy_entrypoints_only_reexport_layer_owners():
    paths = sorted((_COLLAB / "services").rglob("*.py"))
    assert paths, "compatibility services directory must exist"
    paths.append(_COLLAB / "service_api.py")
    paths.extend(
        _COLLAB / "persistence" / name
        for name in (
            "repository.py",
            "models.py",
            "task_storage.py",
        )
    )
    violations = []
    for path in paths:
        for node in _implementation_statements(path.read_text()):
            violations.append(
                f"{path.relative_to(_COLLAB)}:{node.lineno}: {type(node).__name__}"
            )
    assert not violations, (
        "Implementations must live in their ACCS layers:\n" + "\n".join(violations)
    )


@pytest.mark.parametrize(
    "source",
    [
        "class ProductInstantiationService: pass",
        "def project_created_context(): pass",
        "service = ProductInstantiationService()",
        "register_service()",
        "if True:\n    from owner import service",
    ],
)
def test_probe_detects_logic_in_compatibility_entrypoints(source):
    assert _implementation_statements(source)


def test_probe_allows_documented_identity_reexports():
    assert not _implementation_statements(
        '"""Compatibility only."""\nfrom owner import Service as Service\n'
    )
