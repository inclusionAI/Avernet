import ast
import pathlib

FORBIDDEN = [
    "agentclaw.services",
    "agentclaw.infrastructure",
]

FILES = [
    "src/agentclaw/community/api/devices/dependencies.py",
    "src/agentclaw/corp/core/devices/services/arca_device_service.py",
]


def test_devices_no_forbidden_top_level_imports():
    """Only lazy imports allowed in devices layer for forbidden modules."""
    violations = []
    for filepath in FILES:
        try:
            src = pathlib.Path(filepath).read_text()
        except FileNotFoundError:
            continue
        tree = ast.parse(src)
        # Check top-level imports only (not inside functions/classes)
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for bad in FORBIDDEN:
                    if node.module.startswith(bad):
                        # Allow if it has # noqa comment on same line
                        line_number = node.lineno
                        lines = src.split("\n")
                        if line_number <= len(lines):
                            line = lines[line_number - 1]
                            if "# noqa" in line:
                                continue
                        violations.append(f"{filepath}:{node.lineno}: {node.module}")
    assert not violations, "Top-level forbidden imports:\n" + "\n".join(violations)
