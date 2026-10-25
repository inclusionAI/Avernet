"""Tenant lineage and native Personal/Team editor regression tests."""

from unittest.mock import Mock

import pytest
from sqlalchemy import delete

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
)
from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel,
)
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.repository.implementations.bot.collaborator import (
    CollaboratorRepository,
)
from agentclaw.community.core.repository.implementations.spaces.space import (
    SpaceRepository,
)
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.core.spaces.services.space_access_service import (
    SpaceAccessService,
)
from agentclaw.community.kernel.knowledge import BotKnowledgeScope, KnowledgeError
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


def publish(session, pk=1, *, bot_id="default", version=1, status="draft"):
    session.add(
        BotPublishModel(
            source_bot_pk=pk,
            source_bot_id=bot_id,
            publish_bot_id=bot_id,
            owner_id="owner",
            name="Bot",
            status=status,
            version=version,
            env="dev",
            permission_owner="owner",
        )
    )


def default_bot(db):
    with db.orm_session() as session:
        session.query(BotModel).filter_by(id=1).update(
            {"bot_id": "default", "bot_type": "service"}
        )
    return BotKnowledgeScope("default", "owner", "dev")


def toggle(service, scope, actor="owner", enabled=True, revision=0):
    return service.set_enabled(
        scope=scope,
        actor_id=actor,
        kb_id="kb",
        enabled=enabled,
        expected_revision=revision,
    )


@pytest.mark.parametrize(
    "own_status,foreign_status", [("success", "draft"), ("draft", "success")]
)
def test_default_latest_publication_is_tenant_local(
    knowledge, own_status, foreign_status
):
    db, repo, service, _ = knowledge
    scope = default_bot(db)
    with db.orm_session() as session:
        publish(session, status=own_status)
    with avernet_tenant_scope("external-b"):
        with db.orm_session() as session:
            bot = BotModel(
                bot_id="default",
                bot_name="B",
                entity_id="owner",
                entity_type="staff",
                creator_id="owner",
                owner_id="owner",
                env="dev",
                space_id=1,
                bot_type="service",
            )
            session.add(bot)
            session.flush()
            publish(session, bot.id, version=9, status=foreign_status)
    if own_status == "draft":
        assert toggle(service, scope)["revision"] == 1
        assert repo.list_enabled_current(scope) == ("kb",)
    else:
        with pytest.raises(KnowledgeError, match="BOT_DRAFT_REQUIRED"):
            toggle(service, scope)
        with db.orm_session() as session:
            assert session.query(BotKnowledgeBindingModel).count() == 0


def test_unresolved_default_lineage_fails_closed(knowledge):
    db, _, service, _ = knowledge
    scope = default_bot(db)
    with db.orm_session() as session:
        publish(session, pk=999, version=9)
    with pytest.raises(KnowledgeError, match="BOT_DRAFT_REQUIRED"):
        toggle(service, scope)


def test_unresolved_success_cannot_reveal_older_draft(knowledge):
    db, _, service, _ = knowledge
    scope = default_bot(db)
    with db.orm_session() as session:
        publish(session)
        publish(session, pk=999, version=9, status="success")
    with pytest.raises(KnowledgeError, match="BOT_DRAFT_REQUIRED"):
        toggle(service, scope)


def test_retained_legacy_default_source_pk_is_not_rebound(knowledge):
    db, _, service, _ = knowledge
    scope = default_bot(db)
    # Legacy recovery preserves an older source PK. A retained (even deleted)
    # source record is tenant evidence; requiring source PK == current PK is not.
    with db.orm_session() as session:
        old = BotModel(
            bot_id="retired-default",
            bot_name="Old",
            entity_id="owner",
            entity_type="staff",
            creator_id="owner",
            owner_id="owner",
            env="dev",
            space_id=1,
            is_delete=1,
        )
        session.add(old)
        session.flush()
        publish(session, old.id)
    assert toggle(service, scope)["enabled"]


def native_editor(db, *, personal):
    with db.orm_session() as session:
        session.query(BotModel).filter_by(id=1).update({"bot_type": "service"})
        publish(session, bot_id="bot")
        if personal:
            session.query(SpaceModel).filter_by(id=1).update(
                {"space_type": "PERSONAL", "personal_owner_id": "owner"}
            )
            session.execute(
                delete(SpaceMemberModel).where(SpaceMemberModel.user_id != "owner")
            )
    native = CollaboratorService(
        CollaboratorRepository(db),
        BotRepository(db),
        Mock(),
        Mock(),
        SpaceAccessService(SpaceRepository(db)),
    )
    native.add_editor(
        bot_id="bot",
        owner_id="owner",
        user_id="viewer",
        operator_id="owner",
        role="member",
        env="dev",
    )
    bot = BotRepository(db).get_by_pk(1)
    assert (
        native.get_explicit_permission_level(bot=bot, user_id="viewer", env="dev").name
        == "MEMBER"
    )


def test_personal_native_editor_reads_enables_and_disables(knowledge):
    db, _, service, scope = knowledge
    native_editor(db, personal=True)
    assert service.list_bindings(scope=scope, actor_id="viewer")["items"] == []
    first = toggle(service, scope, actor="viewer")
    assert first["enabled"]
    assert service.list_bindings(scope=scope, actor_id="viewer")["items"] == [first]
    service.catalog.authorized.clear()
    assert not toggle(service, scope, actor="viewer", enabled=False, revision=1)[
        "enabled"
    ]


@pytest.mark.parametrize("revocation", ["member", "owner", "editor", "space"])
def test_team_revocation_is_rechecked_in_mutation_transaction(knowledge, revocation):
    db, _, service, scope = knowledge
    native_editor(db, personal=False)

    def revoke(**_):
        with db.orm_session() as session:
            if revocation in {"member", "owner"}:
                user = "viewer" if revocation == "member" else "owner"
                session.query(SpaceMemberModel).filter_by(user_id=user).update(
                    {"status": "INACTIVE"}
                )
            elif revocation == "editor":
                session.query(BotCollaboratorModel).filter_by(user_id="viewer").delete()
            else:
                from datetime import datetime

                session.query(SpaceModel).filter_by(id=1).update(
                    {"deleted_at": datetime.now()}
                )
        return ("kb",)

    service.catalog.list_authorized = revoke
    with pytest.raises(KnowledgeError):
        toggle(service, scope, actor="viewer")
    with db.orm_session() as session:
        assert session.query(BotKnowledgeBindingModel).count() == 0


def test_team_departed_editor_cannot_read_or_disable(knowledge):
    db, _, service, scope = knowledge
    native_editor(db, personal=False)
    toggle(service, scope)
    with db.orm_session() as session:
        session.query(SpaceMemberModel).filter_by(user_id="viewer").update(
            {"status": "INACTIVE"}
        )
    with pytest.raises(KnowledgeError, match="SPACE_MEMBER_REQUIRED"):
        service.list_bindings(scope=scope, actor_id="viewer")
    with pytest.raises(KnowledgeError, match="SPACE_MEMBER_REQUIRED"):
        toggle(service, scope, actor="viewer", enabled=False, revision=1)


def test_personal_explicit_editor_revocation_rechecked_in_transaction(knowledge):
    db, _, service, scope = knowledge
    native_editor(db, personal=True)

    def revoke(**_):
        with db.orm_session() as session:
            session.query(BotCollaboratorModel).filter_by(user_id="viewer").delete()
        return ("kb",)

    service.catalog.list_authorized = revoke
    with pytest.raises(KnowledgeError, match="BOT_EDITOR_REQUIRED"):
        toggle(service, scope, actor="viewer")
    with db.orm_session() as session:
        assert session.query(BotKnowledgeBindingModel).count() == 0


def test_default_draft_lineage_rechecked_after_catalog(knowledge):
    db, _, service, _ = knowledge
    scope = default_bot(db)
    with db.orm_session() as session:
        publish(session)

    def change_draft(**_):
        with db.orm_session() as session:
            session.query(BotPublishModel).filter_by(source_bot_pk=1).update(
                {"status": "success"}
            )
        with avernet_tenant_scope("external-b"):
            with db.orm_session() as session:
                bot = BotModel(
                    bot_id="default",
                    bot_name="B",
                    entity_id="owner",
                    entity_type="staff",
                    creator_id="owner",
                    owner_id="owner",
                    env="dev",
                    space_id=1,
                )
                session.add(bot)
                session.flush()
                publish(session, bot.id, version=99)
        return ("kb",)

    service.catalog.list_authorized = change_draft
    with pytest.raises(KnowledgeError, match="BOT_DRAFT_REQUIRED"):
        toggle(service, scope)
    with db.orm_session() as session:
        assert session.query(BotKnowledgeBindingModel).count() == 0
