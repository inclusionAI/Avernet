"""Architecture enforcement: env-access regression tracker.

Core logic must NOT access raw environment variables directly.
Environment-specific configuration must go through config/ or bootstrap/.
"""

import ast
from pathlib import Path

GATEWAY = Path(__file__).resolve().parents[2] / "src" / "gateway" / "community"

# Approved paths for raw env access
_APPROVED_PREFIXES = (
    "config/",
    "bootstrap/",
    "main.py",
    "__main__.py",
)

_KNOWN_VIOLATIONS: set[str] = {
    # Pre-existing env access in plugin implementations (tech debt).
    # These plugins were written before the env-access arch rule was enforced.
    "  plugin_accessor.py:25: os.getenv(...)",
    "  plugins/database/bare/_plugin.py:154: os.environ access",
    "  plugins/runner/bare/_plugin.py:15: os.environ access",
    "  plugins/runner/bare/_plugin.py:18: os.environ access",
    "  plugins/tracer/bare/_plugin.py:22: os.getenv(...)",
    "  plugins/tracer/bare/_plugin.py:28: os.getenv(...)",
    "  plugins/tracer/bare/_plugin.py:50: os.getenv(...)",
}


def _is_approved(rel_path: str) -> bool:
    """Check if the file path is in an approved location for env access."""
    for prefix in _APPROVED_PREFIXES:
        if rel_path == prefix or rel_path.startswith(prefix):
            return True
    return False


def test_no_unapproved_env_access() -> None:
    """Rule 14: Env access only in config, bootstrap, and main entry points."""
    violations: list[str] = []

    for py_file in sorted(GATEWAY.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        rel_path = str(py_file.relative_to(GATEWAY))

        if _is_approved(rel_path):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "os"
                        and node.func.attr == "getenv"
                    ):
                        violations.append(f"  {rel_path}:{node.lineno}: os.getenv(...)")
                elif isinstance(node.func, ast.Name) and node.func.id == "os.getenv":
                    violations.append(f"  {rel_path}:{node.lineno}: os.getenv(...)")
            elif isinstance(node, ast.Attribute):
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "os"
                    and node.attr == "environ"
                ):
                    violations.append(f"  {rel_path}:{node.lineno}: os.environ access")

    new_violations = [v for v in violations if v not in _KNOWN_VIOLATIONS]

    if new_violations:
        raise AssertionError(
            f"\n{len(new_violations)} env access violation(s) outside "
            f"approved modules (config, bootstrap, main):\n"
            + "\n".join(sorted(new_violations))
            + "\n\nUse config or bootstrap modules for environment access."
        )


# def test_env_access_debt_tracker() -> None:
#     """Audit report — tracks env access for known violations."""
#     import warnings
#
#     if _KNOWN_VIOLATIONS:
#         warnings.warn(
#             f"\n{len(_KNOWN_VIOLATIONS)} known env access violation(s) "
#             f"tracked as tech debt."
#         )
#     else:
#         # Pass silently — no known debt
#         return
