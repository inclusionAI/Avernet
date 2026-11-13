"""Test-owned catalog assembled from the same explicit product installation as DI."""

from agentclaw.community.di.modules.context_collab_products import (
    INSTALLED_PRODUCT_PROFILES,
    build_product_catalog,
)

PRODUCT_CATALOG = build_product_catalog()
PRODUCT_PROFILES = INSTALLED_PRODUCT_PROFILES
