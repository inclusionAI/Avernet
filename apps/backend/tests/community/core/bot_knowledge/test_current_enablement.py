"""Current selection has no versions; service Bot edits require a draft."""

import pytest
from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel,
)
from agentclaw.community.kernel.knowledge import BotKnowledgeScope, KnowledgeError


def toggle(
    service, scope, *, enabled=True, revision=0, request="toggle", actor="owner"
):
    return service.set_enabled(
        scope=scope,
        actor_id=actor,
        kb_id="kb",
        enabled=enabled,
        expected_revision=revision,
    )


def test_only_current_selection_and_no_grant_columns(knowledge):
    db, repo, service, scope = knowledge
    result = toggle(service, scope)
    assert result["enabled"] and result["revision"] == 1
    assert "granted" not in result and "edited_publish_id" not in result
    assert repo.list_enabled_current(scope) == ("kb",)
    assert repo.list_enabled_current(BotKnowledgeScope("bot", "other", "dev")) == ()
    service.catalog.authorized.clear()
    # Revocation does not delete the saved application choice.
    assert repo.list_enabled_current(scope) == ("kb",)
    assert not toggle(service, scope, enabled=False, revision=1, request="off")[
        "enabled"
    ]
    with pytest.raises(KnowledgeError, match="NOT_GRANTED"):
        toggle(service, scope, revision=2, request="back")
    assert not hasattr(service, "set_grant")
    assert "granted" not in BotKnowledgeBindingModel.__table__.columns


def test_current_cas_no_replay(knowledge):
    _, _, service, scope = knowledge
    first = toggle(service, scope)
    assert toggle(service, scope, revision=1) == first
    with pytest.raises(KnowledgeError, match="REVISION_CONFLICT"):
        toggle(service, scope)
    assert len(service.list_bindings(scope=scope, actor_id="owner")["items"]) == 1


def test_only_owner_or_explicit_editor(knowledge):
    from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel

    db, _, service, scope = knowledge
    with pytest.raises(KnowledgeError, match="BOT_EDITOR_REQUIRED"):
        toggle(service, scope, actor="viewer")
    with db.orm_session() as session:
        session.add(
            BotCollaboratorModel(
                bot_pk=1,
                bot_id="bot",
                owner_id="owner",
                user_id="viewer",
                role="member",
                operator_id="owner",
                env="dev",
            )
        )
    assert toggle(service, scope, actor="viewer")["enabled"]


@pytest.mark.parametrize(
    "status,allowed",
    [
        (None, False),
        ("draft", True),
        ("building", False),
        ("validating", False),
        ("success", False),
        ("released", False),
    ],
)
def test_service_bot_requires_current_draft(knowledge, status, allowed):
    from agentclaw.community.plugin_api.models import BotModel
    from agentclaw.community.core.service_bot.repository.models import BotPublishModel

    db, _, service, scope = knowledge
    with db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 1).update({"bot_type": "service"})
        if status is not None:
            session.add(
                BotPublishModel(
                    source_bot_pk=1,
                    source_bot_id="bot",
                    publish_bot_id="bot",
                    owner_id="owner",
                    name="Bot",
                    status=status,
                    version=1,
                    env="dev",
                    permission_owner="owner",
                )
            )
    if allowed:
        assert toggle(service, scope)["enabled"]
    else:
        with pytest.raises(KnowledgeError, match="BOT_DRAFT_REQUIRED"):
            toggle(service, scope)
    with pytest.raises(TypeError):
        service.set_enabled(
            scope=scope,
            actor_id="owner",
            kb_id="kb",
            enabled=False,
            expected_revision=1,
            publish_id=123,
        )


def test_draft_rechecked_after_remote_catalog(knowledge):
    from agentclaw.community.plugin_api.models import BotModel
    from agentclaw.community.core.service_bot.repository.models import BotPublishModel

    db, _, service, scope = knowledge
    with db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 1).update({"bot_type": "service"})
        session.add(
            BotPublishModel(
                source_bot_pk=1,
                source_bot_id="bot",
                publish_bot_id="bot",
                owner_id="owner",
                name="Bot",
                status="draft",
                version=1,
                env="dev",
                permission_owner="owner",
            )
        )

    def changed(**kwargs):
        assert "bot_uuid" not in kwargs
        assert kwargs["scope"].bot_id == "bot"
        with db.orm_session() as session:
            session.query(BotPublishModel).update({"status": "building"})
        return ("kb",)

    service.catalog.list_authorized = changed
    with pytest.raises(KnowledgeError, match="BOT_DRAFT_REQUIRED"):
        toggle(service, scope)


def test_enabled_limit_and_invalid_values(knowledge):
    from agentclaw.community.core.bot_knowledge.repository.models import (
        BotKnowledgeBindingModel,
    )

    db, repo, service, scope = knowledge
    with db.orm_session() as session:
        session.add_all(
            [
                BotKnowledgeBindingModel(
                    env="dev",
                    bot_pk=1,
                    space_id=1,
                    kb_id=f"k{i}",
                    enabled=True,
                    revision=1,
                    updated_by="owner",
                )
                for i in range(100)
            ]
        )
    with pytest.raises(KnowledgeError, match="LIMIT_EXCEEDED"):
        toggle(service, scope)
    with pytest.raises(ValueError):
        toggle(service, scope, enabled=1)


@pytest.mark.parametrize("latest_status,allowed", [("draft", True), ("success", False)])
def test_draft_guard_uses_latest_existing_publish_version(
    knowledge, latest_status, allowed
):
    from agentclaw.community.plugin_api.models import BotModel
    from agentclaw.community.core.service_bot.repository.models import BotPublishModel

    db, _, service, scope = knowledge
    with db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 1).update({"bot_type": "service"})
        # A backfilled older version can have a larger row ID; it is not current.
        for row_id, version, status in [
            (20, 2, latest_status),
            (21, 1, "success" if allowed else "draft"),
        ]:
            session.add(
                BotPublishModel(
                    id=row_id,
                    source_bot_pk=1,
                    source_bot_id="bot",
                    publish_bot_id="bot",
                    owner_id="owner",
                    name="Bot",
                    status=status,
                    version=version,
                    env="dev",
                    permission_owner="owner",
                )
            )
    if allowed:
        assert toggle(service, scope)["enabled"]
    else:
        with pytest.raises(KnowledgeError, match="BOT_DRAFT_REQUIRED"):
            toggle(service, scope)


def test_disable_does_not_call_unavailable_catalog(knowledge):
    _, _, service, scope = knowledge
    assert toggle(service, scope)["enabled"]

    def unavailable(**_kwargs):
        pytest.fail("disabling must not call the external catalog")

    service.catalog.list_authorized = unavailable
    assert not toggle(service, scope, enabled=False, revision=1)["enabled"]
