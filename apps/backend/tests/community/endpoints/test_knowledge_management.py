"""Registered management/content cases through real DI, SQLite and HTTP adapters.

Only the remote HTTP provider and deployment session interpretation are scripted.
These cases do not claim native provider or knowledge-building acceptance.
"""

import json
from contextlib import contextmanager
from typing import Annotated

import httpx
from agentclaw.community.adapters.knowledge_platform.config import (
    KnowledgeIntegrationConfig,
)
from agentclaw.community.adapters.knowledge_platform.content_ports import (
    AsyncContentTransport,
    ContentAccessPolicy,
)
from agentclaw.community.api.knowledge_content_service import (
    KnowledgeContentServiceProtocol,
)
from agentclaw.community.api.knowledge_management_service import (
    KnowledgeManagementServiceProtocol,
)
from agentclaw.community.core.knowledge_management.config import (
    KnowledgeManagementConfig,
)
from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.kernel.knowledge_content import ContentAccess
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.http_client import HttpClient
from agentclaw.community.plugin_api.knowledge_catalog import KnowledgeCatalogPlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.http_client import AsyncHttpxClient
from agentclaw.community.utils.env_utils import get_current_env
from injector import InstanceProvider

from tests.community.endpoints.test_space_knowledge import _seed_knowledge
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

ACTOR = "001234"
ROOT = "/api/knowledge"
KB = ROOT + "/spaces/{space_id}/knowledge-bases/{kb_id}"
PARAMS = {
    "space_id": 1,
    "kb_id": "kb",
    "bot_id": "bot",
    "source_id": "source",
    "run_id": "run",
    "revision_id": "revision",
    "job_id": "job",
}


def _answer(request):
    path = request.url.path
    if (
        "/integrations/tc/" not in path
        and "/bot-authorizations" not in path
        and not path.endswith("/bot-authorization-states")
    ):
        assert request.headers.get("X-Test-Session") == "fixture"
    fact = {
        "space_id": "1",
        "kb_id": "kb",
        "name": "Knowledge",
        "kgbase_status": "ready",
        "can_manage": True,
    }
    if path.endswith("/knowledge-bases/catalog-details"):
        assert request.method == "POST"
        assert json.loads(request.content) == {
            "actor_staff_no": ACTOR,
            "kb_ids": ["kb"],
        }
        payload = {"space_id": "1", "items": [fact]}
    elif path.endswith("/catalog-detail"):
        payload = fact
    elif path.endswith("/knowledge-bases"):
        payload = {"items": [fact], "next_cursor": None}
    elif path.endswith("/authorized-knowledge-bases"):
        payload = {
            "bot_id": "bot",
            "user_id": ACTOR,
            "space_id": "1",
            "kb_ids": ["kb"],
            "next_cursor": None,
        }
    elif path.endswith("/bot-authorization-states"):
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["actor_staff_no"] == ACTOR and body["platform"] == "teamclaw"
        payload = {
            "kb_id": "kb",
            "platform": "teamclaw",
            "platform_env": get_current_env(),
            "items": [
                {**target, "granted": False, "revision": 0}
                for target in body["targets"]
            ],
        }
    elif path.endswith("/authorized-bots") or path.endswith("/bot-authorizations"):
        payload = {"items": [], "next_cursor": None}
    elif "/bot-authorizations/" in path:
        payload = {"granted": True, "revision": 1}
    elif path.endswith("/sources/source/raw"):
        return httpx.Response(
            200, content=b"# Original", headers={"content-type": "text/markdown"}
        )
    elif path.endswith("/sources/source"):
        payload = {"source_id": "source", "kb_id": "kb"}
    elif path.endswith("/sync-runs/run"):
        payload = {"run_id": "run", "target": {"kb_id": "kb", "source_id": "source"}}
    elif path.endswith("/ingest-jobs"):
        payload = {
            "jobs": [
                {"job_id": "job", "kb_id": "kb", "status": "succeeded", "items": []}
            ],
            "offset": 0,
            "limit": 100,
            "total": 1,
        }
    elif path.endswith(
        (
            "/sources",
            "/sources/upload",
            "/sources/source/replace",
            "/personal-docs",
            "/import-docs",
            "/knowledge-sources/source/sync",
            "/knowledge-sources/source/auto-sync",
            "/ingest-tasks",
            "/source-revisions/revision",
            "/activities",
        )
    ):
        payload = {"items": []}
    else:
        raise AssertionError(f"Unexpected knowledge request: {request.method} {path}")
    return httpx.Response(200, json=payload)


def _test_access(actor_id, session):
    if session.get("test_actor", actor_id) != actor_id:
        raise KnowledgeError("KNOWLEDGE_IDENTITY_MISMATCH", 403)
    if session.get("test_session") != "fixture":
        raise KnowledgeError("KNOWLEDGE_CONTENT_UNAVAILABLE", 503)
    return ContentAccess(actor_id, {"X-Test-Session": session["test_session"]})


def _seed_enabled(world):
    _seed_knowledge(world)  # real space/membership/acknowledged outbox context
    make_staff_user(world, user_id=ACTOR)
    db = world.get(DatabasePlugin)
    env = get_current_env()
    with db.orm_session() as session:
        session.add(
            BotModel(
                bot_id="bot",
                bot_name="Bot",
                owner_id=ACTOR,
                creator_id=ACTOR,
                entity_id=ACTOR,
                entity_type="staff",
                space_id=1,
                env=env,
                bot_type="personal",
            )
        )
    world.get(KnowledgeCatalogPlugin).set_response("list_authorized", ("kb",))
    # Only configuration, credentials and the external wire boundary are seeded.
    # All repositories, catalog/content adapters and business services are resolved
    # through the exact providers the real HTTP handlers use.
    world.injector.binder.bind(
        KnowledgeIntegrationConfig,
        to=KnowledgeIntegrationConfig(True, "https://knowledge.test"),
    )
    world.injector.binder.bind(
        KnowledgeManagementConfig, to=KnowledgeManagementConfig(True, "teamclaw")
    )
    world.injector.binder.bind(ContentAccessPolicy, to=InstanceProvider(_test_access))
    sync = world.get(Annotated[HttpClient, "knowledge"])

    @contextmanager
    def send(method, url, **kwargs):
        kwargs.pop("timeout", None)
        response = _answer(httpx.Request(method, url, **kwargs))
        try:
            yield response
        finally:
            response.close()

    sync.set_override("stream", send)
    pool = AsyncHttpxClient(transport=httpx.MockTransport(_answer))
    world.injector.binder.bind(AsyncContentTransport, to=InstanceProvider(pool))


def _assert_real_composition(response, world):
    from agentclaw.community.adapters.knowledge_platform.catalog import (
        EcbKnowledgeClient,
    )
    from agentclaw.community.adapters.knowledge_platform.content_client import (
        EcbKnowledgeContentClient,
    )
    from agentclaw.community.core.knowledge_management.ports import KnowledgeCatalog
    from agentclaw.community.core.knowledge_management.repository.source import (
        KnowledgeSourceRepository,
    )

    service = world.get(KnowledgeManagementServiceProtocol)
    assert service.source is world.get(KnowledgeSourceRepository)
    assert service.catalog is world.get(KnowledgeCatalog)
    assert service.catalog.remote is world.get(EcbKnowledgeClient)
    assert service.catalog.remote.client is world.get(
        Annotated[HttpClient, "knowledge"]
    )
    if "/sources" in response.request.url.path or any(
        suffix in response.request.url.path
        for suffix in ("/yuque/", "/ingest-", "/source-revisions/", "/activities")
    ):
        content = world.get(KnowledgeContentServiceProtocol)
        assert content.client is world.get(EcbKnowledgeContentClient)
        assert content.client.transport is world.get(AsyncContentTransport)


# Explicit endpoint inventory: do not derive coverage claims from the live router.
CASES = [
    (
        "GET",
        ROOT + "/bots/{bot_id}/knowledge-bases",
        {"query_params": {"owner_id": ACTOR}},
    ),
    (
        "PUT",
        ROOT + "/bots/{bot_id}/knowledge-bases/{kb_id}/enablement",
        {
            "json_body": {
                "target_owner_id": ACTOR,
                "enabled": True,
                "expected_revision": 0,
            }
        },
    ),
    ("GET", ROOT + "/spaces/{space_id}/knowledge-bases", {}),
    ("GET", KB + "/catalog-detail", {}),
    ("GET", KB + "/authorized-bots", {}),
    ("GET", KB + "/bot-candidates", {}),
    (
        "POST",
        KB + "/bot-authorization-states",
        {"json_body": {"targets": [{"bot_id": "bot", "owner_id": ACTOR}]}},
    ),
    (
        "PUT",
        KB + "/bot-authorizations/{bot_id}",
        {
            "json_body": {
                "target_owner_id": ACTOR,
                "granted": True,
                "expected_revision": 0,
            }
        },
    ),
    (
        "GET",
        ROOT + "/bots/{bot_id}/available-knowledge-bases",
        {"query_params": {"owner_id": ACTOR}},
    ),
    ("GET", KB + "/sources", {}),
    ("POST", KB + "/sources/upload", {"files": [("files", ("file.md", b"# File"))]}),
    (
        "POST",
        KB + "/sources/{source_id}/replace",
        {
            "form_data": {"expected_content_hash": "hash"},
            "files": [("file", ("file.md", b"# New"))],
        },
    ),
    ("DELETE", KB + "/sources/{source_id}", {}),
    ("GET", KB + "/sources/{source_id}/raw", {}),
    ("GET", KB + "/yuque/documents", {}),
    ("POST", KB + "/yuque/import", {"json_body": {"items": [{"doc_id": 1}]}}),
    ("POST", KB + "/sources/{source_id}/sync", {}),
    ("GET", KB + "/sources/{source_id}/sync-runs/{run_id}", {}),
    ("GET", KB + "/sources/{source_id}/auto-sync", {}),
    (
        "PUT",
        KB + "/sources/{source_id}/auto-sync",
        {"json_body": {"enabled": False, "expected_version": 0}},
    ),
    ("GET", KB + "/ingest-tasks", {}),
    ("GET", KB + "/source-revisions/{revision_id}", {}),
    ("GET", KB + "/activities", {}),
    ("GET", KB + "/ingest-jobs/{job_id}", {}),
]

for method, path, inputs in CASES:
    endpoint_test(
        method=method,
        path=path,
        scenario="configured_real_consumer",
        input=CaseInput(
            path_params=PARAMS,
            headers={"x-user-id": ACTOR, "cookie": "test_session=fixture"},
            **inputs,
        ),
        seed=_seed_enabled,
        expect=ExpectSuccess(),
        extra_assertions=(_assert_real_composition,),
    )(lambda: None)
    endpoint_test(
        method=method,
        path=path,
        scenario="disabled_before_dependencies",
        input=CaseInput(
            path_params=PARAMS,
            headers={"x-user-id": ACTOR, "cookie": "test_session=fixture"},
            **inputs,
        ),
        expect=ExpectError(
            status=503,
            json_contains={"detail": {"code": "KNOWLEDGE_INTEGRATION_UNAVAILABLE"}},
        ),
    )(lambda: None)
