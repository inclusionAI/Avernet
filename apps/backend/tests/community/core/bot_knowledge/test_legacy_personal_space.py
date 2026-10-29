"""Use effective personal ownership consistently through selection and CAS."""

from datetime import datetime
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.plugin_api.models import BotModel


@pytest.fixture
def legacy(knowledge):
    db, repo, service, scope = knowledge
    with db.orm_session() as session:
        session.add(
            SpaceModel(
                id=264,
                space_code="personal-owner",
                space_type="PERSONAL",
                personal_owner_id="owner",
                name="Personal",
                env="dev",
                created_by="owner",
                updated_by="owner",
            )
        )
        session.get(BotModel, 1).space_id = None
    return knowledge


def toggle(service, scope, *, enabled=True, revision=0, actor="owner"):
    return service.set_enabled(
        scope=scope,
        actor_id=actor,
        kb_id="kb",
        enabled=enabled,
        expected_revision=revision,
    )


def test_null_bot_selection_uses_personal_space_without_persisting_bot_move(legacy):
    db, repo, service, scope = legacy
    service.catalog.list_authorized = Mock(return_value=("kb",))
    assert repo.get_bot(scope, actor_id="owner")["space_id"] == 264
    assert toggle(service, scope)["revision"] == 1
    service.catalog.list_authorized.assert_called_once_with(scope=scope, space_id=264)
    assert service.list_enabled_current(scope) == ("kb",)
    assert service.list_bindings(scope=scope, actor_id="owner")["items"][0]["enabled"]
    with pytest.raises(KnowledgeError, match="REVISION_CONFLICT"):
        toggle(service, scope, enabled=False)
    # Existing UI normalization is not a new space; keep selection and revision.
    with db.orm_session() as session:
        assert session.get(BotModel, 1).space_id is None
        session.get(BotModel, 1).space_id = 264
    assert service.list_enabled_current(scope) == ("kb",)
    assert toggle(service, scope, enabled=False, revision=1)["revision"] == 2
    assert service.list_enabled_current(scope) == ()


@pytest.mark.parametrize("actor", ["owner", "editor"])
def test_null_bot_preserves_native_personal_owner_editor_policy(legacy, actor):
    db, _, service, scope = legacy
    with db.orm_session() as session:
        session.add(
            BotCollaboratorModel(
                bot_pk=1,
                bot_id="bot",
                owner_id="owner",
                user_id="editor",
                role="member",
                operator_id="owner",
                env="dev",
            )
        )
    assert toggle(service, scope, actor=actor)["enabled"]
    with pytest.raises(KnowledgeError, match="BOT_EDITOR_REQUIRED"):
        toggle(service, scope, actor="viewer", enabled=False, revision=1)


@pytest.mark.parametrize("change", ["missing", "deleted", "other-env", "other-owner"])
def test_missing_valid_personal_space_fails_closed_without_creating_it(legacy, change):
    db, repo, service, scope = legacy
    with db.orm_session() as session:
        space = session.get(SpaceModel, 264)
        if change == "missing":
            session.delete(space)
        elif change == "deleted":
            space.deleted_at = datetime.now()
        elif change == "other-env":
            space.env = "pre"
        else:
            space.personal_owner_id = "other-owner"
    with pytest.raises(KnowledgeError, match="SPACE_NOT_FOUND"):
        toggle(service, scope)
    assert repo.list_enabled_current(scope) == ()


def test_write_rechecks_actual_space_after_catalog_roundtrip(legacy):
    db, _, service, scope = legacy

    def move(**kwargs):
        assert kwargs["space_id"] == 264
        with db.orm_session() as session:
            session.get(BotModel, 1).space_id = 1
        return ("kb",)

    service.catalog.list_authorized = move
    with pytest.raises(KnowledgeError, match="SPACE_MISMATCH"):
        toggle(service, scope)
    assert service.list_enabled_current(scope) == ()


def test_explicit_bad_space_does_not_fall_back_to_personal(legacy):
    db, repo, _, scope = legacy
    with db.orm_session() as session:
        session.get(BotModel, 1).space_id = 999
    with pytest.raises(KnowledgeError, match="SPACE_NOT_FOUND"):
        repo.get_bot(scope, actor_id="owner")


def test_normalization_during_remote_check_is_the_same_effective_space(legacy):
    db, _, service, scope = legacy

    def normalize(**kwargs):
        assert kwargs["space_id"] == 264
        with db.orm_session() as session:
            session.get(BotModel, 1).space_id = 264
        return ("kb",)

    service.catalog.list_authorized = normalize
    assert toggle(service, scope)["revision"] == 1
    assert service.list_enabled_current(scope) == ("kb",)


def test_null_bot_binding_reclaims_old_space_without_resetting_cas(legacy):
    from agentclaw.community.core.bot_knowledge.repository.models import (
        BotKnowledgeBindingModel as Binding,
    )

    db, _, service, scope = legacy
    with db.orm_session() as session:
        session.add(
            Binding(
                bot_pk=1,
                space_id=1,
                env="dev",
                kb_id="kb",
                enabled=True,
                revision=9,
                updated_by="owner",
            )
        )
    assert service.list_enabled_current(scope) == ()
    assert service.list_bindings(scope=scope, actor_id="owner")["items"] == []
    with pytest.raises(KnowledgeError, match="REVISION_CONFLICT"):
        toggle(service, scope, revision=9)
    assert toggle(service, scope)["revision"] == 10
    with db.orm_session() as session:
        assert session.get(BotModel, 1).space_id is None


def test_null_bot_enablement_limit_uses_personal_space_only(legacy):
    from agentclaw.community.core.bot_knowledge.repository.models import (
        BotKnowledgeBindingModel as Binding,
    )

    db, _, service, scope = legacy
    with db.orm_session() as session:
        session.add_all(
            [
                Binding(
                    bot_pk=1,
                    space_id=264,
                    env="dev",
                    kb_id=f"kb-{i}",
                    enabled=True,
                    revision=1,
                    updated_by="owner",
                )
                for i in range(100)
            ]
        )
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_LIMIT_EXCEEDED"):
        toggle(service, scope)
    assert len(service.list_enabled_current(scope)) == 100


def test_null_bot_retains_service_draft_restriction(legacy):
    db, _, service, scope = legacy
    with db.orm_session() as session:
        session.get(BotModel, 1).bot_type = "service"
    with pytest.raises(KnowledgeError, match="BOT_DRAFT_REQUIRED"):
        toggle(service, scope)
