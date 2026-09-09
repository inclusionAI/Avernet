"""Architecture enforcement: no full-path private-module imports.

Per CLAUDE.md convention rule 1:
  Never import a private module (``_module``) via a full dotted path.
  ❌ ``from community.api.bot_runtime._protocols import BotRunner``
  ✅ ``from community.api.bot_runtime import BotRunner``  (public re-export)
  ✅ ``from ._protocols import BotRunner``              (relative, same package)
  ✅ ``from ._models import BotSessionRecord``          (relative, same package)

This test scans the AST — it catches module-level AND function-body imports.
"""

import ast
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas" / "community"

# Byte-port provenance exemption (phase 89): _aliyun_oss.py is a byte-identical
# port of the enterprise backend (diff==0 contract with the secbaas.enterprise
# tree). Its full-path import of bootstrap._configs is inherited from the
# enterprise source; changing the spelling would break the byte-identity gate.
# Exemption is deliberate and documented, like the ruff I001 per-file-ignore
# for the same file in pyproject.toml.
_BYTE_PORT_EXEMPT_FILES = {
    "plugins/file_transfer/_aliyun_oss.py",
}


def test_no_full_path_private_imports():
    """No absolute import may reference a private module (``_foo``)."""
    violations: list[str] = []

    for py_file in sorted(SECBAAS.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        if py_file.relative_to(SECBAAS).as_posix() in _BYTE_PORT_EXEMPT_FILES:
            continue
        if py_file.name == "__init__.py" and py_file.parent == SECBAAS:
            continue  # top-level secbaas/__init__.py

        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module is None:
                continue
            # Relative imports (. from ._foo) are always allowed
            if getattr(node, "level", None) and node.level > 0:
                continue

            module = node.module

            # Exclude stdlib
            if module == "__future__":
                continue

            segments = module.split(".")
            if not any(s.startswith("_") for s in segments):
                continue

            violations.append(
                f"  {py_file.relative_to(SECBAAS.parent.parent)}: "
                f"from {module} import ..."
            )

    if violations:
        raise AssertionError(
            f"\n{len(violations)} full-path private-module import(s):\n"
            + "\n".join(sorted(violations))
            + "\n\nUse relative imports (from ._foo import ...) for same-package "
            "private modules, or the public __init__.py for cross-package access."
        )
