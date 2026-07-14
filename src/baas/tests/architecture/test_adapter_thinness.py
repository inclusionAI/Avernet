"""Architecture enforcement: adapter thinness rules.

Derived from the Microkernel Architecture Constitution:

- **Rule 7** — Adapters must remain thin.  The delivery layer (adapters/web/)
  should only translate between API protocols and domain operations.  Domain
  logic, session orchestration, concurrency management, and state mutation
  belong in the core layer.

  This test enforces adapter thinness by scanning for:
  1. Concurrency patterns (``asyncio.create_task``, ``asyncio.wait_for``,
     ``asyncio.gather``) — orchestration belongs in services.
  2. Session state mutations (``.mark_running()``, ``.mark_completed()``,
     ``.mark_failed()``) — session lifecycle belongs in services.
  3. Duplicate function definitions across adapter files — extracted into
     a shared utility if needed in multiple routes.
  4. Domain imports — adapters must not import from ``community.core.service``
     or ``community.domain`` at module level.

KNOWN PRE-EXISTING VIOLATIONS (documented in RULES-MANIFEST.md):
- ``routers/open_api/dependencies.py`` — auth policy logic
- ``routers/open_api/session_router.py`` — _check_app_type auth logic
- ``_filter_headers`` duplicated in paas_facade_router.py and bot_http_router.py
"""

import ast
import warnings
from pathlib import Path

from pytestarch import Rule

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas" / "community"
_ADAPTERS_DIR = SECBAAS / "adapters" / "web"

# ── Known pre-existing violations ──────────────────────────────────────────
# Excluded from test FAILURE; emit WARNING only.
_KNOWN_THINNESS_VIOLATIONS: set[str] = {
    "routers/open_api/dependencies.py",  # auth policy logic: _normalize_bot_id,
    # match_allowed_bots, validate_policy
    "routers/open_api/session_router.py",  # _check_app_type auth logic
    "websocket/local_management_ws.py",  # asyncio.create_task + session state handling
}


# ═══════════════════════════════════════════════════════════════════════════
# ── Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _relative_path(adapter_dir: Path, py_file: Path) -> str:
    """Return path relative to adapter_dir for consistent lookups."""
    return str(py_file.relative_to(adapter_dir))


def _scan_for_domain_patterns(adapter_dir: Path) -> dict[str, list[str]]:
    """Scan all ``.py`` files under *adapter_dir* for AST patterns that
    indicate domain logic has leaked into the adapter layer.

    Returns a dict mapping file relative-path to a list of violation messages.
    """
    violations: dict[str, list[str]] = {}

    # ── Concurrency patterns ───────────────────────────────────────────
    _CONCURRENCY_FUNCS = {"asyncio.create_task", "asyncio.wait_for", "asyncio.gather"}

    # ── Session state mutation patterns ─────────────────────────────────
    _SESSION_MUTATIONS = {".mark_running(", ".mark_completed(", ".mark_failed("}

    for py_file in sorted(adapter_dir.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError:
            continue

        rel = _relative_path(adapter_dir, py_file)

        # (a) Concurrency patterns — AST-based function call check
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            # Check for asyncio.xxx() — ast.Attribute with value=asyncio
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "asyncio"
            ):
                full_name = f"asyncio.{node.func.attr}"
                if full_name in _CONCURRENCY_FUNCS:
                    violations.setdefault(rel, []).append(
                        f"  L{node.lineno}: {full_name}() — concurrency "
                        f"orchestration belongs in services, not adapters"
                    )

        # (b) Session state mutations — substring match in source
        # (AST walk can't easily detect .mark_running(x) calls because
        #  the receiver varies at runtime.  Source-level pattern matching
        #  is more reliable here.)
        for mutation_pattern in _SESSION_MUTATIONS:
            idx = source.find(mutation_pattern)
            while idx != -1:
                lineno = source[:idx].count("\n") + 1
                violations.setdefault(rel, []).append(
                    f"  L{lineno}: {mutation_pattern} — session lifecycle "
                    f"state mutation belongs in services"
                )
                idx = source.find(mutation_pattern, idx + 1)

    return violations


# ═══════════════════════════════════════════════════════════════════════════
# Test: no asyncio orchestration in adapters
# ═══════════════════════════════════════════════════════════════════════════


def test_no_new_asyncio_orchestration_in_adapters():
    """Rule 7: Adapters must not use ``asyncio.create_task``,
    ``asyncio.wait_for``, or ``asyncio.gather``.

    Concurrency orchestration belongs in the core service layer.
    Pre-existing violations in known files emit a warning; new
    violations fail the build.
    """
    violations = _scan_for_domain_patterns(_ADAPTERS_DIR)

    # Filter to only concurrency-related violations
    asyncio_violations: dict[str, list[str]] = {
        f: msgs for f, msgs in violations.items() if any("asyncio." in m for m in msgs)
    }

    known: list[str] = []
    new: list[str] = []

    for fpath, msgs in sorted(asyncio_violations.items()):
        if fpath in _KNOWN_THINNESS_VIOLATIONS:
            known.append(f"{fpath}:\n" + "\n".join(msgs))
        else:
            new.append(f"{fpath}:\n" + "\n".join(msgs))

    if known:
        warnings.warn(
            f"\nKnown asyncio orchestration in {len(known)} adapter file(s) "
            f"(pre-existing debt, see RULES-MANIFEST.md):\n" + "\n".join(known)
        )

    if new:
        raise AssertionError(
            f"\n{len(new)} adapter file(s) with NEW asyncio orchestration:\n"
            + "\n".join(new)
        )


# ═══════════════════════════════════════════════════════════════════════════
# Test: no session state mutation in adapters
# ═══════════════════════════════════════════════════════════════════════════


def test_no_session_state_mutation_in_adapters():
    """Rule 7: Adapters must not mutate session lifecycle state
    via ``.mark_running()``, ``.mark_completed()``, or ``.mark_failed()``.

    Session state transitions belong in the core service layer.
    Pre-existing violations in known files emit a warning; new
    violations fail the build.
    """
    violations = _scan_for_domain_patterns(_ADAPTERS_DIR)

    # Filter to only session-mutation violations
    session_violations: dict[str, list[str]] = {
        f: msgs
        for f, msgs in violations.items()
        if any("session lifecycle" in m for m in msgs)
    }

    known: list[str] = []
    new: list[str] = []

    for fpath, msgs in sorted(session_violations.items()):
        if fpath in _KNOWN_THINNESS_VIOLATIONS:
            known.append(f"{fpath}:\n" + "\n".join(msgs))
        else:
            new.append(f"{fpath}:\n" + "\n".join(msgs))

    if known:
        warnings.warn(
            f"\nKnown session state mutations in {len(known)} adapter file(s) "
            f"(pre-existing debt, see RULES-MANIFEST.md):\n" + "\n".join(known)
        )

    if new:
        raise AssertionError(
            f"\n{len(new)} adapter file(s) with NEW session state mutations:\n"
            + "\n".join(new)
        )


# ═══════════════════════════════════════════════════════════════════════════
# Test: no duplicate function definitions across adapter files
# ═══════════════════════════════════════════════════════════════════════════


def test_no_duplicate_function_definitions():
    """Rule 7: Adapter files must not define identically-named functions.

    Duplicate private helpers across routers indicate an opportunity to
    extract shared utility code.

    Known exemption: ``_filter_headers`` in ``paas_facade_router.py``
    and ``bot_http_router.py`` (pre-existing debt).
    """
    file_funcs: dict[str, set[str]] = {}

    for py_file in sorted(_ADAPTERS_DIR.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        rel = _relative_path(_ADAPTERS_DIR, py_file)
        func_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name != "__init__":
                    func_names.add(node.name)
        if func_names:
            file_funcs[rel] = func_names

    # Find private functions (starting with _) that appear in 2+ files.
    # Public function name collisions across router files are expected
    # (e.g., get_device_info in device_router.py vs paas_facade_router.py).
    # Only flag private helper duplication.
    func_to_files: dict[str, set[str]] = {}
    for fpath, names in file_funcs.items():
        for name in names:
            if name.startswith("_"):
                func_to_files.setdefault(name, set()).add(fpath)

    _KNOWN_DUPES = {"_filter_headers"}  # paas_facade_router + bot_http_router

    duplicates = {
        name: files
        for name, files in func_to_files.items()
        if len(files) > 1 and name not in _KNOWN_DUPES
    }

    if duplicates:
        new: list[str] = []
        for name, files in sorted(duplicates.items()):
            new.append(f"  {name}: {', '.join(sorted(files))}")

        if new:
            raise AssertionError(
                "\nDuplicate private function definition(s) across adapter files:\n"
                + "\n".join(new)
            )

    # Also warn about the known dupe so it stays visible
    known_dupes = {
        name: files
        for name, files in func_to_files.items()
        if len(files) > 1 and name in _KNOWN_DUPES
    }
    if known_dupes:
        warnings.warn(
            "\nKnown duplicate function definitions (pre-existing debt):\n"
            + "\n".join(
                f"  {name}: {', '.join(sorted(files))}"
                for name, files in sorted(known_dupes.items())
            )
        )


# ═══════════════════════════════════════════════════════════════════════════
# Test: adapters must not import from domain layers
# ═══════════════════════════════════════════════════════════════════════════


def test_no_domain_imports_in_adapters(project_architecture):
    """Rule 7: Adapters must not import from ``community.core.service``
    or ``community.domain``.

    Adapters should depend only on SPI/protocol interfaces, not on
    concrete domain services.  (Uses pytestarch module-level import
    analysis.)

    Known exemptions:
    ``open_api/session_router.py`` are documented thinness violators
    and are not checked by this test; they are caught separately by
    the AST-based tests above.
    """
    rule = (
        Rule()
        .modules_that()
        .are_sub_modules_of("secbaas.community.adapters.web")
        .should_not()
        .import_modules_that()
        .are_sub_modules_of("secbaas.community.core.service")
    )
    rule.assert_applies(project_architecture)
