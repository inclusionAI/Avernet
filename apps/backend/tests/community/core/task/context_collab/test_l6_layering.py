"""L6 owns composition without reverse-importing products, DI or legacy shims."""

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest
from agentclaw.community.core.task.context_collab.l6_products import catalog

_L6 = Path(catalog.__file__).parent
_CC = "agentclaw.community.core.task.context_collab"


def _forbidden_imports(source: str, package: str) -> list[str]:
    modules = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, package)
            modules.append(module)
            # Also catch `from context_collab import products`.
            modules.extend(f"{module}.{alias.name}" for alias in node.names)
    forbidden = (
        f"{_CC}.products",
        f"{_CC}.services",
        f"{_CC}.service_api",
        f"{_CC}.method_service_api",
        "agentclaw.community.di",
    )
    return [
        module
        for module in modules
        if any(
            module == prefix or module.startswith(prefix + ".") for prefix in forbidden
        )
    ]


def test_l6_has_no_product_composition_root_or_compatibility_dependencies():
    paths = sorted(_L6.rglob("*.py"))
    assert paths
    for path in paths:
        package = f"{_CC}.l6_products"
        parents = path.relative_to(_L6).parts[:-1]
        if parents:
            package += "." + ".".join(parents)
        assert not _forbidden_imports(path.read_text(), package), path


@pytest.mark.parametrize(
    "source",
    [
        f"from {_CC}.products.task_commission.profile import TASK_COMMISSION_PROFILE",
        f"from {_CC} import products",
        "from ...products.task_commission.profile import TASK_COMMISSION_PROFILE",
        "from agentclaw.community.di.modules.task.context_collab_products import build_product_catalog",
        f"from {_CC}.services.method_bootstrap import project_created_context",
        f"import {_CC}.services.method_lifecycle_service",
        f"from {_CC}.service_api import CollaborationCreateCommand",
        f"from {_CC}.method_service_api import OpenMethodOpsServiceProtocol",
        f"from {_CC} import services",
        f"from {_CC} import service_api",
        "from ... import services",
        "from ...service_api import CollaborationCreateCommand",
        "from ...services.method_bootstrap import project_created_context",
    ],
)
def test_probe_detects_reverse_dependencies(source):
    assert _forbidden_imports(source, f"{_CC}.l6_products.operations")


def test_l6_catalog_has_no_implicit_runtime_installation():
    assert not any(
        isinstance(value, catalog.ProductCatalog) for value in vars(catalog).values()
    )
    assert not hasattr(catalog, "DEFAULT_PRODUCT_CATALOG")
    assert not hasattr(catalog, "BUILTIN_PRODUCT_PROFILES")


@pytest.mark.parametrize(
    "source",
    [
        "from ..operations.method_bootstrap import project_created_context",
        "from ..service_api import CollaborationCreateCommand",
        f"from {_CC}.l5_methods.operations.contracts import OpenMethodOpsServiceProtocol",
    ],
)
def test_probe_allows_canonical_layer_imports(source):
    assert not _forbidden_imports(source, f"{_CC}.l6_products.operations")
