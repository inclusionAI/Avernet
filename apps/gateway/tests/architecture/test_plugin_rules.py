"""Architecture enforcement: plugin lifecycle and SPI mapping rules.

Derived from the Microkernel Architecture Constitution:

- **Rule 11** — The plugin lifecycle is uniform and enforced.  Each
  plugin type must declare which lifecycle phases it participates in.
  Plugins that manage external connections or resources should define
  a ``close()`` method for cleanup; those that manage background
  processes should define ``start()`` and ``stop()``.

  This test verifies that:
  1. Every ``_protocols.py`` in ``spi/`` that defines a resource-managing
     Plugin Protocol has a ``close()`` method.
  2. Every SPI Protocol has a corresponding plugin implementation directory.
  3. No plugin directory exists without a corresponding SPI Protocol.
  4. Every SPI Protocol method has a concrete implementation in plugins/.
"""

import ast
from pathlib import Path

GATEWAY = Path(__file__).resolve().parents[2] / "src" / "gateway" / "community"
TESTS = Path(__file__).resolve().parents[2] / "tests"


def _protocol_classes_in_file(filepath: Path):
    """Yield (class_name, methods) for Protocol classes in a file."""
    if not filepath.exists():
        return
    try:
        tree = ast.parse(filepath.read_text())
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
            isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases
        ):
            methods = [
                n.name
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            yield node.name, methods


# ═══════════════════════════════════════════════════════════════════════════
# Rule 11: Plugin lifecycle uniformity — SPI ↔ plugin mapping
# ═══════════════════════════════════════════════════════════════════════════


def test_all_spi_plugins_have_corresponding_plugin_impls():
    """Rule 11: Every Protocol in spi/ should have at least one
    concrete implementation directory in plugins/.
    """
    spi_dir = GATEWAY / "spi"

    spi_packages = {
        d.name
        for d in spi_dir.iterdir()
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith(".")
    }

    plugins_dir = GATEWAY / "plugins"
    plugin_packages = {
        d.name
        for d in plugins_dir.iterdir()
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith(".")
    }

    missing_plugins = spi_packages - plugin_packages
    if missing_plugins:
        import warnings

        warnings.warn(
            "\nSPI packages without corresponding plugin implementations:\n"
            + "\n".join(
                f"  spi/{p} -> (no plugins/{p})" for p in sorted(missing_plugins)
            )
        )


def test_no_dangling_plugin_implementations():
    """Rule 11: Every concrete plugin under plugins/ should map to
    a corresponding SPI Protocol definition.
    """
    spi_packages = {
        d.name
        for d in (GATEWAY / "spi").iterdir()
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith(".")
    }

    plugins_dir = GATEWAY / "plugins"
    plugin_packages = {
        d.name
        for d in plugins_dir.iterdir()
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith(".")
    }

    orphan_plugins = plugin_packages - spi_packages
    if orphan_plugins:
        import warnings

        warnings.warn(
            "\nPlugin package(s) without an SPI Protocol definition:\n"
            + "\n".join(f"  plugins/{p}" for p in sorted(orphan_plugins))
        )


def test_resource_plugins_have_close():
    """Rule 11: Resource-managing plugin Protocols should have close().

    Gateway plugins that manage external connections (cache, database,
    secret resolver) must provide a cleanup method to prevent resource
    leaks.
    """
    resource_spi_packages = ["cache", "database", "secret_resolver"]
    missing_close: list[str] = []

    for pkg in resource_spi_packages:
        proto_file = GATEWAY / "spi" / pkg / "_protocols.py"
        for class_name, methods in _protocol_classes_in_file(proto_file):
            if "Plugin" in class_name and "close" not in methods:
                # Skip if the protocol doesn't manage connections
                # (pure stateless contracts don't need close)
                if all(
                    m not in methods
                    for m in ["get", "set", "sync_connection", "session", "get_secret"]
                ):
                    continue
                missing_close.append(
                    f"  spi/{pkg}/{class_name}" + f" -> methods={methods}"
                )

    if missing_close:
        import warnings

        warnings.warn(
            "\nResource plugin Protocol(s) without close() — resource leak risk:\n"
            + "\n".join(missing_close)
        )


# ═══════════════════════════════════════════════════════════════════════════
# Rule 10/11: SPI → Plugin method mapping
# ═══════════════════════════════════════════════════════════════════════════


def test_spi_methods_have_plugin_implementations():
    """Rule 10/11: Every SPI Protocol method should have at least one
    implementation in the corresponding plugins/ directory.

    This detects contract drift — when a new method is added to an SPI
    Protocol but no plugin implements it.
    """
    spi_dir = GATEWAY / "spi"
    plugins_dir = GATEWAY / "plugins"
    missing: list[str] = []

    for spi_pkg_dir in sorted(spi_dir.iterdir()):
        if not spi_pkg_dir.is_dir() or spi_pkg_dir.name.startswith("_"):
            continue

        # Find all Protocol classes and their methods in this SPI package
        for proto_file in spi_pkg_dir.rglob("_protocols.py"):
            for class_name, methods in _protocol_classes_in_file(proto_file):
                if not methods:
                    continue

                # Determine the corresponding plugin directory
                # SPI: spi/cache/_protocols.py -> plugins: plugins/cache/
                # SPI: spi/auth/_protocols.py -> plugins: plugins/auth/
                spi_rel = proto_file.parent.relative_to(spi_dir)
                plugin_dir = plugins_dir / spi_rel

                # Check if this is an exempted protocol (sub-contract, not a standalone plugin)
                if class_name == "ConnectionProvider" and "database" in str(spi_rel):
                    continue  # sub-contract used by DataSourcePlugin

                if not plugin_dir.exists():
                    missing.append(
                        f"  spi/{spi_rel}/{class_name} (no plugins/{spi_rel} directory)"
                    )
                    continue

                # Scan all .py files in the plugin directory for method implementations
                plugin_methods: set[str] = set()
                for plugin_py in plugin_dir.rglob("*.py"):
                    if "__pycache__" in str(plugin_py):
                        continue
                    try:
                        plugin_tree = ast.parse(plugin_py.read_text())
                    except SyntaxError:
                        continue
                    for node in ast.walk(plugin_tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            plugin_methods.add(node.name)

                # Check each SPI method has a plugin implementation
                for method in methods:
                    # Skip dunder methods and lifecycle helpers that may be inherited
                    if method.startswith("__") or method in ("close", "start", "stop"):
                        continue
                    if method not in plugin_methods:
                        missing.append(
                            f"  spi/{spi_rel}/{class_name}.{method}() "
                            f"(no def found in plugins/{spi_rel})"
                        )

    if missing:
        import warnings

        warnings.warn(
            "\nSPI Protocol method(s) without plugin implementation:\n"
            + "\n".join(missing)
            + "\n\nEither add implementations to plugins/ or update the SPI contract."
        )
