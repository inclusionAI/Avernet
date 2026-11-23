"""Public versioned authoring snapshots and explicit authorized SQL import."""

from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.products.legacy_profiles import (
    LEGACY_PRODUCT_PROFILES,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    init_product_profile,
    OPEN_SINGLE_COMMISSION_PROFILE,
)

INSTALLED_PRODUCT_PROFILES = (
    *LEGACY_PRODUCT_PROFILES,
    init_product_profile(),
    OPEN_SINGLE_COMMISSION_PROFILE,
)


def build_product_catalog() -> ProductCatalog:
    """Build an isolated snapshot; production Profile reads use published SQL rows."""
    return ProductCatalog(INSTALLED_PRODUCT_PROFILES)


def import_installed_product_profiles(publication, actor):
    """Explicit deployment/test import. Never called by request-path DI providers.

    Caller must establish the target trusted tenant/env and independent publish
    authorization. Imports and historical-reference verification are one transaction.
    """
    return publication.import_profiles(INSTALLED_PRODUCT_PROFILES, actor)
