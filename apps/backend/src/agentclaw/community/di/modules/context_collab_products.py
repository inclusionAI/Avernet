"""Composition-root installation of code-owned, versioned product definitions."""

from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.products.legacy_profiles import (
    LEGACY_PRODUCT_PROFILES,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
)

INSTALLED_PRODUCT_PROFILES = (*LEGACY_PRODUCT_PROFILES, TASK_COMMISSION_PROFILE)


def build_product_catalog() -> ProductCatalog:
    """Build an isolated immutable catalog; TaskModule binds one per injector."""
    return ProductCatalog(INSTALLED_PRODUCT_PROFILES)
