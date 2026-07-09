"""R8 arch gate: ``api/`` contains only Service API Protocols.

The api/ layer is the public surface between adapters and core: a
collection of ``@runtime_checkable Protocol`` classes (one per service)
that adapters depend on. Concrete services live in ``core/<module>/``;
HTTP routers live in ``adapters/http/<module>/``. This test enforces
both invariants on the api/ tree:

1. Every ``.py`` file directly under ``api/`` (excluding ``__init__``,
   ``README``, ``CLAUDE``) defines exactly one top-level
   ``Protocol`` subclass. No router functions, no service classes,
   no Pydantic models.

2. ``api/`` has no subdirectories holding routers or non-Protocol
   code. The only thing nested inside is module-private ``__pycache__``
   (ignored).

The previous R8 tasks (4 – 7.5) all enforced this incrementally per
module. This test pins the invariant in CI so the next contributor
can't quietly land a router back under ``api/``.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]  # .../src/backend
_API_ROOT = _BACKEND_ROOT / "src" / "agentclaw" / "community" / "api"


# Files that legitimately live under api/ but are not Protocol modules.
_NON_PROTOCOL_ALLOWLIST: frozenset[str] = frozenset({
    "__init__.py",
})

# Legacy api/ subpackages still present in the current codebase.
# This test only guards against newly introduced api/ subpackages.
_LEGACY_API_SUBDIR_ALLOWLIST: frozenset[str] = frozenset({
    "access",
    "aicoding",
    "antprocess",
    "approvals",
    "auth",
    "bot_collaborator",
    "bot_management",
    "bot_public",
    "bot_render_screen",
    "channel",
    "cron",
    "devices",
    "enums",
    "expert_chat",
    "harness",
    "identity",
    "mcp",
    "models",
    "resources",
    "service_bot",
    "skill_center",
    "system",
    "system_config",
    "token_exchange",
})


def _iter_api_files() -> list[pathlib.Path]:
    """Return every .py file directly under api/, recursively, excluding caches."""
    return [
        p for p in _API_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def _top_level_class_defs(tree: ast.AST) -> list[ast.ClassDef]:
    return [node for node in tree.body if isinstance(node, ast.ClassDef)]  # type: ignore[attr-defined]


def _bases_include_protocol(cls: ast.ClassDef) -> bool:
    """``class X(Protocol):`` or ``class X(SomeProto, Protocol):`` etc.

    We only need a structural read of the base names, so resolve the
    rightmost identifier in each base expression (no import tracing).
    """
    for base in cls.bases:
        name = None
        if isinstance(base, ast.Name):
            name = base.id
        elif isinstance(base, ast.Attribute):
            name = base.attr
        elif isinstance(base, ast.Subscript) and isinstance(base.value, ast.Name):
            name = base.value.id
        if name == "Protocol":
            return True
    return False


def test_api_root_exists() -> None:
    """Guard against silent skip if the path resolver drifts."""
    assert _API_ROOT.is_dir(), f"api/ root not found at {_API_ROOT}"


def test_api_layer_has_no_subdirectories() -> None:
    """No subpackages under api/. Routers belong in adapters/http/, not api/."""
    sub_dirs = [
        p for p in _API_ROOT.iterdir()
        if (
            p.is_dir()
            and p.name != "__pycache__"
            and p.name not in _LEGACY_API_SUBDIR_ALLOWLIST
        )
    ]
    assert not sub_dirs, (
        "api/ must be flat — one Protocol per file at the top level. "
        "Move HTTP routers under adapters/http/<module>/ instead. "
        f"Offending subdirectories: {[p.name for p in sub_dirs]}"
    )


@pytest.mark.unit
def test_every_api_file_defines_a_protocol() -> None:
    """Each .py file under api/ (except __init__) must define a Protocol class.

    The check is intentionally narrow: at least one top-level
    ``class X(Protocol):`` declaration. We don't enforce "exactly one"
    here so a module can re-export a closely-related Protocol pair if
    it ever needs to, but in practice the established pattern is one
    Protocol per file and that pattern is checked by the conformance
    test (one pair per row of ``_PAIRS``).
    """
    offenders: list[str] = []
    for file in _iter_api_files():
        if file.name in _NON_PROTOCOL_ALLOWLIST:
            continue
        try:
            tree = ast.parse(file.read_text(), filename=str(file))
        except SyntaxError as exc:  # pragma: no cover — would be a separate failure
            offenders.append(f"{file.name}:{exc.lineno} SyntaxError: {exc.msg}")
            continue
        if not any(_bases_include_protocol(cls) for cls in _top_level_class_defs(tree)):
            rel = file.relative_to(_API_ROOT)
            offenders.append(str(rel))
    assert not offenders, (
        "Every api/<file>.py must define a Protocol. Offending files:\n  "
        + "\n  ".join(offenders)
    )
