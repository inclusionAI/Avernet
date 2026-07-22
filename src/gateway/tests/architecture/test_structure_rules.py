"""Architecture enforcement: structural rules.

Derived from the Microkernel Architecture Constitution:

- **Rule 22** — Context boundaries are explicit.
- **Rule 25** — Protocols have self-validating contracts.
"""

import ast
import warnings
from pathlib import Path

GATEWAY = Path(__file__).resolve().parents[2] / "src" / "gateway" / "community"

# Protocols exempt from conformance test coverage check
_EXEMPT_PROTOCOLS: set[str] = set()


# ═══════════════════════════════════════════════════════════════════════════
# Rule 22: Module context boundaries are explicit
# ═══════════════════════════════════════════════════════════════════════════


def test_top_level_modules_have_context_docstrings() -> None:
    """Rule 22: Each top-level module must have a docstring declaring
    its architectural role.
    """
    top_level_dirs = [
        d
        for d in GATEWAY.iterdir()
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith(".")
    ]

    missing: list[str] = []
    for mod_dir in sorted(top_level_dirs):
        init_file = mod_dir / "__init__.py"
        if not init_file.exists():
            continue
        text = init_file.read_text()
        module = ast.parse(text)
        first_node = module.body[0] if module.body else None
        if not isinstance(first_node, ast.Expr) or not isinstance(
            first_node.value, ast.Constant
        ):
            missing.append(mod_dir.name)

    if missing:
        warnings.warn(
            f"\n{len(missing)} top-level module(s) without context docstring:\n"
            + "\n".join(
                f"  gateway/community/{m}/__init__.py" for m in sorted(missing)
            ),
            stacklevel=1,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Rule 25: Protocols have self-validating contracts
# ═══════════════════════════════════════════════════════════════════════════

_TEST_ROOT = Path(__file__).resolve().parents[1] / "contracts"


def _find_protocol_test_file(protocol_file: Path) -> bool:
    """Check if a test references a Protocol class from the given file."""
    if not protocol_file.exists():
        return False
    tree = ast.parse(protocol_file.read_text())
    protocol_classes = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases
        )
    ]

    if not protocol_classes:
        return True  # no Protocols to check

    for test_file in _TEST_ROOT.rglob("*.py"):
        if "__pycache__" in str(test_file):
            continue
        test_text = test_file.read_text()
        for proto_class in protocol_classes:
            if proto_class in test_text:
                return True

    return False


def test_spi_protocols_have_conformance_tests() -> None:
    """Rule 25: Every spi Protocol should have a corresponding test."""
    proto_root = GATEWAY / "spi"
    uncovered: list[str] = []

    for proto_file in sorted(proto_root.rglob("_protocols.py")):
        rel = str(proto_file.relative_to(GATEWAY))

        # Check if exempt
        pkg = rel.rsplit("/", 1)[0]  # e.g. "spi/auth"
        if pkg in _EXEMPT_PROTOCOLS:
            continue

        if not _find_protocol_test_file(proto_file):
            uncovered.append(rel)

    if uncovered:
        warnings.warn(
            f"\n{len(uncovered)} protocol file(s) without matching test:\n"
            + "\n".join(f"  {f}" for f in sorted(uncovered))
            + "\n\nAdd conformance tests for these protocols.",
            stacklevel=1,
        )


def test_architecture_test_files_not_oversized() -> None:
    """Architecture test files should stay reasonably sized."""
    test_dir = Path(__file__).parent
    oversized: list[str] = []

    for test_file in sorted(test_dir.glob("test_*.py")):
        try:
            lines = len(test_file.read_text().splitlines())
        except OSError:
            continue
        if lines > 400:
            oversized.append(f"  {test_file.name}: {lines} lines")

    if oversized:
        warnings.warn(
            "\nArchitecture test file(s) exceed 400 lines:\n" + "\n".join(oversized),
            stacklevel=1,
        )
