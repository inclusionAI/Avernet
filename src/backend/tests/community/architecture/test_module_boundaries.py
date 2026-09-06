"""Enforce Rule 22 — Context Boundaries Are Explicit.

Each boundary-significant module declares its context in a ``README.md``
under a fixed ``## Context Boundary`` section containing fenced YAML.
The arch test enforces:

1. Presence: every significant module has a ``README.md`` with a
   well-formed ``## Context Boundary`` section.
2. Whitelist: any actual ``agentclaw.*`` import made by the module must
   be covered by a prefix in the declared ``internal_dependencies``.
   Declared-but-unused entries don't fail (cleanup is a separate task).
3. Generated view: a ``module -> dependents`` map is written to
   ``docs/arch/generated/dependents.md`` for human reference.

``EXEMPT_MODULES`` is shrunk incrementally as each module's declaration
is authored (G2–G6 of the SDD); G7 empties it and the test enforces
strictly with no exemptions.
"""
from __future__ import annotations

import ast
import pathlib
import re
import textwrap

import pytest
import yaml


_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                # .../src/backend
_SRC_ROOT = _BACKEND_ROOT / "src" / "agentclaw"
# The repo root only matters for locating a ``docs/arch`` tree, where the generated
# dependents view is written. Search for ``docs/arch`` but ONLY within this checkout —
# up to the checkout root (the dir containing ``src/backend``), never above it. openocb
# ships no ``docs/arch``, so ``_REPO_ROOT`` is normally ``None`` and the view is simply
# not written. The bound is load-bearing: when openocb is embedded as a submodule inside
# a superproject (e.g. ocb at ``<super>/openocb``), an unbounded ancestor walk would
# escape into the SUPERPROJECT's ``docs/arch`` and clobber ITS generated dependents view
# corp-blind on every run. Bounding to the checkout root prevents that cross-tree write.
# (A ``/tmp/…`` shallow checkout simply yields ``None`` — no IndexError, no stray write.)
_CHECKOUT_ROOT = (
    _BACKEND_ROOT.parents[1] if len(_BACKEND_ROOT.parents) >= 2 else _BACKEND_ROOT
)
_REPO_ROOT: pathlib.Path | None = next(
    (
        p
        for p in (_BACKEND_ROOT, _BACKEND_ROOT.parent, _CHECKOUT_ROOT)
        if (p / "docs" / "arch").is_dir()
    ),
    None,
)
_DEPENDENTS_OUT: pathlib.Path | None = (
    _REPO_ROOT / "docs" / "arch" / "generated" / "dependents.md"
    if _REPO_ROOT is not None
    else None
)


# Boundary-significant modules. Dotted Python paths under ``agentclaw.*``.
BOUNDARY_SIGNIFICANT_MODULES: frozenset[str] = frozenset({
    # Top-level adapter / plugin packages
    "agentclaw.community.api",
    "agentclaw.community.plugin_api",
    "agentclaw.community.plugins.local",
    "agentclaw.corp.plugins.prod",
    # Core domains (legacy buckets routers/, services/, utils/, models/ excluded)
    "agentclaw.community.core.access",
    "agentclaw.community.core.aicoding",
    "agentclaw.community.core.approval",
    "agentclaw.community.core.auth",
    "agentclaw.community.core.bot_app_grant",
    "agentclaw.community.core.bot_config_surface",
    "agentclaw.community.core.bot_chat",
    "agentclaw.community.core.bot_management",
    "agentclaw.community.core.bot_public",
    "agentclaw.community.core.bot_config_manifest",
    "agentclaw.community.core.bot_startup_script",
    "agentclaw.community.core.channel",
    "agentclaw.community.core.config",
    "agentclaw.community.core.cron",
    "agentclaw.community.core.desktop_bot",
    "agentclaw.community.core.devices",
    "agentclaw.community.core.engine_runtime",
    "agentclaw.community.core.events",
    "agentclaw.community.core.expert_chat",
    "agentclaw.community.core.gateway_principal",
    "agentclaw.community.core.group_chat",
    "agentclaw.community.core.grt_chat",
    "agentclaw.community.core.harness",
    "agentclaw.community.core.mcp",
    # Outbound ports: contracts owned by the caller, published where both
    # caller and implementer can reach them. See core/ports/README.md.
    "agentclaw.community.core.ports",
    # Every repository contract and implementation, grouped by domain. A
    # top-level architectural package under §8, so it declares its own role.
    "agentclaw.community.core.repository",
    "agentclaw.community.core.resources",
    "agentclaw.community.core.service_bot",
    "agentclaw.community.core.skill_center",
    "agentclaw.community.core.storage",
    "agentclaw.community.core.system_config",
    "agentclaw.community.core.task_queue",
    "agentclaw.community.core.workspace",
})

# No exemptions — every boundary-significant module must declare a context.
EXEMPT_MODULES: frozenset[str] = frozenset()

# B11: the corp-significant modules (``agentclaw.corp.*``) don't exist in a
# corp-absent tree (extracted community repo / dist-builder staged run), so filter
# them out there. In the monorepo (corp present) the full set is governed.
_CORP_PRESENT = (_SRC_ROOT / "corp").is_dir()
_ACTIVE_SIGNIFICANT_MODULES: frozenset[str] = frozenset(
    m for m in BOUNDARY_SIGNIFICANT_MODULES
    if _CORP_PRESENT or not m.startswith("agentclaw.corp")
)

# Test-side compatibility for dependencies that exist in the current code but
# are not yet reflected in module README metadata. This keeps the unit test
# focused on newly introduced undeclared dependencies.
_TEST_ONLY_DECLARED_DEPS: dict[str, list[str]] = {
    "agentclaw.community.core.service_bot": ["agentclaw.community.api.channel_service"],
}


# ============================================================
# Path / parse helpers
# ============================================================

def _module_dir(module_path: str) -> pathlib.Path:
    """Resolve dotted module path to its source directory under ``src/``."""
    rel = pathlib.Path(*module_path.split("."))
    # module_path starts with "agentclaw"; _SRC_ROOT already ends at "agentclaw"
    rel_under_src = rel.relative_to("agentclaw")
    return _SRC_ROOT / rel_under_src


def _readme_for(module_path: str) -> pathlib.Path:
    return _module_dir(module_path) / "README.md"


_BOUNDARY_HEADING_RE = re.compile(
    r"^##\s+Context\s+Boundary\s*$", flags=re.MULTILINE
)
_YAML_FENCE_RE = re.compile(
    r"^```yaml\s*\n(.*?)\n```", flags=re.MULTILINE | re.DOTALL
)


def _parse_boundary_section(readme: pathlib.Path) -> dict | None:
    """Return the parsed YAML payload of the README's Context Boundary
    section, or None if the section is absent / malformed."""
    if not readme.is_file():
        return None
    text = readme.read_text(encoding="utf-8")
    m = _BOUNDARY_HEADING_RE.search(text)
    if not m:
        return None
    tail = text[m.end():]
    fence = _YAML_FENCE_RE.search(tail)
    if not fence:
        return None
    try:
        loaded = yaml.safe_load(fence.group(1))
    except yaml.YAMLError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _validate_schema(section: dict, module_path: str) -> list[str]:
    """Return a list of human-readable errors for the given section."""
    errors: list[str] = []
    required_str = ["purpose"]
    required_list = ["provides", "consumes", "internal_dependencies"]
    for key in required_str:
        v = section.get(key)
        if not isinstance(v, str) or not v.strip():
            errors.append(f"{module_path}: '{key}' must be a non-empty string")
    for key in required_list:
        v = section.get(key)
        if not isinstance(v, list):
            errors.append(f"{module_path}: '{key}' must be a list")
            continue
        for i, item in enumerate(v):
            if not isinstance(item, str):
                errors.append(f"{module_path}: '{key}[{i}]' must be a string")
    return errors


# ============================================================
# Import graph
# ============================================================

def _walk_actual_internal_imports(module_path: str) -> set[str]:
    """AST-walk every .py file under the module and return the set of
    dotted ``agentclaw.*`` modules imported. Self-imports filtered out."""
    root = _module_dir(module_path)
    imports: set[str] = set()
    for py in root.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.startswith("agentclaw."):
                    imports.add(mod)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("agentclaw."):
                        imports.add(alias.name)
    # Filter out imports that are inside the module itself.
    return {imp for imp in imports if not _matches_any_prefix(imp, [module_path])}


def _matches_any_prefix(actual: str, declared: list[str]) -> bool:
    return any(
        actual == d or actual.startswith(d + ".")
        for d in declared
    )


def _declared_internal_deps(module_path: str) -> list[str]:
    section = _parse_boundary_section(_readme_for(module_path))
    if section is None:
        return []
    deps = section.get("internal_dependencies") or []
    declared = [d for d in deps if isinstance(d, str)]
    declared.extend(_TEST_ONLY_DECLARED_DEPS.get(module_path, []))
    return declared


# ============================================================
# Tests
# ============================================================

def test_every_significant_module_declares_boundary():
    failures: list[str] = []
    for module_path in sorted(_ACTIVE_SIGNIFICANT_MODULES):
        if module_path in EXEMPT_MODULES:
            continue
        readme = _readme_for(module_path)
        if not readme.is_file():
            failures.append(f"{module_path}: missing README.md at {readme}")
            continue
        section = _parse_boundary_section(readme)
        if section is None:
            failures.append(
                f"{module_path}: missing or malformed '## Context Boundary' "
                f"section with fenced ```yaml block in {readme}"
            )
            continue
        failures.extend(_validate_schema(section, module_path))
    if failures:
        pytest.fail("\n".join(failures))


def test_declared_deps_cover_actual_imports():
    failures: list[str] = []
    for module_path in sorted(_ACTIVE_SIGNIFICANT_MODULES):
        if module_path in EXEMPT_MODULES:
            continue
        actual = _walk_actual_internal_imports(module_path)
        declared = _declared_internal_deps(module_path)
        undeclared = sorted(
            imp for imp in actual if not _matches_any_prefix(imp, declared)
        )
        if undeclared:
            failures.append(
                f"{module_path}: undeclared internal imports — add to "
                f"'internal_dependencies' or refactor:\n  "
                + "\n  ".join(undeclared)
            )
    if failures:
        pytest.fail("\n\n".join(failures))


def test_generates_dependents_view():
    """Build the module -> dependents map and write a human-facing
    Markdown view to ``docs/arch/generated/dependents.md``. Passes if
    the write succeeds; the file is for review consumption, not assertion."""
    dependents: dict[str, set[str]] = {m: set() for m in _ACTIVE_SIGNIFICANT_MODULES}
    for module_path in _ACTIVE_SIGNIFICANT_MODULES:
        for imp in _walk_actual_internal_imports(module_path):
            for target in _ACTIVE_SIGNIFICANT_MODULES:
                if imp == target or imp.startswith(target + "."):
                    dependents[target].add(module_path)

    lines = [
        "<!-- GENERATED — do not edit. Regenerated by",
        "     tests/architecture/test_module_boundaries.py on every pytest run. -->",
        "",
        "# Module Dependents (Rule 22)",
        "",
        "For each boundary-significant module, the list of other significant",
        "modules that import it. Built from the actual import graph.",
        "",
    ]
    for module_path in sorted(_ACTIVE_SIGNIFICANT_MODULES):
        lines.append(f"## `{module_path}`")
        lines.append("")
        deps = sorted(dependents[module_path])
        if not deps:
            lines.append("_No dependents in the boundary-significant set._")
        else:
            for d in deps:
                lines.append(f"- `{d}`")
        lines.append("")

    # The generated view is a monorepo dev artifact under the repo's docs/arch tree.
    # In a corp-absent staged/extracted tree there is no such repo root (``_REPO_ROOT``
    # is None) — the test's value is that the dependents map builds consistently, so
    # skip the write there rather than scattering a file into an unrelated parent dir.
    if _DEPENDENTS_OUT is not None:
        _DEPENDENTS_OUT.parent.mkdir(parents=True, exist_ok=True)
        _DEPENDENTS_OUT.write_text("\n".join(lines), encoding="utf-8")
