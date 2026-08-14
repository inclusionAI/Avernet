"""Architecture enforcement: no full-path private-module imports.

Per convention:
  ❌ ``from gateway.community.spi.auth._protocols import AuthPlugin``
  ✅ ``from gateway.community.spi.auth import AuthPlugin``  (public re-export)
  ✅ ``from ._protocols import AuthPlugin``              (relative, same package)

This test scans the AST — it catches module-level AND function-body imports.
"""

import ast
from pathlib import Path

GATEWAY = Path(__file__).resolve().parents[2] / "src" / "gateway" / "community"


def test_no_full_path_private_imports() -> None:
    """No absolute import may reference a private module (``_foo``)."""
    violations: list[str] = []

    for py_file in sorted(GATEWAY.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        if py_file.name == "__init__.py" and py_file.parent == GATEWAY:
            continue  # top-level community/__init__.py

        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module is None:
                continue
            # Relative imports (from ._foo) are always allowed
            if getattr(node, "level", None) and node.level > 0:
                continue

            module = node.module

            # Exclude stdlib
            if module == "__future__":
                continue

            segments = module.split(".")
            if not any(s.startswith("_") for s in segments):
                continue

            # Allow __init__.py in a package to import from its own private
            # submodules (e.g. spi/cache/__init__.py importing from
            # gateway.community.spi.cache._protocols).
            init_parent = py_file.parent
            init_pkg = str(init_parent.relative_to(GATEWAY)).replace("/", ".")
            mod_pkg = ".".join(segments[:-1])
            if module.startswith("gateway.community."):
                mod_pkg = module[len("gateway.community.") :]
                mod_pkg = ".".join(mod_pkg.split(".")[:-1])
            if init_pkg == mod_pkg:
                continue

            violations.append(
                f"  {py_file.relative_to(GATEWAY.parent.parent)}: "
                f"from {module} import ..."
            )

    if violations:
        raise AssertionError(
            f"\n{len(violations)} full-path private-module import(s):\n"
            + "\n".join(sorted(violations))
            + "\n\nUse relative imports (from ._foo import ...) for same-package "
            "private modules, or the public __init__.py for cross-package access."
        )
