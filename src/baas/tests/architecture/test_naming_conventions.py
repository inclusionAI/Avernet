"""Architecture enforcement: PEP 8 naming conventions.

Derived from the Microkernel Architecture Constitution:

- **Rule 19** — Naming conventions.  Function and method names MUST use
  ``snake_case`` (PEP 8), and module/package names MUST be all-lowercase.
  This test flags violations as warnings only — never fails.

Checks performed:
  1. camelCase function/method names — scan all ``secbaas/`` source files
     for functions/methods whose names contain uppercase letters that are
     not exempt (dunder methods, well-known test conventions, all-uppercase
     constants).
  2. Inconsistent module case — scan the ``secbaas/`` package tree for
     directories or ``.py`` files that contain uppercase letters.
"""

import ast
import warnings
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas" / "community"

# ── Exemptions for function/method names ──────────────────────────────
# Dunder methods: __init__, __str__, etc. — always allowed
# Test-level conventions: setUp, tearDown — unittest expected camelCase
# All-uppercase: constants/settings like MAX_RETRIES — allowed
_KNOWN_EXEMPT = frozenset({"setUp", "tearDown"})


# ═══════════════════════════════════════════════════════════════════════
# Rule 19-a: camelCase function / method names
# ═══════════════════════════════════════════════════════════════════════


def _is_camel_case(name: str) -> bool:
    """Return True if *name* looks like camelCase (not snake_case).

    Rules:
      - Must start with a lowercase letter.
      - Must contain at least one uppercase letter later in the name.
      - Dunder methods (``__foo__``) are exempt.
      - Names starting with ``_`` are exempt (private/protected).
      - Well-known test exceptions (``setUp``, ``tearDown``) are exempt.
      - All-uppercase names (constants/settings) are exempt.
    """
    if not name:
        return False
    # Dunder methods: __init__, __str__, etc.
    if name.startswith("__") and name.endswith("__"):
        return False
    # Private/protected: _internal, __private_mangled
    if name.startswith("_"):
        return False
    # All-uppercase: constants like MAX_RETRIES
    if name.isupper():
        return False
    # Well-known test exemption (setUp, tearDown)
    if name in _KNOWN_EXEMPT:
        return False
    # Must start lowercase, then somewhere have an uppercase letter
    if not name[0].islower():
        return False
    return any(c.isupper() for c in name)


def test_no_camelcase_function_names():
    """Rule 19 (Guideline): Function/method names must be snake_case.

    Scans every ``secbaas/`` ``.py`` file for ``def`` / ``async def``
    nodes whose name contains uppercase letters.  Dunder methods,
    well-known test conventions (``setUp``, ``tearDown``), and
    all-uppercase names are exempt.

    Emits ``warnings.warn()`` only — never FAILs.
    """
    violations: list[tuple[str, str, str]] = []

    for py_file in sorted(SECBAAS.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _is_camel_case(node.name):
                rel = py_file.relative_to(SECBAAS).as_posix()
                violations.append((rel, node.name, str(node.lineno)))

    if not violations:
        return

    lines = [
        f"  {rel}:{lineno} def {name}()" for rel, name, lineno in sorted(violations)
    ]
    warnings.warn(
        f"\n{len(violations)} camelCase function/method name(s) "
        f"(Rule 19 Guideline — rename to snake_case per PEP 8):\n" + "\n".join(lines)
    )


# ═══════════════════════════════════════════════════════════════════════
# Rule 19-b: Inconsistent module directory / file case
# ═══════════════════════════════════════════════════════════════════════

# Filenames that are always exempt from case checks
_ALWAYS_EXEMPT_FILENAMES = frozenset({"__init__.py", "__main__.py"})


def _path_has_uppercase(p: Path) -> bool:
    """Check whether a path component contains any uppercase letters."""
    return any(c.isupper() for c in p.name)


def test_no_inconsistent_module_case():
    """Rule 19 (Guideline): Module dirs and .py files must be all-lowercase.

    Walks the ``secbaas/`` package tree and flags any directory or
    ``.py`` filename that contains an uppercase letter.  Standard
    special modules (``__init__.py``, ``__main__.py``) are exempt.

    Emits ``warnings.warn()`` only — never FAILs.
    """
    violations: list[str] = []

    # Collect all dirs and .py files under secbaas that have uppercase
    for entry in sorted(SECBAAS.rglob("*")):
        if "__pycache__" in str(entry):
            continue

        if entry.is_dir():
            # Skip the root secbaas/ dir itself — only check subdirectories
            if entry == SECBAAS:
                continue
            if _path_has_uppercase(entry):
                rel = entry.relative_to(SECBAAS).as_posix()
                violations.append(f"  {rel}/ (directory)")
            continue

        if entry.is_file() and entry.suffix == ".py":
            if entry.name in _ALWAYS_EXEMPT_FILENAMES:
                continue
            if _path_has_uppercase(entry):
                rel = entry.relative_to(SECBAAS).as_posix()
                violations.append(f"  {rel}")

    if not violations:
        return

    warnings.warn(
        f"\n{len(violations)} module path(s) with uppercase letter(s) "
        f"(Rule 19 Guideline — rename to all-lowercase per PEP 8):\n"
        + "\n".join(sorted(violations))
    )
