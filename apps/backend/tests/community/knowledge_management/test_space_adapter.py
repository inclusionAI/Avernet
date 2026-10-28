"""The outbound adapter preserves identities/IDs and never retries uncertain writes."""

import json

import httpx
import pytest
from agentclaw.community.adapters.knowledge_platform.space_knowledge import (
    EcbSpaceKnowledgeConfig,
    EcbSpaceKnowledgeProvider,
)
from agentclaw.community.plugin_api.space_knowledge import (
    SpaceKnowledgeError,
    SyncedSpace,
)

SPACE = SyncedSpace("7", "pre", 12)
PAYLOAD = {
    "kb_id": "kb-one",
    "tenant_id": "tenant-one",
    "kb_name": "Knowledge",
    "description": "",
    "kgbase_status": "creating",
    "created_by": "001234",
    "owner_staff_no": "001234",
}


def provider(handler, **config):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return EcbSpaceKnowledgeProvider(
        EcbSpaceKnowledgeConfig(
            enabled=True, base_url="https://ecb.test", tc_env="pre", **config
        ),
        client,
    ), client


def test_create_preserves_actor_revision_and_stable_request_id():
    seen = []

    def handler(request):
        seen.append(request)
        assert "authorization" not in request.headers
        assert json.loads(request.content) == {
            "request_id": "stable",
            "expected_space_revision": 12,
            "actor_staff_no": "001234",
            "kb_name": "Knowledge",
            "description": "",
        }
        return httpx.Response(202, json=PAYLOAD)

    adapter, client = provider(handler)
    with client:
        result = adapter.create(
            space=SPACE,
            actor_id="001234",
            request_id="stable",
            name="Knowledge",
            description="",
        )
    assert result.accepted and result.state.status == "creating"
    assert result.state.owner_id == "001234"
    assert len(seen) == 1


@pytest.mark.parametrize(
    "status,code",
    [
        (409, "CREATE_REQUEST_CONFLICT"),
        (409, "SPACE_REVISION_MISMATCH"),
        (410, "KB_DELETED"),
        (403, "SPACE_MEMBER_REQUIRED"),
        (404, "RESOURCE_NOT_FOUND"),
    ],
)
def test_safe_business_conflicts_preserved(status, code):
    def _callback(r):
        return httpx.Response(
            status, json={"detail": {"code": code, "secret": "must-not-leak"}}
        )

    adapter, client = provider(_callback)
    with client, pytest.raises(SpaceKnowledgeError, match=f"^{code}$"):
        adapter.get(space=SPACE, actor_id="001234", kb_id="kb-one")


@pytest.mark.parametrize(
    "case",
    ["timeout", "bad-json", "bad-state", "redirect", "upstream-auth", "raw-error"],
)
def test_dependencies_are_sanitized_without_automatic_retry(case):
    calls = []

    def handler(request):
        calls.append(request)
        if case == "timeout":
            raise httpx.ReadTimeout("Bearer private-value")
        if case == "bad-json":
            return httpx.Response(200, text="secret")
        if case == "bad-state":
            return httpx.Response(200, json={**PAYLOAD, "kgbase_status": "unknown"})
        if case == "redirect":
            return httpx.Response(302, headers={"Location": "https://other.test"})
        if case == "upstream-auth":
            return httpx.Response(
                403, json={"detail": {"code": "TC_SPACE_AUTH_INVALID"}}
            )
        return httpx.Response(500, text="private stack")

    adapter, client = provider(handler)
    with client, pytest.raises(SpaceKnowledgeError, match="^DEPENDENCY_UNAVAILABLE$"):
        adapter.create(
            space=SPACE,
            actor_id="001234",
            request_id="stable",
            name="Knowledge",
            description="",
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "case", ["disabled", "missing-endpoint", "missing-environment", "environment"]
)
def test_closed_before_any_io(case):
    calls = []
    adapter, client = provider(lambda r: calls.append(r))
    if case == "disabled":
        adapter._config = EcbSpaceKnowledgeConfig()
    if case == "missing-endpoint":
        adapter._config = adapter._config.model_copy(update={"base_url": ""})
    if case == "missing-environment":
        adapter._config = adapter._config.model_copy(update={"tc_env": ""})
    space = SyncedSpace("7", "prod", 12) if case == "environment" else SPACE
    with client, pytest.raises(SpaceKnowledgeError, match="^DEPENDENCY_UNAVAILABLE$"):
        adapter.get(space=space, actor_id="001234", kb_id="kb-one")
    assert not calls


@pytest.mark.parametrize(
    "kb_id", [".", "..", "folder/other", "folder\\other", "white space"]
)
def test_resource_path_cannot_be_reinterpreted(kb_id):
    seen = []
    adapter, client = provider(lambda request: seen.append(request))
    with client, pytest.raises(SpaceKnowledgeError, match="^INVALID_INPUT$"):
        adapter.get(space=SPACE, actor_id="001234", kb_id=kb_id)
    assert seen == []


@pytest.mark.parametrize("method", ["get"])
def test_status_and_retry_reject_a_different_canonical_id(method):
    adapter, client = provider(
        lambda request: httpx.Response(200, json={**PAYLOAD, "kb_id": "replacement"})
    )
    with client, pytest.raises(SpaceKnowledgeError, match="^DEPENDENCY_UNAVAILABLE$"):
        getattr(adapter, method)(space=SPACE, actor_id="001234", kb_id="kb-one")


def test_list_scoped_envelope_projection_and_current_actor():
    def handler(request):
        assert "authorization" not in request.headers
        assert request.method == "GET"
        assert (
            request.url.path
            == "/api/v1/knowledge/integrations/tc/spaces/7/knowledge-bases"
        )
        assert request.url.params["actor_staff_no"] == "001234"
        return httpx.Response(
            200,
            json={
                "space_id": "7",
                "knowledge_bases": [
                    {
                        **PAYLOAD,
                        "ext": {"secret": "private"},
                        "last_error": "private",
                        "kgbase_id": "physical",
                    }
                ],
            },
        )

    adapter, client = provider(handler)
    with client:
        rows = adapter.list_knowledge_bases(space=SPACE, actor_id="001234")
    assert rows.knowledge_bases[0].kb_id == "kb-one"
    assert not hasattr(rows.knowledge_bases[0], "last_error")


@pytest.mark.parametrize(
    "payload",
    [
        {"space_id": "8", "knowledge_bases": []},
        {"space_id": "7", "knowledge_bases": None},
        {"space_id": "7", "knowledge_bases": [PAYLOAD, PAYLOAD]},
        {"space_id": "7", "knowledge_bases": [{**PAYLOAD, "kgbase_status": "invalid"}]},
    ],
)
def test_list_rejects_wrong_scope_duplicate_or_malformed_items(payload):
    adapter, client = provider(lambda request: httpx.Response(200, json=payload))
    with client, pytest.raises(SpaceKnowledgeError, match="^DEPENDENCY_UNAVAILABLE$"):
        adapter.list_knowledge_bases(space=SPACE, actor_id="001234")


@pytest.mark.parametrize(
    "cursor,rows,next_cursor",
    [
        ("", [PAYLOAD], "folder/next"),
        ("", [PAYLOAD], "white space"),
        ("kb-one", [PAYLOAD], None),
        ("kb-zero", [PAYLOAD], "kb-zzzz"),
        ("kb-one", [], "kb-one"),
        ("", [PAYLOAD], "kb-aaa"),
    ],
)
def test_list_rejects_invalid_or_nonprogressing_provider_page(
    cursor, rows, next_cursor
):
    def _callback(request):
        return httpx.Response(
            200,
            json={"space_id": "7", "knowledge_bases": rows, "next_cursor": next_cursor},
        )

    adapter, client = provider(_callback)
    with client, pytest.raises(SpaceKnowledgeError, match="^DEPENDENCY_UNAVAILABLE$"):
        adapter.list_knowledge_bases(space=SPACE, actor_id="001234", cursor=cursor)


def test_empty_filtered_page_preserves_forward_cursor():
    def _callback(request):
        return httpx.Response(
            200, json={"space_id": "7", "knowledge_bases": [], "next_cursor": "kb-two"}
        )

    adapter, client = provider(_callback)
    with client:
        page = adapter.list_knowledge_bases(
            space=SPACE, actor_id="001234", cursor="kb-one"
        )
    assert page.knowledge_bases == [] and page.next_cursor == "kb-two"
