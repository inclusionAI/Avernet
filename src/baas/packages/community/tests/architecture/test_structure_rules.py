"""Architecture enforcement: structural rules.

Derived from the Microkernel Architecture Constitution:

- **Rule 22** — Context boundaries are explicit.  Each top-level
  module (api/, spi/, core/, infra/, adapters/, plugins/, domain/)
  must have a docstring declaring its architectural context.

- **Rule 25** — Protocols have self-validating contracts.  Every
  Protocol defined in secbaas.api and secbaas.spi should have a
  corresponding test file.
"""

import ast
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas"

# ═══════════════════════════════════════════════════════════════════════════
# Rule 22: Module context boundaries are explicit
# ═══════════════════════════════════════════════════════════════════════════


def test_top_level_modules_have_context_docstrings():
    """Rule 22: Each top-level module must have a docstring declaring
    its architectural role.
    """
    top_level_dirs = [
        d
        for d in SECBAAS.iterdir()
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
        import warnings

        warnings.warn(
            f"\n{len(missing)} top-level module(s) without context docstring "
            f"(pre-existing, see RULES-MANIFEST.md):\n"
            + "\n".join(f"  secbaas/{m}/__init__.py" for m in sorted(missing))
        )


# ═══════════════════════════════════════════════════════════════════════════
# Rule 25: Protocols have self-validating contracts (conformance tests)
#
# For every Protocol class defined in secbaas.api.*._protocols and
# secbaas.spi.*._protocols, check that there is a corresponding test
# file that exercises it.
#
# NOTE: This is a soft check — some protocols may be tested implicitly
# via integration tests.  The mapping is documented in RULES-MANIFEST.md.
# ═══════════════════════════════════════════════════════════════════════════

_PROTOCOL_TEST_DIRS = [
    Path(__file__).resolve().parents[2] / "tests",
]


def _find_protocol_test_file(protocol_module: str) -> bool:
    # protocol_module like src.secbaas.api.paas._protocols
    # Use parts[2:] to skip leading src.secbaas since SECBAAS already includes it
    parts = protocol_module.split(".")
    proto_file = SECBAAS / f"{'/'.join(parts[2:])}.py"
    if not proto_file.exists():
        return False
    text = proto_file.read_text()
    tree = ast.parse(text)
    protocol_classes = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases
        )
    ]

    if not protocol_classes:
        return True

    for test_root in _PROTOCOL_TEST_DIRS:
        if not test_root.exists():
            continue
        for test_file in test_root.rglob("*.py"):
            if "__pycache__" in str(test_file):
                continue
            test_text = test_file.read_text()
            for proto_class in protocol_classes:
                if proto_class in test_text:
                    return True

    return False


def test_api_protocols_have_conformance_tests():
    """Rule 25: Every api Protocol should have a corresponding test.

    Reports protocol files without obvious test coverage as warnings.
    """
    uncovered: list[str] = []

    for proto_dir in [SECBAAS / "api", SECBAAS / "spi"]:
        if not proto_dir.exists():
            continue
        for proto_file in proto_dir.rglob("_protocols.py"):
            rel = proto_file.relative_to(SECBAAS.parent.parent)
            module = ".".join(rel.with_suffix("").parts)
            if not _find_protocol_test_file(module):
                uncovered.append(str(rel))

    if uncovered:
        import warnings

        warnings.warn(
            f"\n{len(uncovered)} protocol file(s) without matching test:\n"
            + "\n".join(f"  {f}" for f in sorted(uncovered))
            + "\n\nSee RULES-MANIFEST.md for the full coverage mapping."
        )


# ═══════════════════════════════════════════════════════════════════════════
# Issue #2: Import-linter gap documentation
# ═══════════════════════════════════════════════════════════════════════════

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_import_linter_not_configured():
    """Known Issue #2: import-linter or equivalent is not configured.

    Currently only pytestarch tests run, which catch module-level imports
    but NOT lazy/function-body imports.  An ``import-linter`` config (or
    equivalent) would block illegal lazy imports automatically.

    This test will PASS SILENTLY once an import-linter config is added.
    """
    # Check common config locations
    pyproject = _PROJECT_ROOT / "pyproject.toml"
    setup_cfg = _PROJECT_ROOT / "setup.cfg"
    import_linter_cfg = _PROJECT_ROOT / ".import-linter.cfg"

    import_linter_found = False

    if pyproject.exists():
        text = pyproject.read_text()
        if "[tool.import-linter]" in text:
            import_linter_found = True

    if import_linter_cfg.exists():
        import_linter_found = True

    if setup_cfg.exists():
        text = setup_cfg.read_text()
        if "[import_linter" in text:
            import_linter_found = True

    if not import_linter_found:
        import warnings

        warnings.warn(
            "\nimport-linter is NOT configured — lazy/function-body "
            "imports are not caught by pytestarch tests. "
            "See RULES-MANIFEST.md Issue #2."
        )


def test_architecture_test_files_not_oversized():
    """Architecture test files should stay reasonably sized.

    Very large test files are harder to review and may signal
    mixed concerns.  Emit a warning for files over 400 lines.
    """
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
        import warnings

        warnings.warn(
            "\nArchitecture test file(s) exceed 400 lines "
            "(consider splitting):\n" + "\n".join(oversized)
        )
