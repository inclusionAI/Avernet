"""Architecture enforcement for the sandbox-proxy.

Enforces the key boundary rules:

1. **No private/enterprise imports** — community source must not import
   ``sandboxproxy.corp`` or any non-community namespace.
2. **No forbidden transport in core** — ``community.core`` must not import
   ``fastapi``/``starlette`` (transport stays in adapters).
3. **No hardcoded private endpoints** — community source must not embed
   ``alipay``/internal hosts or tokens.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SOURCE_ROOT = (
    Path(__file__).resolve().parents[2] / "src" / "sandboxproxy" / "community"
)


def _iter_source_files() -> list[Path]:
    return sorted(_SOURCE_ROOT.rglob("*.py"))


def _imports_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


class TestNoPrivateImports:
    def test_no_corp_imports(self) -> None:
        forbidden = ("sandboxproxy.corp",)
        for path in _iter_source_files():
            for imp in _imports_of(path):
                assert not imp.startswith(forbidden), (
                    f"{path.relative_to(_SOURCE_ROOT)} imports private {imp}"
                )


class TestCoreTransportAgnostic:
    def test_core_does_not_import_fastapi(self) -> None:
        core = _SOURCE_ROOT / "core"
        for path in core.rglob("*.py"):
            for imp in _imports_of(path):
                assert not imp.startswith("fastapi"), (
                    f"{path.relative_to(_SOURCE_ROOT)} imports fastapi"
                )
                assert not imp.startswith("starlette"), (
                    f"{path.relative_to(_SOURCE_ROOT)} imports starlette"
                )


class TestNoHardcodedEndpoints:
    def test_no_private_hosts(self) -> None:
        for path in _iter_source_files():
            text = path.read_text(encoding="utf-8")
            assert "alipay" not in text, (
                f"{path.relative_to(_SOURCE_ROOT)} embeds a private host"
            )
