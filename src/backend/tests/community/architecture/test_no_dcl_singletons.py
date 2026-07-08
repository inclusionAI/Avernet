"""Forbid the double-checked-locking / lazy module-level singleton pattern.

This pattern — a nullable module-level variable plus a ``global`` initializer
gate inside a function — is what the DI refactor (Phases 1-4) eliminated.
Re-introducing it sneaks hidden state back into the codebase and bypasses
the injector. Allowlisted singletons below are the few cases that are
genuinely bootstrap state or legacy debt knowingly retained.

Detection heuristic (AST-only, no runtime imports):

  A module is flagged when BOTH conditions hold:

  1. It has a module-level assignment of the form ``_<name>: <T> | None = None``
     (or ``Optional[<T>] = None``, or plain ``_<name> = None``).
  2. Some function in that same module contains ``global _<name>`` followed by
     an ``if _<name> is None:`` initialization gate.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                # .../src/backend
_AGENTCLAW_ROOT = _BACKEND_ROOT / "src" / "agentclaw"


# ---------------------------------------------------------------------------
# Allowlist — relative paths under src/agentclaw/ that may keep the pattern.
# Each entry MUST have a one-line justification. Adding a new entry is a
# review-level decision: every DCL singleton is a smell.
# ---------------------------------------------------------------------------
_ALLOWLIST: dict[str, str] = {
    # Per-task allowlist requested in the spec: di/__init__.py is the public
    # surface of the DI package; if a future maintainer puts a bootstrap
    # global here, that is still legitimate.
    "di/__init__.py": "DI package public surface — legitimate location for bootstrap globals.",
    # Config is read before the injector exists (pre-DI bootstrap), so the
    # ConfigProvider registry caches the resolved AppConfig at module scope by
    # design — see specs/2026-06-24-config-provider-retire-monkeypatch.
    "core/config/provider.py": "Pre-DI ConfigProvider registry — bootstrap config cache (B2).",
    # ----- pre-existing legacy DCL singletons retained intentionally -----
    # These predate the refactor; the guard exists to stop NEW ones, not
    # force retroactive fixes. Each is wrapped by a public accessor used
    # widely enough that ripping it out is its own task.
    "corp/plugins/prod/moltis_connection_manager.py": "Legacy device connection manager — pre-existing singleton.",
    "corp/plugins/prod/arca_factory.py": "Legacy lazy factory exposed via PEP 562 __getattr__.",
    "corp/plugins/prod/layotto.py": "Legacy layotto manager singleton.",
    "corp/plugins/prod/oss_storage.py": "Legacy botOssClient exposed via PEP 562 __getattr__.",
    "plugins/local/database.py": "Legacy SQLAlchemy engine/session factory cache.",
    "core/workspace/path_factory.py": "Legacy WorkspacePathFactory singleton.",
    "core/skill_center/feature_flags.py": "Legacy SkillCenter feature-flag cache.",
    "core/events/bus.py": "Process-wide event bus with locked DCL — pre-existing.",
    "adapters/http/skill_center/__init__.py": "Legacy combined-router lazy assembly.",
    "core/nas_usage/service.py": "NAS usage service singleton for background task management.",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rel(path: pathlib.Path) -> str:
    rel = path.relative_to(_AGENTCLAW_ROOT).as_posix()
    # B11: layers migrate under ``agentclaw/community/<layer>``. Strip the
    # ``community/`` prefix so layer-relative allowlist keys ("core/...") match
    # whichever side of the move a file is on.
    return rel[len("community/"):] if rel.startswith("community/") else rel


def _is_none_constant(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _nullable_singleton_names(tree: ast.Module) -> dict[str, int]:
    """Return {underscore_name: lineno} for module-level lazy nullable globals.

    Matches:
      _foo: T | None = None
      _foo: Optional[T] = None
      _foo = None
    """
    found: dict[str, int] = {}
    for node in tree.body:
        # AnnAssign: `_foo: T | None = None`
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            if not name.startswith("_"):
                continue
            if node.value is None or not _is_none_constant(node.value):
                continue
            ann = node.annotation
            # `T | None`
            is_optional = (
                isinstance(ann, ast.BinOp)
                and isinstance(ann.op, ast.BitOr)
                and (_is_none_constant(ann.right) or _is_none_constant(ann.left))
            )
            # `Optional[T]`
            if not is_optional and isinstance(ann, ast.Subscript):
                if isinstance(ann.value, ast.Name) and ann.value.id == "Optional":
                    is_optional = True
                elif (
                    isinstance(ann.value, ast.Attribute)
                    and ann.value.attr == "Optional"
                ):
                    is_optional = True
            if is_optional:
                found[name] = node.lineno
        # Assign: `_foo = None`
        elif isinstance(node, ast.Assign) and _is_none_constant(node.value):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id.startswith("_"):
                    found[tgt.id] = node.lineno
    return found


def _dcl_gated_globals(tree: ast.Module) -> set[str]:
    """Return underscore-prefixed names used in a DCL pattern inside some function.

    Specifically: a function declares ``global _foo`` AND its body contains
    ``if _foo is None:``.
    """
    gated: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        global_names: set[str] = set()
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Global):
                for nm in sub.names:
                    if nm.startswith("_"):
                        global_names.add(nm)
        if not global_names:
            continue
        # Look for `if _name is None:` (or `if _name is not None` — only `is None` gates init)
        for sub in ast.walk(fn):
            if not isinstance(sub, ast.If):
                continue
            test = sub.test
            if (
                isinstance(test, ast.Compare)
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Is)
                and isinstance(test.left, ast.Name)
                and test.left.id in global_names
                and len(test.comparators) == 1
                and _is_none_constant(test.comparators[0])
            ):
                gated.add(test.left.id)
    return gated


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_no_dcl_singletons() -> None:
    """Fail on new lazy module-level singletons gated by ``if _x is None``."""
    violations: list[str] = []
    for path in _AGENTCLAW_ROOT.rglob("*.py"):
        if not path.is_file():
            continue
        rel = _rel(path)
        if rel in _ALLOWLIST:
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        nullable = _nullable_singleton_names(tree)
        if not nullable:
            continue
        gated = _dcl_gated_globals(tree)
        for name in sorted(nullable.keys() & gated):
            violations.append(f"{rel}:{nullable[name]}  lazy DCL singleton `{name}`")
    assert not violations, (
        "Module-level lazy DCL singletons are forbidden — register the dependency "
        "with the injector instead.\n"
        "If the case is genuinely bootstrap state, add it to `_ALLOWLIST` in this "
        "test with a one-line justification.\n"
        "Violations found:\n  " + "\n  ".join(violations)
    )


@pytest.mark.unit
def test_dcl_allowlist_entries_still_exist() -> None:
    """Stale allowlist entries must be pruned.

    B11: corp-tolerant — a ``corp/`` allowlist entry cannot exist in a corp-absent
    tree (the extracted community repo / the dist-builder staged run), so those
    entries are skipped there; in the monorepo (corp present) they are still checked.
    """
    corp_present = (_AGENTCLAW_ROOT / "corp").is_dir()
    missing = [
        rel for rel in _ALLOWLIST
        if not (_AGENTCLAW_ROOT / rel).is_file()
        and not (_AGENTCLAW_ROOT / "community" / rel).is_file()
        and not (rel.startswith("corp/") and not corp_present)
    ]
    assert not missing, (
        "Allowlisted files no longer exist. Remove stale entries from "
        "`_ALLOWLIST`:\n  " + "\n  ".join(missing)
    )
