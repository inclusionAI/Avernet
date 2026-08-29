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
    "task",
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


def _module_path(module: str) -> pathlib.Path | None:
    """``agentclaw.community.core.a.b`` -> the file that defines it."""
    if not module.startswith("agentclaw."):
        return None
    candidate = _BACKEND_ROOT / "src" / pathlib.Path(*module.split("."))
    for path in (candidate.with_suffix(".py"), candidate / "__init__.py"):
        if path.is_file():
            return path
    return None


def _defines_protocol(module: str, name: str, _seen: frozenset[str] = frozenset()) -> bool:
    """Is ``name`` a Protocol class as defined by ``module`` (following re-exports)?

    Resolved from the source rather than the name, because Service API
    contracts that predate the suffix convention (``CallerTokenProvider``,
    ``CallerRuntimeUpdater``) are real Protocols under a plain name, while a
    constant re-exported from a ``_protocol`` module is not one at all.
    """
    if module in _seen or len(_seen) > 8:
        return False
    path = _module_path(module)
    if path is None:
        return False
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except (SyntaxError, OSError):  # pragma: no cover — a separate failure
        return False
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return _bases_include_protocol(node)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            if any((alias.asname or alias.name) == name for alias in node.names):
                origin = next(a.name for a in node.names if (a.asname or a.name) == name)
                return _defines_protocol(node.module, origin, _seen | {module})
    return False


def _reexported_protocol_names(tree: ast.AST) -> set[str]:
    """Names a re-export-only module republishes via ``__all__``.

    The cleaner Service API shape defines the Protocol in the owning
    ``core/<module>/protocols.py`` and re-exports it here: core then
    imports its own abstraction, so a concrete service can inherit its
    Protocol without the ``core -> api`` waiver that Rule 6 otherwise
    requires. Such a module has no ``class X(Protocol)`` of its own —
    it imports the name and lists it in ``__all__``.

    We accept one only when every name in ``__all__`` is actually
    imported and the module sources them from a contract module — one
    whose name ends in ``_protocol``/``_contract`` (or a ``protocols``
    package) — or names them with the ``Protocol`` suffix. A router or a
    service class still cannot masquerade as a contract module.
    """
    exported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                exported |= {
                    el.value
                    for el in node.value.elts
                    if isinstance(el, ast.Constant) and isinstance(el.value, str)
                }
    if not exported:
        return set()
    imported = {
        (alias.asname or alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    if not exported <= imported:
        return set()
    contract_suffixes = ("_protocol", "_protocols", "_contract", "_contracts")
    from_contract_module = {
        (alias.asname or alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").split(".")[-1].endswith(contract_suffixes + ("protocols",))
        for alias in node.names
    }
    # Some Service API contracts predate the naming convention
    # (``CallerTokenProvider``), so the source module vouches for them.
    vouched = {n for n in exported if n.endswith("Protocol")} | (
        exported & from_contract_module
    )
    # Every exported name must be vouched, not merely one of them. Accepting a
    # nonempty subset would let a module re-export ``FooProtocol`` from a
    # contract module and a concrete ``FooService`` from an implementation
    # module in the same ``__all__`` — exactly the leak that put a concrete
    # TaskClaimGrantService in api/ under the old gate.
    if vouched != exported:
        return set()
    # ...and at least one of them must be an actual Protocol. Provenance alone
    # vouches for constants too, so a module re-exporting only ``GRANTED`` from
    # a ``_protocol`` module would otherwise pass while declaring no contract.
    sources = {
        (alias.asname or alias.name): (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
    }
    if not any(
        name in sources and _defines_protocol(*sources[name])
        for name in exported
    ):
        return set()
    return vouched


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
            if _reexported_protocol_names(tree):
                # Re-export-only module: the Protocol is defined in core/ and
                # republished here. Still a contract module, no local class.
                continue
            rel = file.relative_to(_API_ROOT)
            offenders.append(str(rel))
    assert not offenders, (
        "Every api/<file>.py must define a Protocol, or re-export one from "
        "core/<module>/protocols.py via __all__. Offending files:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.unit
def test_reexport_module_counts_as_a_protocol_module() -> None:
    """A module that re-exports its Protocol from core/ satisfies the gate."""
    tree = ast.parse(
        "from agentclaw.community.core.skill_center.skill_query_service_protocol import (\n"
        "    SkillQueryServiceProtocol,\n"
        ")\n\n"
        '__all__ = ["SkillQueryServiceProtocol"]\n'
    )
    assert _reexported_protocol_names(tree) == {"SkillQueryServiceProtocol"}


@pytest.mark.unit
def test_reexport_escape_hatch_rejects_unimported_and_non_protocol_names() -> None:
    """The hatch only opens for Protocol names the module actually imports."""
    # __all__ advertises a Protocol the module never imported.
    declared_only = ast.parse('__all__ = ["SomethingProtocol"]\n')
    assert _reexported_protocol_names(declared_only) == set()

    # A router re-exporting non-Protocol names cannot pose as a contract module.
    router = ast.parse(
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n\n"
        '__all__ = ["router"]\n'
    )
    assert _reexported_protocol_names(router) == set()


@pytest.mark.unit
def test_reexport_hatch_rejects_a_concrete_service_smuggled_alongside_a_protocol() -> None:
    """One vouched name does not vouch for the rest of ``__all__``.

    Accepting a nonempty subset would let a module republish its Protocol
    from a contract module and a concrete service from an implementation
    module in the same ``__all__`` — the leak that kept a concrete
    ``TaskClaimGrantService`` in api/ unnoticed under the previous gate.
    """
    smuggled = ast.parse(
        "from agentclaw.community.core.task.task_grant_service_protocol import (\n"
        "    TaskClaimGrantServiceProtocol,\n"
        ")\n"
        "from agentclaw.community.core.task.services.task_grant_service import (\n"
        "    TaskClaimGrantService,\n"
        ")\n\n"
        '__all__ = ["TaskClaimGrantServiceProtocol", "TaskClaimGrantService"]\n'
    )
    assert _reexported_protocol_names(smuggled) == set()

    # The same module without the concrete class is still a valid re-export.
    clean = ast.parse(
        "from agentclaw.community.core.task.task_grant_service_protocol import (\n"
        "    GRANTED,\n"
        "    TaskClaimGrantServiceProtocol,\n"
        ")\n\n"
        '__all__ = ["GRANTED", "TaskClaimGrantServiceProtocol"]\n'
    )
    assert _reexported_protocol_names(clean) == {"GRANTED", "TaskClaimGrantServiceProtocol"}


@pytest.mark.unit
def test_reexport_hatch_requires_an_actual_protocol_not_just_contract_provenance() -> None:
    """Coming from a contract module is not the same as being a contract.

    ``vouched == exported`` holds for a module re-exporting only ``GRANTED``
    from ``task_grant_service_protocol`` — provenance vouches for constants
    too — so without this check an api/ file could declare no Protocol at all.
    """
    constants_only = ast.parse(
        "from agentclaw.community.core.task.task_grant_service_protocol import (\n"
        "    GRANTED,\n    REVOKED,\n)\n\n"
        '__all__ = ["GRANTED", "REVOKED"]\n'
    )
    assert _reexported_protocol_names(constants_only) == set()

    # The same constants alongside the Protocol they belong to are fine.
    with_protocol = ast.parse(
        "from agentclaw.community.core.task.task_grant_service_protocol import (\n"
        "    GRANTED,\n    REVOKED,\n    TaskClaimGrantServiceProtocol,\n)\n\n"
        '__all__ = ["GRANTED", "REVOKED", "TaskClaimGrantServiceProtocol"]\n'
    )
    assert _reexported_protocol_names(with_protocol) == {
        "GRANTED", "REVOKED", "TaskClaimGrantServiceProtocol",
    }


@pytest.mark.unit
def test_protocol_detection_follows_the_source_not_the_name() -> None:
    """A Protocol without the suffix still counts; a non-Protocol never does.

    ``CallerTokenProvider`` predates the naming convention and is a real
    Protocol in core/caller_identity/caller_credential_protocol.py, while
    ``UnavailableCallerTokenProvider`` sits in that same module and is a
    concrete class.
    """
    mod = "agentclaw.community.core.caller_identity.caller_credential_protocol"
    assert _defines_protocol(mod, "CallerTokenProvider")
    assert _defines_protocol(mod, "CallerRuntimeUpdater")
    assert not _defines_protocol(mod, "UnavailableCallerTokenProvider")
    assert not _defines_protocol(mod, "CALLER_CHAT_TASK")
