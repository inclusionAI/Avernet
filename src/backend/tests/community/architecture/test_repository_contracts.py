"""Repository contracts are enforceable, not decorative.

``core/repository/`` replaced a convention that lived only in prose and in a DI
``binder.bind()`` call. This guard keeps the replacement honest:

1. **Every Protocol member is ``@abstractmethod``.** Without it, an implementation
   that inherits a Protocol and omits a member silently inherits the ``...`` body
   and returns ``None`` — worse than the ``AttributeError`` the old shape gave,
   because it fails silently. The abstract marker is what converts the omission
   into a ``TypeError`` at construction naming the missing member.
2. **Every implementation declares its Protocol(s).** This is the link that makes
   "go to implementation" resolve in an IDE, and the reason the move happened.
3. **``protocols/`` carries no runtime domain import.** Domain services must
   import Protocols at runtime (``injector`` resolves constructor annotations via
   ``get_type_hints()``), so the reverse direction has to stay type-only or the
   import graph closes a cycle. Ten domain ``__init__.py`` files eagerly import
   their services, so this is not hypothetical.
4. **``protocols/`` holds contracts only, ``implementations/`` bodies only** —
   ``arch.rules.md`` §8's "one path does not serve incompatible roles".

The drift this replaced was real and recurring: ``SkillRepository`` declared
three members its implementation never had, and the third was added *after* the
first two were documented. Nothing in CI noticed either time.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest
import yaml

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                  # .../src/backend
_REPO_ROOT = (
    _BACKEND_ROOT / "src" / "agentclaw" / "community" / "core" / "repository"
)
_PROTOCOLS = _REPO_ROOT / "protocols"
_IMPLEMENTATIONS = _REPO_ROOT / "implementations"


def _py_files(root: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in sorted(root.rglob("*.py")) if p.name != "__init__.py"]


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def _classes(path: pathlib.Path) -> list[ast.ClassDef]:
    return [n for n in ast.parse(path.read_text(encoding="utf-8")).body
            if isinstance(n, ast.ClassDef)]


def _is_protocol(node: ast.ClassDef) -> bool:
    return any(isinstance(b, ast.Name) and b.id == "Protocol" for b in node.bases)


def test_repository_package_exists() -> None:
    """Guard against a silent pass if the tree is restructured out from under it."""
    assert _PROTOCOLS.is_dir(), f"missing {_PROTOCOLS}"
    assert _IMPLEMENTATIONS.is_dir(), f"missing {_IMPLEMENTATIONS}"
    assert _py_files(_PROTOCOLS), "no Protocol modules found"
    assert _py_files(_IMPLEMENTATIONS), "no implementation modules found"


def test_every_protocol_member_is_abstract() -> None:
    """A non-abstract member is inherited as a no-op returning None."""
    offenders: list[str] = []
    for path in _py_files(_PROTOCOLS):
        for cls in _classes(path):
            for body in cls.body:
                if not isinstance(body, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if body.name.startswith("__"):
                    continue
                if not any(isinstance(d, ast.Name) and d.id == "abstractmethod"
                           for d in body.decorator_list):
                    offenders.append(f"{_rel(path)}:{body.lineno} {cls.name}.{body.name}")
    assert not offenders, (
        "Repository Protocol members missing @abstractmethod. Without it an "
        "implementation that omits the member inherits the '...' body and "
        "returns None silently:\n  " + "\n  ".join(offenders)
    )


def test_every_implementation_declares_its_protocol() -> None:
    """The base class is what makes 'go to implementation' resolve."""
    offenders: list[str] = []
    for path in _py_files(_IMPLEMENTATIONS):
        for cls in _classes(path):
            if cls.name.startswith("_") or cls.name.endswith("Mixin"):
                continue
            if not cls.name.endswith(("Repository", "Repositories")):
                continue
            bases = [b.id for b in cls.bases if isinstance(b, ast.Name)]
            if not any(b.endswith(("Protocol", "Repository")) for b in bases):
                offenders.append(f"{_rel(path)}:{cls.lineno} {cls.name} bases={bases}")
    assert not offenders, (
        "Repository implementations that declare no Protocol base:\n  "
        + "\n  ".join(offenders)
    )


def test_protocols_have_no_runtime_domain_imports() -> None:
    """Every agentclaw import in protocols/ must sit under `if TYPE_CHECKING:`."""
    offenders: list[str] = []
    for path in _py_files(_PROTOCOLS):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        type_only: set[int] = set()
        for node in tree.body:
            if isinstance(node, ast.If):
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        type_only.add(id(sub))
        for node in ast.walk(tree):
            if id(node) in type_only:
                continue
            if isinstance(node, ast.ImportFrom) and node.module and \
                    node.module.startswith("agentclaw.community.core.") and \
                    "repository.protocols" not in node.module:
                offenders.append(f"{_rel(path)}:{node.lineno} imports {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("agentclaw.community.core."):
                        offenders.append(f"{_rel(path)}:{node.lineno} imports {alias.name}")
    assert not offenders, (
        "protocols/ must carry no runtime domain import — domain services import "
        "these Protocols at runtime for DI, so a runtime import back into a "
        "domain closes a cycle. Move it under `if TYPE_CHECKING:`:\n  "
        + "\n  ".join(offenders)
    )


def test_protocols_hold_no_implementations() -> None:
    """§8: one path does not serve two roles."""
    offenders = [
        f"{_rel(p)}:{c.lineno} {c.name}"
        for p in _py_files(_PROTOCOLS) for c in _classes(p)
        if not _is_protocol(c) and not c.name.endswith(("Error", "Exception"))
        and not any(isinstance(b, ast.Name) and b.id in {"ABC", "StrEnum", "Enum"}
                    for b in c.bases)
    ]
    assert not offenders, (
        "protocols/ holds contracts only; these look like concrete classes:\n  "
        + "\n  ".join(offenders)
    )


def _declared_provides() -> set[str]:
    """The ``provides`` list from the package README's Context Boundary block."""
    text = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    heading = re.search(r"^##\s+Context\s+Boundary\s*$", text, flags=re.MULTILINE)
    assert heading, "README.md has no '## Context Boundary' section"
    fence = re.search(
        r"^```yaml\s*\n(.*?)\n```", text[heading.end():],
        flags=re.MULTILINE | re.DOTALL,
    )
    assert fence, "Context Boundary section has no fenced yaml block"
    return set(yaml.safe_load(fence.group(1))["provides"])


def _is_contract(node: ast.ClassDef) -> bool:
    """A contract is a Protocol or an ABC — both are consumed as bases.

    Not every contract in ``protocols/`` is a ``Protocol``:
    ``PublishOperationRepository`` is an ``ABC``, and it is public surface for
    exactly the same reason. Selecting on ``Protocol`` alone silently dropped it
    from the required set.
    """
    return _is_protocol(node) or any(
        isinstance(b, ast.Name) and b.id == "ABC" for b in node.bases
    )


def _public_surface() -> set[str]:
    """Every contract and every non-mixin repository class in the package."""
    names: set[str] = set()
    for path in _py_files(_PROTOCOLS):
        names.update(c.name for c in _classes(path) if _is_contract(c))
    for path in _py_files(_IMPLEMENTATIONS):
        names.update(
            c.name for c in _classes(path)
            if not c.name.startswith("_")
            and not c.name.endswith("Mixin")
            and c.name.endswith(("Repository", "Repositories"))
        )
    return names


def test_readme_provides_lists_the_real_public_surface() -> None:
    """Rule 22's ``provides`` is a name index, and here it is a checked one.

    ``test_module_boundaries.py`` only asserts that each entry is a string, so a
    ``provides`` list of prose descriptions passes it while naming nothing —
    which is what this README shipped first. The names are the point:
    ``docs/arch/context-boundary-format.md`` calls the field "Public surface —
    names only". With ~90 of them, a hand-maintained list rots by the next
    repository added, so it is derived-checked instead of trusted.
    """
    declared, actual = _declared_provides(), _public_surface()
    missing = sorted(actual - declared)
    stale = sorted(declared - actual)
    assert not missing and not stale, (
        "core/repository/README.md 'provides' is out of step with the package.\n"
        + (f"  missing (add): {', '.join(missing)}\n" if missing else "")
        + (f"  stale (remove): {', '.join(stale)}\n" if stale else "")
    )


def test_incomplete_implementation_fails_at_construction() -> None:
    """The teeth. This is the behaviour the whole change buys."""
    from agentclaw.community.core.repository.protocols.platform import (
        TaskQueueRepositoryProtocol,
    )

    class Incomplete(TaskQueueRepositoryProtocol):
        """Deliberately implements nothing."""

    with pytest.raises(TypeError) as excinfo:
        Incomplete()
    message = str(excinfo.value)
    assert "abstract method" in message, message
    # The message must name what is missing — that is the diagnostic value.
    assert "enqueue" in message or "claim_batch" in message, message
