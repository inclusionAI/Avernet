"""Consumer HTTP contract: no ECB package, credentials or remote services."""

import httpx
import pytest
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError

from .conftest import make_catalog
from .http_contract import EcbContract, detail, grant


@pytest.fixture
def upstream(world):
    upstream = EcbContract()
    fact = detail(
        name="Actual ECB KB",
        description="Stored description",
        authorized_bot_count=2,
        can_manage=False,
    )
    prefix = "/api/v1/knowledge/integrations/tc/spaces/1/knowledge-bases"
    upstream.reply(
        "GET",
        prefix,
        {"items": [fact], "next_cursor": None},
        params={"actor_staff_no": "actor", "cursor": "", "limit": 1},
    )
    upstream.reply(
        "GET", prefix + "/kb/catalog-detail", fact, params={"actor_staff_no": "actor"}
    )
    for cursor, bot, next_cursor in ((0, "bot", 1), (1, "missing", None)):
        upstream.reply(
            "GET",
            prefix + "/kb/authorized-bots",
            {"items": [grant(bot=bot, platform_env="pre")], "next_cursor": next_cursor},
            params={"actor_staff_no": "actor", "cursor": cursor, "limit": 1},
        )
    with httpx.Client(transport=httpx.MockTransport(upstream.send)) as client:
        world.service.catalog = make_catalog(
            "https://ecb.example", enabled=True, client=client
        )
        yield upstream
    assert not upstream.errors, upstream.errors


def test_ecb_catalog_and_paged_grants_merge(world, upstream):
    listed = world.service.list_in_space(space_id=1, actor_id="actor", limit=1)
    fact = listed["items"][0]
    assert fact["name"] == "Actual ECB KB"
    assert fact["description"] == "Stored description"
    assert fact["document_count"] == 0 and fact["authorized_bot_count"] == 2
    assert fact["updated_at"] is None and fact["updated_at_basis"] == "unavailable"
    assert fact["can_manage"] is False
    first = world.service.authorized_bots(
        space_id=1, actor_id="actor", kb_id="kb", limit=1
    )
    assert first["items"][0]["bot_name"] == "Real bot"
    assert first["items"][0]["enabled"] is False and first["next_cursor"] == 1
    second = world.service.authorized_bots(
        space_id=1, actor_id="actor", kb_id="kb", limit=1, cursor=1
    )
    assert second["items"][0]["bot_id"] == "missing"
    assert (
        second["items"][0]["metadata_missing"] is True
        and second["items"][0]["enabled"] is None
    )
    assert second["next_cursor"] is None
    with pytest.raises(KnowledgeManagementError) as error:
        world.service.candidates(space_id=1, actor_id="actor", kb_id="kb")
    assert error.value.code == "KNOWLEDGE_MANAGE_REQUIRED"


@pytest.mark.parametrize(
    "status,code",
    [(403, "KNOWLEDGE_REMOTE_ACCESS_DENIED"), (404, "KNOWLEDGE_REMOTE_NOT_FOUND")],
)
def test_ecb_revoked_read_never_prefetches_local_bot_data(
    world, upstream, status, code
):
    upstream.reply(
        "GET",
        "/api/v1/knowledge/integrations/tc/spaces/1/knowledge-bases/kb/authorized-bots",
        {"detail": {"code": "not_visible"}},
        params={"actor_staff_no": "actor", "cursor": 0, "limit": 20},
        status=status,
    )
    world.sql.clear()
    with pytest.raises(KnowledgeManagementError) as error:
        world.service.authorized_bots(space_id=1, actor_id="actor", kb_id="kb")
    assert error.value.code == code
    assert not any("ac_bots" in sql for sql in world.sql)
    assert world.enabled.calls == []
    assert len(upstream.requests) == 1
