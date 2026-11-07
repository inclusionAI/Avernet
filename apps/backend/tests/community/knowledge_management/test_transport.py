import httpx
import pytest
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError

from .conftest import make_catalog


@pytest.mark.parametrize(
    "operation,suffix,params",
    [
        (
            "list_in_space",
            "",
            {"actor_staff_no": "actor", "cursor": "previous-kb", "limit": "3"},
        ),
        ("detail", "/kb/catalog-detail", {"actor_staff_no": "actor"}),
        (
            "authorized_bots",
            "/kb/authorized-bots",
            {"actor_staff_no": "actor", "cursor": "40", "limit": "3"},
        ),
    ],
)
def test_exact_new_ecb_read_paths_and_no_service_token(operation, suffix, params):
    calls = []

    def transport(request):
        calls.append(request)
        assert (
            request.url.path
            == "/api/v1/knowledge/integrations/tc/spaces/1/knowledge-bases" + suffix
        )
        assert dict(request.url.params) == params
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={"items": [], "next_cursor": None}
            if operation == "list_in_space"
            else {"ok": True},
        )

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        catalog = make_catalog("https://ecb.example", enabled=True, client=client)
        kwargs = {"space_id": 1, "actor_id": "actor"}
        if operation != "list_in_space":
            kwargs["kb_id"] = "kb"
        if operation != "detail":
            kwargs.update(
                cursor="previous-kb" if operation == "list_in_space" else 40, limit=3
            )
        assert getattr(catalog, operation)(**kwargs) == (
            {"items": [], "next_cursor": None}
            if operation == "list_in_space"
            else {"ok": True}
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "KNOWLEDGE_REMOTE_INVALID_INPUT"),
        (403, "KNOWLEDGE_REMOTE_ACCESS_DENIED"),
        (404, "KNOWLEDGE_REMOTE_NOT_FOUND"),
        (409, "KNOWLEDGE_REMOTE_CONFLICT"),
        (410, "KNOWLEDGE_REMOTE_RETIRED"),
    ],
)
def test_remote_native_rejections_preserved_without_body(status, code):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="SECRET")
        )
    ) as client:
        catalog = make_catalog("https://ecb.example", enabled=True, client=client)
        with pytest.raises(KnowledgeManagementError) as error:
            catalog.detail(space_id=1, kb_id="kb", actor_id="actor")
        assert error.value.code == code
        assert not hasattr(error.value, "status")
        assert "SECRET" not in str(error.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"location": "https://evil.example"}),
        httpx.Response(401, text="SECRET"),
        httpx.Response(500, text="SECRET"),
        httpx.Response(200, json=[]),
        httpx.Response(200, text="bad-json"),
        httpx.Response(200, content=b"x" * (1024 * 1024 + 1)),
    ],
)
def test_remote_failures_and_size_limit_do_not_become_empty(response):
    calls = []

    def transport(request):
        calls.append(request)
        return response

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        catalog = make_catalog("https://ecb.example", enabled=True, client=client)
        with pytest.raises(KnowledgeManagementError) as error:
            catalog.detail(space_id=1, kb_id="kb", actor_id="actor")
        assert error.value.code == "KNOWLEDGE_DEPENDENCY_UNAVAILABLE"
        assert str(error.value) == "KNOWLEDGE_DEPENDENCY_UNAVAILABLE"
    assert len(calls) == 1


def test_remote_timeout_fails_closed():
    def transport(request):
        raise httpx.ReadTimeout("SECRET origin")

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        catalog = make_catalog("https://ecb.example", enabled=True, client=client)
        with pytest.raises(
            KnowledgeManagementError, match="KNOWLEDGE_DEPENDENCY_UNAVAILABLE"
        ):
            catalog.detail(space_id=1, kb_id="kb", actor_id="actor")


@pytest.mark.parametrize(
    "origin",
    [
        "http://ecb.example",
        "https://a:b@ecb.example",
        "https://ecb.example/path",
        "https://ecb.example?query=1",
        "//ecb.example",
    ],
)
def test_only_trusted_https_origin_config(origin):
    with pytest.raises(ValueError):
        make_catalog(origin)


def test_default_disabled_and_missing_origin_are_unavailable():
    for catalog in (
        make_catalog("https://ecb.example"),
        make_catalog("", enabled=True),
    ):
        with pytest.raises(
            KnowledgeManagementError,
            match="KNOWLEDGE_(INTEGRATION|DEPENDENCY)_UNAVAILABLE",
        ):
            catalog.detail(space_id=1, kb_id="kb", actor_id="actor")
