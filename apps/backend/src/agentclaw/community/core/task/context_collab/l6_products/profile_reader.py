"""L6 outbound exact-version configuration port; no latest or fallback lookup."""

from typing import Protocol, runtime_checkable
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductCapabilities,
    LegacyProductConfiguration,
)


@runtime_checkable
class ProductProfileReader(Protocol):
    """Every call uses the current trusted tenant/env, never a cached request scope.

    Missing rows and DB/codec failures propagate. The static ProductCatalog is also
    an implementation for pure authoring and explicitly isolated tests.
    """

    def get(self, product: str, version: int) -> LegacyProductConfiguration: ...
    def capabilities(self) -> ProductCapabilities: ...
