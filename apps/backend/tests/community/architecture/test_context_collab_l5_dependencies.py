"""L5 Method definitions and operations must not depend on L6 or products."""

import ast
from pathlib import Path

import pytest

_COLLAB = (
    Path(__file__).resolve().parents[3]
    / "src/agentclaw/community/core/task/context_collab"
)
_PREFIX = "agentclaw.community.core.task.context_collab"
_FORBIDDEN = tuple(
    f"{_PREFIX}.{name}"
    for name in (
        "l6_products",
        "products",
        "services",
        "service_api",
        "method_service_api",
        "persistence.repository",
        "g4_contracts_consistency.product_contract",
    )
)


def _upward_imports(source: str, package: str) -> list[str]:
    violations = []
    for node in ast.walk(ast.parse(source)):
        imports = []
        if isinstance(node, ast.Import):
            imports = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - node.level + 1]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            imports = [module, *(f"{module}.{alias.name}" for alias in node.names)]
        for imported in imports:
            if any(
                imported == prefix or imported.startswith(prefix + ".")
                for prefix in _FORBIDDEN
            ):
                violations.append(f"{node.lineno}: {imported}")
    return violations


def test_l5_imports_only_lower_or_crosscutting_contracts():
    violations = []
    for path in sorted((_COLLAB / "l5_methods").rglob("*.py")):
        package = f"{_PREFIX}." + ".".join(path.relative_to(_COLLAB).parts[:-1])
        violations.extend(
            f"{path.relative_to(_COLLAB)}:{violation}"
            for violation in _upward_imports(path.read_text(), package)
        )
    assert not violations, "L5 upward dependencies:\n" + "\n".join(violations)


@pytest.mark.parametrize(
    "source",
    [
        f"from {_PREFIX}.l6_products.contracts import ScenarioRole",
        f"import {_PREFIX}.products.task_commission.publication",
        "from ..l6_products.contracts import ScenarioRole",
        "from .. import products",
        "if TYPE_CHECKING:\n    from ..l6_products.contracts import ScenarioRole",
    ],
)
def test_import_probe_detects_upward_and_type_checking_dependencies(source):
    assert _upward_imports(source, f"{_PREFIX}.l5_methods")


@pytest.mark.parametrize(
    "source",
    [
        f"from {_PREFIX}.l5_methods.open_profile import ParticipationType",
        "from ..l4_context_primitives.participants import Participant",
        "from .publication import OpenPublicationProfile",
        f"from {_PREFIX}.l6_products_unrelated import Value",
    ],
)
def test_import_probe_allows_layer_owned_and_lower_contracts(source):
    assert not _upward_imports(source, f"{_PREFIX}.l5_methods")
