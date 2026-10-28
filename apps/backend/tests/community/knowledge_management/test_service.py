from unittest.mock import Mock

import pytest
from agentclaw.community.core.knowledge_management.repository.source import (
    ExistingKnowledgeEnablementReader,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

from .conftest import fact, grant

ARGS = {"space_id": 1, "kb_id": "kb", "actor_id": "actor"}


def test_list_preserves_ecb_facts_and_unavailable_business_clock(world):
    result = world.service.list_in_space(space_id=1, actor_id="actor", limit=10)
    item = result["items"][0]
    assert item == fact() | {"space_name": "Actual space"}
    assert item["updated_at"] is None
    assert item["authorized_bot_count"] == 205  # Not the current page length.
    assert result["knowledge_bases"] == result["items"]
    assert world.enabled.calls == []


def test_legacy_page_names_remain_supported(world):
    world.catalog.page = {"knowledge_bases": [fact()], "next_cursor": None, "total": 35}
    result = world.service.list_in_space(
        space_id=1, actor_id="actor", cursor="old-kb", limit=5
    )
    assert result["items"] == result["knowledge_bases"]
    assert result["total"] == 35
    assert world.catalog.calls[0][1]["limit"] == 5


def test_detail_uses_catalog_read_without_bot_editor(world):
    assert world.service.detail(**ARGS)["space_name"] == "Actual space"
    assert [call[0] for call in world.catalog.calls] == ["detail"]


@pytest.mark.parametrize("operation", ["list", "detail", "authorized", "candidates"])
def test_nonmember_denied_before_any_ecb_or_display_read(world, operation):
    kwargs = dict(ARGS, actor_id="stranger")
    with pytest.raises(KnowledgeManagementError) as error:
        if operation == "list":
            world.service.list_in_space(space_id=1, actor_id="stranger")
        else:
            getattr(
                world.service,
                "authorized_bots" if operation == "authorized" else operation,
            )(**kwargs)
    assert error.value.code == "SPACE_MEMBER_REQUIRED"
    assert world.catalog.calls == []
    assert not any("ac_bots" in sql for sql in world.sql if sql.startswith("SELECT"))


@pytest.mark.parametrize("target", ["space", "membership"])
def test_removed_space_or_inactive_member_denied(world, target):
    from datetime import datetime

    with world.db.orm_session() as session:
        if target == "space":
            session.query(SpaceModel).filter_by(id=1).update(
                {"deleted_at": datetime.now()}
            )
        else:
            session.query(SpaceMemberModel).filter_by(user_id="actor").update(
                {"status": "INACTIVE"}
            )
    with pytest.raises(KnowledgeManagementError, match="SPACE_MEMBER_REQUIRED"):
        world.service.detail(**ARGS)
    assert world.catalog.calls == []


def test_nonteamclaw_tenant_cannot_enumerate_space(world):
    with avernet_tenant_scope("unrelated"):
        with pytest.raises(KnowledgeManagementError, match="SPACE_MEMBER_REQUIRED"):
            world.service.detail(**ARGS)
    assert world.catalog.calls == []


@pytest.mark.parametrize("operation", ["authorized_bots", "candidates"])
def test_ecb_denial_precedes_bot_directory_queries(world, operation):
    world.catalog.denied = True
    with pytest.raises(KnowledgeManagementError) as error:
        getattr(world.service, operation)(**ARGS)
    assert error.value.code == "KNOWLEDGE_REMOTE_ACCESS_DENIED"
    assert [call[0] for call in world.catalog.calls] == ["detail"]
    assert world.enabled.calls == []
    assert not any("ac_bots" in sql for sql in world.sql if sql.startswith("SELECT"))


def test_reader_can_read_grants_but_not_candidates_without_manage(world):
    world.catalog.kb["can_manage"] = False
    assert world.service.authorized_bots(**ARGS)["items"][0]["bot_name"] == "Real bot"
    with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_MANAGE_REQUIRED"):
        world.service.candidates(**ARGS)


def test_composite_identity_batch_merge_preserves_grants(world):
    world.catalog.grants = {
        "items": [grant(), grant(owner_id="other-owner")],
        "next_cursor": 9,
    }
    world.enabled.values = {("bot", "owner"): True}
    result = world.service.authorized_bots(**ARGS, limit=2)
    first, second = result["items"]
    assert first["bot_name"] == "Real bot"
    assert first["owner_name"] == "Existing nickname"
    assert first["avatar_url"] == "https://img.example/avatar.png"
    assert first["enabled"] is True
    assert second["bot_name"] == "Other owner bot"
    assert second["bot_type"] == "service"
    assert second["owner_name"] == "Stored owner"
    assert second["avatar_url"] is None
    assert second["enabled"] is False
    assert first.items() >= grant().items()
    assert result["next_cursor"] == 9
    assert len(world.enabled.calls) == 1
    assert (
        len([sql for sql in world.sql if sql.startswith("SELECT") and "ac_bots" in sql])
        == 1
    )


@pytest.mark.parametrize("bot_id", ["outside", "prod", "deleted", "missing"])
def test_unavailable_bot_retains_fact_without_cross_scope_display(world, bot_id):
    world.catalog.grants = {"items": [grant(bot_id)], "next_cursor": None}
    item = world.service.authorized_bots(**ARGS)["items"][0]
    assert item["bot_id"] == bot_id
    assert item["granted"] is True
    assert item["bot_available"] is False
    assert item["enabled"] is None
    assert item["bot_name"] is None
    assert item["owner_name"] is None
    assert world.enabled.calls == []


@pytest.mark.parametrize(
    "mutation",
    [
        {"kb_id": "other"},
        {"platform": "other"},
        {"platform_env": "prod"},
        {"granted": False},
        {"granted": "true"},
        {"user_id": "other"},
        {"bot_owner_id": ""},
    ],
)
def test_bad_or_cross_scope_grant_fails_before_local_enrichment(world, mutation):
    world.catalog.grants["items"] = [grant(**mutation)]
    with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_GRANTS_INVALID"):
        world.service.authorized_bots(**ARGS)
    assert world.enabled.calls == []
    assert not any("ac_bots" in sql for sql in world.sql if sql.startswith("SELECT"))


def test_duplicate_grant_rejected(world):
    world.catalog.grants["items"] *= 2
    with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_GRANTS_INVALID"):
        world.service.authorized_bots(**ARGS)


@pytest.mark.parametrize("cursor", [-1, "2", True, 0, 2**63])
def test_bad_grant_cursor_rejected(world, cursor):
    world.catalog.grants["next_cursor"] = cursor
    with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_GRANTS_INVALID"):
        world.service.authorized_bots(**ARGS)


@pytest.mark.parametrize("operation", ["list", "detail"])
def test_cross_kb_or_space_catalog_fact_rejected(world, operation):
    world.catalog.kb["space_id"] = "2"
    with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_CATALOG_INVALID"):
        if operation == "list":
            world.service.list_in_space(space_id=1, actor_id="actor")
        else:
            world.service.detail(**ARGS)


def test_detail_cannot_return_a_different_kb(world):
    world.catalog.kb["kb_id"] = "different"
    with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_CATALOG_INVALID"):
        world.service.detail(**ARGS)


def test_candidates_bounded_search_real_name_and_owner_not_global_directory(world):
    result = world.service.candidates(**ARGS, limit=1)
    assert len(result["items"]) == 1
    assert result["next_cursor"] == 1
    next_page = world.service.candidates(**ARGS, cursor=1, limit=1)
    assert next_page["items"][0]["owner_id"] == "other-owner"
    assert next_page["next_cursor"] is None
    assert (
        world.service.candidates(**ARGS, keyword="Existing nickname")["items"][0][
            "bot_id"
        ]
        == "bot"
    )
    assert len(world.service.candidates(**ARGS, keyword="Stored owner")["items"]) == 1
    assert world.service.candidates(**ARGS, keyword="SECRET")["items"] == []
    assert world.service.candidates(**ARGS, keyword="%_")["items"] == []


def test_revoke_retained_grant_after_bot_soft_delete_uses_ecb_state(world):
    world.catalog.grant_states = Mock(
        return_value={
            "items": [
                {"bot_id": "bot", "owner_id": "owner", "revision": 2, "granted": True}
            ]
        }
    )
    world.catalog.set_grant = Mock(return_value={"granted": False, "revision": 3})
    with world.db.orm_session() as session:
        session.query(BotModel).filter_by(id=1).update({"is_delete": 1})

    result = world.service.set_grant(
        space_id=1,
        kb_id="kb",
        actor_id="actor",
        bot_id="bot",
        owner_id="owner",
        granted=False,
        expected_revision=2,
    )

    assert result == {"granted": False, "revision": 3}
    world.catalog.grant_states.assert_called_once_with(
        kb_id="kb", actor_id="actor", identities=[("bot", "owner")], env="pre"
    )
    world.catalog.set_grant.assert_called_once_with(
        kb_id="kb",
        actor_id="actor",
        bot_id="bot",
        owner_id="owner",
        granted=False,
        expected_revision=2,
    )


def test_missing_display_values_not_fabricated(world):
    with world.db.orm_session() as session:
        session.query(BotModel).filter_by(id=2).update(
            {"bot_name": None, "owner_name": None, "bot_type": None, "ext": "broken"}
        )
    world.catalog.grants["items"] = [grant(owner_id="other-owner")]
    item = world.service.authorized_bots(**ARGS)["items"][0]
    assert all(
        item[key] is None
        for key in ("bot_name", "owner_name", "avatar_url", "bot_type")
    )


def test_ambiguous_bot_identity_fails_closed(world):
    with world.db.orm_session() as session:
        session.add(
            BotModel(
                bot_id="bot",
                owner_id="owner",
                entity_id="other-entity",
                entity_type="user",
                creator_id="owner",
                env="pre",
                space_id=1,
            )
        )
    with pytest.raises(
        KnowledgeManagementError, match="KNOWLEDGE_BOT_IDENTITY_AMBIGUOUS"
    ):
        world.service.authorized_bots(**ARGS)


def test_enablement_failure_never_becomes_false(world):
    world.enabled.fail = True
    with pytest.raises(
        KnowledgeManagementError, match="KNOWLEDGE_ENABLEMENT_UNAVAILABLE"
    ):
        world.service.authorized_bots(**ARGS)


def test_missing_public_binding_model_is_explicit_dependency_block(world):
    world.service.enablement = ExistingKnowledgeEnablementReader(world.db, "pre")
    with pytest.raises(
        KnowledgeManagementError, match="KNOWLEDGE_ENABLEMENT_UNAVAILABLE"
    ):
        world.service.authorized_bots(**ARGS)


def test_empty_grant_page_does_not_require_missing_enablement_dependency(world):
    world.service.enablement = ExistingKnowledgeEnablementReader(world.db, "pre")
    world.catalog.grants["items"] = []
    assert world.service.authorized_bots(**ARGS)["items"] == []


@pytest.mark.parametrize("method", ["list_in_space", "authorized_bots", "candidates"])
@pytest.mark.parametrize("limit", [0, 101, True])
def test_invalid_page_size_rejected_without_any_remote_call(world, method, limit):
    kwargs = {"space_id": 1, "actor_id": "actor", "limit": limit}
    if method != "list_in_space":
        kwargs["kb_id"] = "kb"
    with pytest.raises(KnowledgeManagementError) as error:
        getattr(world.service, method)(**kwargs)
    assert error.value.code == "INVALID_KNOWLEDGE_INPUT"
    assert world.catalog.calls == []


def test_upstream_page_over_limit_rejected(world):
    world.catalog.grants["items"] = [grant(), grant(owner_id="other-owner")]
    with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_GRANTS_INVALID"):
        world.service.authorized_bots(**ARGS, limit=1)


def test_only_current_grant_page_enriched(world):
    world.catalog.grants = {
        "items": [grant(owner_id="other-owner")],
        "next_cursor": 101,
    }
    result = world.service.authorized_bots(**ARGS, cursor=100, limit=1)
    assert len(result["items"]) == 1
    assert [call[0] for call in world.catalog.calls] == ["detail", "grants"]
    assert set(world.enabled.calls[0]["bots"]) == {("bot", "other-owner")}


def test_all_missing_page_keeps_grant_count_and_cursor_progress(world):
    world.catalog.grants = {
        "items": [grant("deleted"), grant("outside")],
        "next_cursor": 78,
    }
    result = world.service.authorized_bots(**ARGS, cursor=5, limit=2)
    assert result["next_cursor"] == 78
    assert len(result["items"]) == 2
    assert all(
        item["metadata_missing"] and item["enabled"] is None for item in result["items"]
    )
    assert world.service.detail(**ARGS)["authorized_bot_count"] == 205
    assert world.enabled.calls == []


def test_service_protocol_conformance(world):
    from agentclaw.community.api.knowledge_management_service import (
        KnowledgeManagementServiceProtocol,
    )

    assert isinstance(world.service, KnowledgeManagementServiceProtocol)


@pytest.mark.parametrize(
    "change",
    [{"id": None}, {"id": True}, {"revision": "1"}, {"revision": 0}, {"updated_by": 1}],
)
def test_malformed_grant_facts_fail_before_binding_read(world, change):
    world.catalog.grants["items"] = [grant(**change)]
    with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_GRANTS_INVALID"):
        world.service.authorized_bots(**ARGS)
    assert world.enabled.calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"document_count": -1},
        {"document_count": True},
        {"authorized_bot_count": "2"},
        {"updated_at": 1},
        {"created_at": 1},
    ],
)
def test_malformed_kb_stats_or_clock_not_turned_into_defaults(world, change):
    world.catalog.kb.update(change)
    with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_CATALOG_INVALID"):
        world.service.detail(**ARGS)


def test_maximum_page_enrichment_is_one_bot_query_and_one_binding_read(world):
    with world.db.orm_session() as session:
        for i in range(100):
            session.add(
                BotModel(
                    bot_id=f"bulk-{i}",
                    owner_id="owner",
                    entity_id="owner",
                    entity_type="user",
                    creator_id="owner",
                    env="pre",
                    space_id=1,
                    bot_name=f"Bulk {i}",
                )
            )
    world.catalog.grants["items"] = [grant(f"bulk-{i}") for i in range(100)]
    world.sql.clear()
    result = world.service.authorized_bots(**ARGS, limit=100)
    assert len(result["items"]) == 100
    assert sum("ac_bots" in sql for sql in world.sql) == 1
    assert len(world.enabled.calls) == 1
    assert len(world.enabled.calls[0]["bots"]) == 100


def test_semantic_error_contract_contains_no_http_status():
    import inspect

    error = KnowledgeManagementError("KNOWLEDGE_ENABLEMENT_UNAVAILABLE")
    assert list(inspect.signature(KnowledgeManagementError).parameters) == ["code"]
    assert error.__dict__ == {"code": "KNOWLEDGE_ENABLEMENT_UNAVAILABLE"}
    assert error.args == ("KNOWLEDGE_ENABLEMENT_UNAVAILABLE",)
    assert not hasattr(error, "status")
    assert not hasattr(error, "status_code")
