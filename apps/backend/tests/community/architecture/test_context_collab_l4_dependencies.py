"""L4 must not acquire L5/L6 or application composition dependencies."""

import ast
from pathlib import Path

_COLLAB = (
    Path(__file__).resolve().parents[3]
    / "src/agentclaw/community/core/task/context_collab"
)
_PREFIX = "agentclaw.community.core.task.context_collab"
_FORBIDDEN = tuple(
    f"{_PREFIX}.{name}"
    for name in (
        "l5_methods",
        "l6_products",
        "products",
        "services",
        "service_api",
        "persistence.repository",
        "persistence.method_store",
        "g1_secure_govern.authorization",
        "g4_contracts_consistency.product_contract",
    )
)


def test_l4_imports_only_lower_or_crosscutting_contracts():
    violations = []
    for path in sorted((_COLLAB / "l4_context_primitives").rglob("*.py")):
        package = f"{_PREFIX}." + ".".join(path.relative_to(_COLLAB).parts[:-1])
        for node in ast.walk(ast.parse(path.read_text())):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package.split(".")[
                        : len(package.split(".")) - node.level + 1
                    ]
                    module = ".".join(base + ([node.module] if node.module else []))
                else:
                    module = node.module or ""
                imports = [module, *(f"{module}.{alias.name}" for alias in node.names)]
            for imported in imports:
                if any(
                    imported == prefix or imported.startswith(prefix + ".")
                    for prefix in _FORBIDDEN
                ):
                    violations.append(
                        f"{path.relative_to(_COLLAB)}:{node.lineno}: {imported}"
                    )
    assert not violations, "L4 upward dependencies:\n" + "\n".join(violations)
