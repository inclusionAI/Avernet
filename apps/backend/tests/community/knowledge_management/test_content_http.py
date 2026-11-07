"""BFF-to-ECB wire contract with real HTTP adapters; no upstream success fiction."""

import json
from types import SimpleNamespace

import httpx
import pytest
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.knowledge_management.content_router import router
from agentclaw.community.adapters.knowledge_platform.catalog import EcbKnowledgeClient
from agentclaw.community.adapters.knowledge_platform.config import (
    KnowledgeIntegrationConfig,
)
from agentclaw.community.adapters.knowledge_platform.content import (
    RemoteKnowledgeContentService,
)
from agentclaw.community.adapters.knowledge_platform.content_client import (
    EcbKnowledgeContentClient,
)
from agentclaw.community.api.knowledge_management_service import (
    KnowledgeManagementServiceProtocol,
)
from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.plugins.http_client import AsyncHttpxClient
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import CallableProvider, Injector


from agentclaw.community.api.knowledge_content_service import (
    KnowledgeContentServiceProtocol,
)
from agentclaw.community.core.knowledge_management.config import (
    KnowledgeManagementConfig,
)


def prepare_test_access(actor_id, session):
    from agentclaw.community.kernel.knowledge_content import ContentAccess

    if session.get("test_actor", actor_id) != actor_id:
        raise KnowledgeError("KNOWLEDGE_IDENTITY_MISMATCH", 403)
    return ContentAccess(
        actor_id,
        {"Cookie": f"session={session['session']}" if "session" in session else ""},
    )


@pytest.fixture
def content():
    seen, scope = [], []
    options = {"status": 200, "body": {"items": []}, "deny": False}

    def send(request):
        seen.append(request)
        if options.get("require_session_context") and (
            request.headers.get("referer") != "https://ecb.test/"
            or request.headers.get("cookie") != "session=dummy"
        ):
            return httpx.Response(401, json={"detail": {"code": "UNAUTHENTICATED"}})
        if request.url.path.endswith("/raw"):
            return httpx.Response(
                200,
                content=b"# original\n",
                headers={"content-type": "text/markdown", "set-cookie": "bad=bad"},
            )
        if options.get("metadata") is not None and "/sources/" in request.url.path:
            suffix = request.url.path.rsplit("/sources/", 1)[1]
            if request.method == "GET" and "/" not in suffix:
                return httpx.Response(200, json=options["metadata"])
        return httpx.Response(options["status"], json=options["body"])

    def authorize_space(**kwargs):
        scope.append(kwargs)
        if options["deny"]:
            raise KnowledgeError("SPACE_MEMBER_REQUIRED", 403)

    config = KnowledgeIntegrationConfig(True, "https://ecb.test")
    remote = EcbKnowledgeContentClient(
        EcbKnowledgeClient(config.catalog_origin, config),
        AsyncHttpxClient(transport=httpx.MockTransport(send)),
    )
    injector = Injector()
    for interface, value in (
        (KnowledgeIntegrationConfig, config),
        (
            KnowledgeManagementConfig,
            KnowledgeManagementConfig(config.enabled, "teamclaw"),
        ),
        (
            KnowledgeContentServiceProtocol,
            RemoteKnowledgeContentService(remote, prepare_test_access),
        ),
        (EcbKnowledgeContentClient, remote),
        (
            KnowledgeManagementServiceProtocol,
            SimpleNamespace(authorize_space=authorize_space),
        ),
    ):
        injector.binder.bind(interface, to=CallableProvider(lambda value=value: value))
    app = FastAPI()
    attach_injector(app, injector)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        "owner", "owner", "owner"
    )
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            options=options,
            seen=seen,
            scope=scope,
            injector=injector,
            prefix="/api/knowledge/spaces/1/knowledge-bases/kb",
        )


def test_scoped_list_and_only_session_cookie_forwarded(content):
    w = content
    response = w.client.get(
        w.prefix + "/sources?offset=2&limit=3",
        headers={"Cookie": "session=dummy; arbitrary=secret", "x-user-id": "spoof"},
    )
    assert response.status_code == 200
    assert w.scope == [{"space_id": 1, "actor_id": "owner"}]
    request = w.seen[-1]
    assert (
        str(request.url)
        == "https://ecb.test/api/v1/knowledge/kb/kb/sources?offset=2&limit=3"
    )
    assert request.headers["cookie"] == "session=dummy"
    assert "x-user-id" not in request.headers
    assert "set-cookie" not in response.headers


def test_denied_scope_and_mismatched_local_identity_never_call_ecb(content):
    w = content
    w.options["deny"] = True
    assert w.client.get(w.prefix + "/sources").status_code == 403
    w.options["deny"] = False
    assert (
        w.client.get(
            w.prefix + "/sources", headers={"Cookie": "test_actor=other"}
        ).status_code
        == 403
    )
    assert w.seen == []


def test_actual_multipart_upload_conflict_and_add(content):
    w = content
    w.options.update(
        status=409,
        body={
            "detail": {
                "error_code": "DUPLICATE_FILENAME",
                "duplicates": [{"source_id": "old", "filename": "a.md"}],
            }
        },
    )
    result = w.client.post(
        w.prefix + "/sources/upload",
        files={"files": ("a.md", b"# new", "text/markdown")},
    )
    assert result.status_code == 409
    assert result.json()["detail"]["duplicates"][0]["source_id"] == "old"
    assert b'name="on_name_conflict"\r\n\r\nask' in w.seen[-1].content
    w.options.update(status=200, body={"ingest_job_id": "job"})
    result = w.client.post(
        w.prefix + "/sources/upload",
        data={"on_name_conflict": "add"},
        files={"files": ("a.md", b"# new")},
    )
    assert result.status_code == 200
    assert b'name="on_name_conflict"\r\n\r\nadd' in w.seen[-1].content
    assert b"# new" in w.seen[-1].content
    rejected = w.client.post(
        w.prefix + "/sources/upload", files={"files": ("a.pdf", b"bad")}
    )
    assert rejected.status_code == 400
    assert rejected.json() == {"detail": "Only TXT and Markdown files are supported"}


def test_replace_preserves_source_and_hash_cas(content):
    w = content
    assert (
        w.client.post(
            w.prefix + "/sources/s/replace",
            data={"expected_content_hash": "hash"},
            files={"file": ("a.md", b"new")},
        ).status_code
        == 200
    )
    assert w.seen[-1].url.path.endswith("/kb/kb/sources/s/replace")
    assert b'name="file"' in w.seen[-1].content and b"hash" in w.seen[-1].content
    assert w.client.delete(w.prefix + "/sources/s").status_code == 200
    assert w.seen[-1].method == "DELETE"


def test_yuque_query_url_not_reparsed_and_batch_source_kb_is_server_controlled(content):
    w = content
    url = "https://yuque.example/a/b/c"
    assert (
        w.client.get(w.prefix + "/yuque/documents", params={"q": url}).status_code
        == 200
    )
    assert w.seen[-1].url.params["q"] == url and w.seen[-1].url.params["kb_id"] == "kb"
    assert (
        w.client.post(
            w.prefix + "/yuque/import", json={"items": [{"source_url": url}]}
        ).status_code
        == 200
    )
    assert json.loads(w.seen[-1].content) == {
        "kb_id": "kb",
        "items": [{"source_url": url}],
    }
    assert (
        w.client.post(
            w.prefix + "/yuque/import",
            json={"kb_id": "other", "items": [{"doc_id": 1}]},
        ).status_code
        == 422
    )


def test_schedule_is_document_not_connector_and_keeps_version(content):
    w = content
    body = {
        "enabled": True,
        "expected_version": 4,
        "schedule": {"frequency": "weekly", "time": "10:30", "weekdays": [2]},
    }
    assert (
        w.client.put(w.prefix + "/sources/yuque-17/auto-sync", json=body).status_code
        == 200
    )
    assert (
        w.seen[-1].url.path == "/api/v1/knowledge/knowledge-sources/yuque-17/auto-sync"
    )
    assert json.loads(w.seen[-1].content)["expected_version"] == 4
    assert w.client.post(w.prefix + "/sources/yuque-17/sync").status_code == 200
    assert w.seen[-1].url.path.endswith("/knowledge-sources/yuque-17/sync")
    assert w.client.get(w.prefix + "/sources/yuque-17/auto-sync").status_code == 200


def test_foreign_source_never_reaches_global_source_mutation_or_raw(content):
    w = content
    w.options.update(status=404, body={"detail": "source not found"})
    assert w.client.post(w.prefix + "/sources/foreign/sync").status_code == 404
    assert len(w.seen) == 1
    assert w.client.get(w.prefix + "/sources/foreign/raw").status_code == 404
    assert len(w.seen) == 2


def test_raw_streams_original_and_drops_upstream_cookie(content):
    w = content
    response = w.client.get(w.prefix + "/sources/s/raw")
    assert response.status_code == 200 and response.content == b"# original\n"
    assert "set-cookie" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "suffix", ["ingest-tasks", "activities", "source-revisions/r1"]
)
def test_status_and_update_records_are_forwarded(content, suffix):
    w = content
    assert w.client.get(w.prefix + "/" + suffix).status_code == 200
    assert w.seen[-1].url.path.endswith("/kb/kb/" + suffix)


def test_login_requirement_and_outage_not_turned_into_success(content):
    w = content
    w.options.update(status=401, body={"detail": "need_login"})
    assert w.client.get(w.prefix + "/yuque/documents").status_code == 401
    w.options.update(status=503, body={"detail": "must not echo secret"})
    response = w.client.get(w.prefix + "/sources")
    assert response.status_code == 503 and "secret" not in response.text


def test_provider_access_denied_reaches_frontend_after_local_gate(content):
    w = content
    w.options.update(status=403, body={"detail": {"code": "KB_PERMISSION_DENIED"}})
    assert w.client.get(w.prefix + "/sources").status_code == 403
    assert w.scope == [{"space_id": 1, "actor_id": "owner"}]
    assert len(w.seen) == 1


def test_document_sync_run_status_is_scoped_to_exact_kb_and_source(content):
    w = content
    body = {
        "run_id": "run-1",
        "status": "COMPLETED",
        "target": {"kb_id": "kb", "source_id": "yuque-17", "document_sync_version": 1},
        "result": {"document_sync": {"phase": "NOOP"}},
    }
    w.options.update(body=body)
    response = w.client.get(w.prefix + "/sources/yuque-17/sync-runs/run-1")
    assert response.status_code == 200 and response.json() == body
    assert w.seen[-1].url.path == "/api/v1/knowledge/data-source/sync-runs/run-1"
    assert len(w.seen) == 2  # KB-scoped source check, then the existing ECB run API.
    for target in (
        {"kb_id": "other", "source_id": "yuque-17"},
        {"kb_id": "kb", "source_id": "other"},
    ):
        w.options["body"] = {**body, "target": target}
        response = w.client.get(w.prefix + "/sources/yuque-17/sync-runs/run-1")
        assert response.status_code == 404 and "COMPLETED" not in response.text
    w.options.update(status=404, body={"detail": "source not found"})
    w.seen.clear()
    assert (
        w.client.get(w.prefix + "/sources/missing/sync-runs/run-1").status_code == 404
    )
    assert len(w.seen) == 1


def test_document_sync_run_status_rejects_malformed_or_different_run(content):
    w = content
    for body in (
        {"target": []},
        {"run_id": "different", "target": {"kb_id": "kb", "source_id": "s"}},
    ):
        w.options["body"] = body
        assert w.client.get(w.prefix + "/sources/s/sync-runs/run-1").status_code == 502


@pytest.mark.parametrize(
    ("method", "suffix", "upstream_detail", "body"),
    [
        (
            "POST",
            "sync",
            {"code": "YUQUE_CREDENTIAL_MISSING", "message": "authorize first"},
            None,
        ),
        (
            "PUT",
            "auto-sync",
            "YUQUE_CREDENTIAL_MISSING",
            {"enabled": True, "expected_version": 2},
        ),
    ],
)
def test_ecb_credential_412_reaches_frontend_after_successful_source_check(
    content, method, suffix, upstream_detail, body
):
    w = content
    w.options.update(
        status=412,
        body={"detail": upstream_detail},
        metadata={"source_id": "s", "kb_id": "kb"},
    )
    result = w.client.request(method, w.prefix + "/sources/s/" + suffix, json=body)
    assert result.status_code == 412 and result.json() == {"detail": upstream_detail}
    assert len(w.seen) == 2
    assert w.seen[0].url.path == "/api/v1/knowledge/kb/kb/sources/s"
    assert w.seen[1].url.path == "/api/v1/knowledge/knowledge-sources/s/" + suffix


@pytest.mark.parametrize("operation", ["list", "upload", "raw"])
def test_content_uses_ecb_referer_and_real_session_not_browser_origin(
    content, operation
):
    w = content
    w.options["require_session_context"] = True
    headers = {
        "Cookie": "session=dummy; arbitrary=secret",
        "Referer": "https://untrusted.test/page?private=value",
        "Origin": "https://untrusted.test",
    }
    if operation == "upload":
        response = w.client.post(
            w.prefix + "/sources/upload",
            headers=headers,
            files={"files": ("a.md", b"# new")},
        )
    else:
        suffix = "/sources/s/raw" if operation == "raw" else "/sources"
        response = w.client.get(w.prefix + suffix, headers=headers)
    assert response.status_code == 200
    assert len(w.seen) == (2 if operation == "raw" else 1)
    for request in w.seen:
        assert request.headers["referer"] == "https://ecb.test/"
        assert request.headers["cookie"] == "session=dummy"
        assert request.url.host == "ecb.test"
        assert "origin" not in request.headers
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("suffix", ["/sources", "/sources/s/raw"])
def test_ecb_referer_does_not_replace_missing_session(content, suffix):
    w = content
    w.options["require_session_context"] = True
    response = w.client.get(w.prefix + suffix)
    assert response.status_code == 401
    assert response.json() == {"detail": {"code": "UNAUTHENTICATED"}}
    assert len(w.seen) == 1
    assert w.seen[0].headers["referer"] == "https://ecb.test/"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin", ["https://knowledge-pre.example", "https://knowledge-prod.example"]
)
async def test_json_and_raw_use_configured_ecb_environment_without_following_redirect(
    origin,
):
    seen = []

    def redirect(request):
        seen.append(request)
        return httpx.Response(
            302, headers={"location": "https://untrusted.test/collect"}
        )

    config = KnowledgeIntegrationConfig(True, origin)
    remote = EcbKnowledgeContentClient(
        EcbKnowledgeClient(origin, config),
        AsyncHttpxClient(transport=httpx.MockTransport(redirect)),
    )
    for raw in (False, True):
        with pytest.raises(KnowledgeError) as caught:
            if raw:
                await remote.raw(
                    "/api/v1/knowledge/sources/s/raw",
                    headers={"Cookie": "session=dummy"},
                )
            else:
                await remote.call(
                    "GET",
                    "/api/v1/knowledge/kb/kb/sources",
                    headers={"Cookie": "session=dummy"},
                )
        assert caught.value.status == 503
    assert len(seen) == 2
    for request in seen:
        assert request.headers["referer"] == origin + "/"
        assert str(request.url).startswith(origin + "/api/v1/knowledge/")


def test_business_job_read_is_kb_scoped_not_global(content):
    w = content
    job = {
        "job_id": "job",
        "status": "failed",
        "items": [{"filename": "a.md", "status": "failed", "error": "build failed"}],
    }
    w.options["body"] = {"total": 1, "offset": 0, "limit": 100, "jobs": [job]}
    result = w.client.get(w.prefix + "/ingest-jobs/job")
    assert result.status_code == 200
    assert result.json() == job
    assert w.seen[-1].url.path == "/api/v1/knowledge/kb/kb/ingest-jobs"
    assert dict(w.seen[-1].url.params) == {"offset": "0", "limit": "100"}
    assert len(w.scope) == 1


def test_business_job_not_found_only_after_authoritative_end(content):
    w = content
    w.options["body"] = {
        "total": 1,
        "offset": 0,
        "limit": 100,
        "jobs": [{"job_id": "unrelated", "status": "succeeded", "items": []}],
    }
    result = w.client.get(w.prefix + "/ingest-jobs/other-kb-job")
    assert result.status_code == 404
    assert "unrelated" not in result.text
    w.options["deny"] = True
    assert w.client.get(w.prefix + "/ingest-jobs/job").status_code == 403
    assert len(w.seen) == 1


@pytest.mark.parametrize("status", [401, 403, 404, 409])
def test_business_job_preserves_upstream_rejections(content, status):
    content.options.update(status=status, body={"detail": {"code": "UPSTREAM"}})
    response = content.client.get(content.prefix + "/ingest-jobs/job")
    assert response.status_code == status


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"total": 1, "offset": 0, "limit": 100, "jobs": []},
        {"total": 1, "offset": 0, "limit": 100, "jobs": [{"job_id": "job"}]},
    ],
)
def test_business_job_malformed_is_not_missing_or_success(content, body):
    content.options["body"] = body
    response = content.client.get(content.prefix + "/ingest-jobs/job")
    assert response.status_code == 502


def test_business_job_second_page_and_scan_budget(content):
    from fastapi_injector import get_injector_instance

    w = content
    calls = []

    def send(request):
        calls.append(request)
        offset = int(request.url.params["offset"])
        jobs = [
            {"job_id": f"job-{offset + n}", "status": "succeeded", "items": []}
            for n in range(100)
        ]
        return httpx.Response(
            200, json={"total": 301, "offset": offset, "limit": 100, "jobs": jobs}
        )

    remote = get_injector_instance(w.client.app).get(EcbKnowledgeContentClient)
    remote.transport = AsyncHttpxClient(transport=httpx.MockTransport(send))
    result = w.client.get(w.prefix + "/ingest-jobs/job-150")
    assert result.status_code == 200 and result.json()["job_id"] == "job-150"
    assert len(calls) == 2
    calls.clear()
    result = w.client.get(w.prefix + "/ingest-jobs/job-250")
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "KNOWLEDGE_JOB_LOOKUP_LIMIT"
    assert len(calls) == 2


def test_business_job_explicit_wrong_kb_fails_closed(content):
    content.options["body"] = {
        "total": 1,
        "offset": 0,
        "limit": 100,
        "jobs": [
            {"kb_id": "other", "job_id": "job", "status": "succeeded", "items": []}
        ],
    }
    assert content.client.get(content.prefix + "/ingest-jobs/job").status_code == 502


def test_yuque_connection_projects_current_session_after_kb_gate(content):
    w = content
    w.options["body"] = {
        "connected": False,
        "authorization_url": "https://ecb.test/oauth/login",
    }
    response = w.client.get(w.prefix + "/yuque/connection")
    assert response.status_code == 200
    assert response.json()["connected"] is False
    assert response.headers["cache-control"] == "no-store"
    assert (
        w.seen[-1].url.path
        == "/api/v1/knowledge/data-source/connectors/yuque/connection"
    )
    assert w.seen[-1].url.query == b""
    assert w.scope[-1]["actor_id"] == "owner"
    w.options["deny"] = True
    before = len(w.seen)
    assert w.client.get(w.prefix + "/yuque/connection").status_code == 403
    assert len(w.seen) == before


def test_yuque_connection_requires_login_before_remote_request(content):
    w = content

    # Match the existing authentication dependency's HTTP contract, not a provider error.
    def require_login():
        raise HTTPException(401, detail={"code": "UNAUTHENTICATED"})

    w.client.app.dependency_overrides[get_current_user] = require_login
    assert w.client.get(w.prefix + "/yuque/connection").status_code == 401
    assert w.seen == []


def test_yuque_connection_disabled_before_remote_request(content):
    w = content
    w.injector.binder.bind(
        KnowledgeManagementConfig,
        to=CallableProvider(lambda: KnowledgeManagementConfig()),
    )
    assert w.client.get(w.prefix + "/yuque/connection").status_code == 503
    assert w.seen == []
