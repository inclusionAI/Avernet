"""L6 contracts cannot install or reverse-import concrete products and DI."""

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
    forbidden = (f"{_CC}.products", "agentclaw.community.di")
    return [
        module
        for module in modules
        if any(
            module == prefix or module.startswith(prefix + ".") for prefix in forbidden
        )
    ]


def test_l6_has_no_product_or_composition_root_dependencies():
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
        "from ..products.task_commission.profile import TASK_COMMISSION_PROFILE",
        "from agentclaw.community.di.modules.context_collab_products import build_product_catalog",
    ],
)
def test_probe_detects_reverse_dependencies(source):
    assert _forbidden_imports(source, f"{_CC}.l6_products")


def test_l6_catalog_has_no_implicit_runtime_installation():
    assert not any(
        isinstance(value, catalog.ProductCatalog) for value in vars(catalog).values()
    )
    assert not hasattr(catalog, "DEFAULT_PRODUCT_CATALOG")
    assert not hasattr(catalog, "BUILTIN_PRODUCT_PROFILES")
