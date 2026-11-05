"""TC v2 always binds the new Card/Doc service, independently of v1 storage."""

from fastapi import APIRouter, Request
from search_core.adapters.http.cards import create_card_router
from search_core.adapters.http.routes import create_search_router
from search_core.api.models import VerifiedIdentity
from search_core.application.search import CatalogSearchApplication
from search_core.core.card_management import merge_fields

from app.tc.auth import require_oss_auth
from app.tc.catalog import FILTER_FIELDS, TcCatalog
from app.tc.routes import config_response, mutate


def services(app):
    registry = app.state.context.registry
    return registry.require("tc_card_catalog"), registry.require("tc_card_search")


async def authenticate(request: Request):
    require_oss_auth(request)
    return VerifiedIdentity("tc-api", None)


class CardOperations:
    def __init__(self, app):
        self.app = app

    def upsert(self, agent_id, fields, resource_type, search_config):
        catalog, runtime = services(self.app)
        current = catalog.get(agent_id)
        if resource_type not in (None, "agent"):
            raise ValueError("TC accepts Worker Cards")
        defaults = {
            "type": "bot",
            "runtime_state": "offline",
            "availability": "private",
            "trust_level": "guarded",
            "lifecycle_state": "active",
            "domains": [],
            "responsibilities": [],
            "skills": [],
            "profile_id": "default",
        }
        data = merge_fields(current.card.source_card if current else defaults, fields)
        if not data.get("name"):
            raise ValueError("TC Card name is required")
        TcCatalog.validate(data)
        with runtime.caches.changing():
            return runtime.updates.upsert(
                agent_id, fields if current else data, resource_type, search_config
            )

    def remove(self, agent_id):
        _catalog, runtime = services(self.app)
        with runtime.caches.changing():
            runtime.updates.remove(agent_id)

    def read(self, agent_id):
        return services(self.app)[1].updates.read(agent_id)

    def status(self, agent_id):
        return services(self.app)[1].updates.status(agent_id)


class NewRetrieval:
    def __init__(self, app):
        self.app = app

    async def retrieve(self, request):
        return await services(self.app)[1].retrieve(
            request.query, request.limit, filters=dict(request.filters)
        )


def include_v2(app, *, search_router=None):
    app.include_router(create_card_router(CardOperations(app), authenticate))
    search = CatalogSearchApplication(
        NewRetrieval(app),
        filter_fields=FILTER_FIELDS | {"worker_id", "profile_key"},
        request_timeout=35,
    )
    app.include_router(
        search_router
        if search_router is not None
        else create_search_router(search, authenticate, timeout_seconds=35)
    )
    router = APIRouter(prefix="/api/v2/workers")

    @router.get("/{worker_id}/config")
    async def read_config(worker_id: str, request: Request):
        require_oss_auth(request)
        catalog, _runtime = services(request.app)
        from app.tc.routes import require

        return config_response(require(catalog, worker_id))

    @router.put("/{worker_id}/config")
    async def write_config(worker_id: str, request: Request, payload: dict):
        require_oss_auth(request)
        catalog, runtime = services(request.app)
        fields = dict(payload.get("config") or {})
        if "fusion_enable" in payload:
            fields["fusion_enable"] = payload["fusion_enable"]
        row = await mutate(
            runtime,
            catalog.save_config,
            worker_id,
            fields,
            version=payload.get("version"),
            code="UPDATE_CONFIG_ERROR",
        )
        return config_response(row)

    app.include_router(router)
