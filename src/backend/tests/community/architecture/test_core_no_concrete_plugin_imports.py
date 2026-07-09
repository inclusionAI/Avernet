"""S5 — core/ must not import concrete plugin implementations.

Rule 14 (``docs/arch/arch.rules.md``) — Configuration-Driven Wiring —
forbids business code from depending directly on a concrete impl. The
sibling guard ``test_no_is_local_mode_outside_allowlist.py`` forbids the
*explicit* form (``if is_local_mode():``). This guard forbids the
*implicit* form: ``core/`` importing ``agentclaw.corp.plugins.prod.*`` or
``agentclaw.community.plugins.local.*`` directly. Environment-specific impls must
be injected via DI (depend on a ``plugin_api`` Protocol, never a concrete
class).

Scans ALL Import/ImportFrom nodes (module-level AND function-local) under
``src/agentclaw/core/``. ``agentclaw.community.plugin_api.*`` is allowed (that's the
Protocol — the DI-correct dependency).

Adding to ``_ALLOWLIST`` is a review-level decision; each entry MUST carry
a one-line justification. The allowlist tightens as Rule-14 refactors land:
an allowlisted file whose violation disappears (refactor landed) — or that
no longer exists — is flagged STALE so the dead entry gets removed.

Known blind spot (intentionally NOT detected, YAGNI): a two-step
``from agentclaw.community.plugins import prod`` followed by attribute-walking
(``prod.passport.ProdPassportPlugin``) is not caught — only direct imports of
the concrete module are; the sibling ``test_no_fastapi_in_core.py`` shares
this gap, and the idiomatic form is the direct import this guard flags.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                 # .../src/backend
_AGENTCLAW_ROOT = _BACKEND_ROOT / "src" / "agentclaw"
# B11: the core layer lives under BOTH community/ and corp/. Rule 14 (core must
# not import concrete plugin impls) is universal, so scan both cores.
_CORE_ROOTS = (
    _AGENTCLAW_ROOT / "community" / "core",
    _AGENTCLAW_ROOT / "corp" / "core",
)

# Forbidden import roots (prefix match on the dotted module path).
_FORBIDDEN_PREFIXES = (
    "agentclaw.corp.plugins.prod",
    "agentclaw.community.plugins.local",
)

# Allowlist — paths under src/agentclaw/ that may import a concrete plugin
# impl from core/. Each entry MUST have a one-line justification. These are
# reviewed Rule-14 exceptions pending refactor, not silent passes.
_ALLOWLIST: dict[str, str] = {}


def _forbidden_in_module_path(module: str | None) -> bool:
    if not module:
        return False
    return any(
        module == p or module.startswith(p + ".") for p in _FORBIDDEN_PREFIXES
    )


def _forbidden_imports_in_tree(tree: ast.AST) -> list[tuple[int, str]]:
    """The guard's one and only AST traversal.

    Walks every Import/ImportFrom node (module-level AND function-local, since
    ``ast.walk`` descends into function bodies) and returns ``(lineno, module)``
    tuples for each forbidden import. ``ImportFrom`` with a non-zero ``level``
    (relative import) is skipped — a relative import can never name an absolute
    ``agentclaw.community.plugins.*`` root. ``_violations_in_file`` formats these tuples
    into messages; the self-tests call this directly so they pin the REAL walk.
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and _forbidden_in_module_path(node.module):
                out.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden_in_module_path(alias.name):
                    out.append((node.lineno, alias.name))
    return out


def _violations_in_file(path: pathlib.Path) -> list[str]:
    """Return ``"<rel>:<line> imports <module>"`` strings for forbidden imports."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    rel = path.relative_to(_AGENTCLAW_ROOT).as_posix()
    return [
        f"{rel}:{lineno} imports {module}"
        for lineno, module in _forbidden_imports_in_tree(tree)
    ]


def test_core_does_not_import_concrete_plugin_impls():
    violations: list[str] = []
    # Allowlist keys whose file actually produced a violation this run — i.e.
    # the suppression is still doing work. Anything in _ALLOWLIST not in this
    # set is stale (violation gone, or the file no longer exists).
    still_needed: set[str] = set()
    for py in (p for root in _CORE_ROOTS if root.is_dir() for p in root.rglob("*.py")):
        rel = py.relative_to(_AGENTCLAW_ROOT).as_posix()
        file_violations = _violations_in_file(py)
        if rel in _ALLOWLIST:
            if file_violations:
                still_needed.add(rel)
            # Suppressed: do not surface this file's violations.
            continue
        violations.extend(file_violations)

    # Stale = allowlisted but no longer producing a violation this run (refactor
    # landed, or file gone). This is the real stale check — it runs on real code,
    # so service_dep.py allowlisted + still violating ⇒ not stale ⇒ green.
    stale = sorted(set(_ALLOWLIST) - still_needed)
    if stale:
        pytest.fail(
            "S5 stale allowlist entries (violation gone — remove from "
            "_ALLOWLIST):\n" + "\n".join(f"  {p}" for p in stale)
        )

    if violations:
        pytest.fail(
            "S5 Rule-14 violations: core/ must not import concrete plugin "
            "impls (use DI + a plugin_api Protocol). Offending sites:\n"
            + "\n".join(violations)
            + "\n\nIf a site is a reviewed exception, add it to _ALLOWLIST "
            "with a one-line justification."
        )


# ---------------------------------------------------------------------------
# Self-tests — prove the guard has TEETH (is not a no-op). The main guard above
# only validates the helpers implicitly by running on real code; these pin the
# detection logic explicitly: forbidden imports are CAUGHT, plugin_api is ALLOWED.
#
# The file-based cases parse a source string with ``ast.parse`` and call the
# REAL ``_forbidden_imports_in_tree`` — the guard's one and only traversal — so
# there is no mirror copy to drift. If someone stops scanning ``ast.Import`` (or
# any branch) in the real function, ``test_violations_catches_plain_import``
# (resp. the others) FAILS. We assert on the module names only (drop the lineno).
# ---------------------------------------------------------------------------


def _forbidden_modules_in_source(src: str) -> list[str]:
    """Run the REAL guard traversal on a source string, returning module names.

    Parses ``src`` and delegates to ``_forbidden_imports_in_tree`` (the single
    traversal the production guard uses), dropping the lineno so callers can
    assert on the matched dotted module paths. This is NOT a mirror — it calls
    the real function, so any regression in the walk surfaces here.
    """
    tree = ast.parse(src)
    return [module for _lineno, module in _forbidden_imports_in_tree(tree)]


def test_forbidden_prefix_matching():
    # exact + submodule of a forbidden root → forbidden
    assert _forbidden_in_module_path("agentclaw.corp.plugins.prod") is True
    assert _forbidden_in_module_path("agentclaw.corp.plugins.prod.passport") is True
    assert _forbidden_in_module_path("agentclaw.community.plugins.local.cache") is True
    # plugin_api (the DI-correct Protocol dependency) → allowed
    assert _forbidden_in_module_path("agentclaw.community.plugin_api.auth") is False
    # a sibling-named package that merely shares a prefix → allowed
    # (must NOT false-match on a bare ``startswith`` without the dot boundary)
    assert _forbidden_in_module_path("agentclaw.community.plugins.produtils") is False
    assert _forbidden_in_module_path(None) is False


def test_violations_catches_function_local_import():
    # A forbidden import INSIDE a function must be flagged — ast.walk descends
    # into function bodies, so the guard catches lazy/function-local imports.
    src = (
        "def loader():\n"
        "    from agentclaw.corp.plugins.prod.passport import ProdPassportPlugin\n"
        "    return ProdPassportPlugin\n"
    )
    found = _forbidden_modules_in_source(src)
    assert "agentclaw.corp.plugins.prod.passport" in found, found


def test_violations_catches_plain_import():
    # ``import agentclaw.community.plugins.local.cache`` (Import node, not ImportFrom).
    src = "import agentclaw.community.plugins.local.cache\n"
    found = _forbidden_modules_in_source(src)
    assert "agentclaw.community.plugins.local.cache" in found, found


def test_violations_allows_plugin_api():
    # plugin_api import must NOT be flagged (it's the DI-correct Protocol dep).
    src = "from agentclaw.community.plugin_api.auth import AuthPlugin\n"
    found = _forbidden_modules_in_source(src)
    assert found == [], found
