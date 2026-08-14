"""Architecture guards for default MCP/CLI engine variability seams."""
from __future__ import annotations

import ast
import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]
_SRC_ROOT = _BACKEND_ROOT / "src" / "agentclaw"
_DEFAULT_CAPABILITIES = (
    _SRC_ROOT / "community" / "core" / "default_capabilities.py"
)
_MCP_DEFAULTS = (
    _SRC_ROOT / "community" / "core" / "mcp" / "services" / "_defaults.py"
)
_ENGINE_POLICY_LITERALS = frozenset({"aicoding", "claude_code", "normalcc"})
_AICODING_IMPL_PREFIX = "agentclaw.community.core.aicoding"
_AICODING_ENGINE_IMPL_PREFIX = (
    "agentclaw.community.core.bot_management.engines.aicoding"
)


def _parse(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _string_constants(node: ast.AST) -> set[str]:
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def _imported_modules(tree: ast.Module) -> list[tuple[int, str]]:
    modules: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append((node.lineno, node.module))
    return modules


def test_default_capabilities_uses_registered_bucket_policy_not_engine_comparisons() -> None:
    tree = _parse(_DEFAULT_CAPABILITIES)
    failures: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        literals = _string_constants(node) & _ENGINE_POLICY_LITERALS
        if literals:
            failures.append(
                f"{_DEFAULT_CAPABILITIES.relative_to(_BACKEND_ROOT)}:{node.lineno} "
                f"compares engine policy literal(s): {sorted(literals)}"
            )

    if failures:
        pytest.fail(
            "default_capabilities.py must delegate engine bucket policy to the "
            "engine registry, not branch on engine-name strings:\n  "
            + "\n  ".join(failures)
        )


def test_mcp_defaults_does_not_import_engine_specific_resolvers() -> None:
    tree = _parse(_MCP_DEFAULTS)
    forbidden = []
    for lineno, module in _imported_modules(tree):
        if module.startswith(_AICODING_IMPL_PREFIX) or module.startswith(
            _AICODING_ENGINE_IMPL_PREFIX
        ):
            forbidden.append(f"{_MCP_DEFAULTS.relative_to(_BACKEND_ROOT)}:{lineno} imports {module}")

    if forbidden:
        pytest.fail(
            "MCP defaults must resolve bucket-specific implementations through "
            "the engine registry/composition root, not import concrete engine "
            "resolvers directly:\n  " + "\n  ".join(forbidden)
        )
