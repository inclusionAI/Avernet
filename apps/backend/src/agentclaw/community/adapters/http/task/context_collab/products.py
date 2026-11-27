"""Context-collab creation endpoints sharing the existing ProductProfile adapter.

The handlers delegate to ProductProfileServiceProtocol through DI. Sharing them
retains strict schemas, actor binding, redaction and error mapping on both paths.
"""

from fastapi import APIRouter, Depends

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.task.product_body_limits import (
    ProductBodyLimitRoute,
)
from agentclaw.community.adapters.http.task.product_profiles import (
    authenticated_actor,
    create_collet,
    create_profile,
)

router = APIRouter(
    prefix="/context_collab",
    tags=["product-profiles"],
    route_class=ProductBodyLimitRoute,
    dependencies=[Depends(authenticated_actor)],
)
router.add_api_route(
    "/product_profiles",
    create_profile,
    methods=["POST"],
    response_model=Envelope[dict],
    summary="Create a product profile with the default static Swarm composition",
)
router.add_api_route(
    "/collets",
    create_collet,
    methods=["POST"],
    response_model=Envelope[dict],
    summary="Create a product collet from a persisted profile",
)
