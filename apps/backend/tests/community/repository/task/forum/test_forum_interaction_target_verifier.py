"""BOT-target verifier tests for BBS interaction legacy aliases."""

from __future__ import annotations

import pytest

from agentclaw.community.core.repository.implementations.task.forum.forum_repository import (
    ForumRepository,
)
from agentclaw.community.core.task.forum.interaction import target_verifier
from agentclaw.community.core.task.forum.interaction.target_verifier import (
    DatabaseForumInteractionTargetVerifier,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

_ENV = "bbs-test-env"
_TENANT = "bbs-test-tenant"


def _bot(number: int, **overrides) -> BotModel:
    """Create a legacy-style default Bot owned by another actor."""
    base = {
        "bot_id": "default",
        "entity_id": f"entity-{number}",
        "entity_type": "staff",
        "creator_id": f"owner-{number}",
        "owner_id": f"owner-{number}",
        "status": "ACTIVE",
        "env": _ENV,
        "avernet_tenant": _TENANT,
    }
    base.update(overrides)
    return BotModel(**base)


def _insert(db, *bots: BotModel) -> None:
    with db.orm_session() as session:
        session.add_all(bots)


def _verifier(db) -> DatabaseForumInteractionTargetVerifier:
    return DatabaseForumInteractionTargetVerifier(
        ForumRepository(db),
        db,
    )


@pytest.fixture
def scoped_env(monkeypatch):
    monkeypatch.setattr(target_verifier, "get_current_env", lambda: _ENV)
    with avernet_tenant_scope(_TENANT):
        yield


def test_verify_bot_accepts_multiple_legacy_default_rows_under_merged_env_tenant(
    scoped_env,
    db,
):
    _insert(db, _bot(1), _bot(2))

    assert _verifier(db).verify_bot("default") is True


def test_verify_bot_rejects_soft_deleted_default_only(scoped_env, db):
    _insert(db, _bot(1, is_delete=1))

    assert _verifier(db).verify_bot("default") is False


def test_verify_bot_preserves_unique_non_default_bot(scoped_env, db):
    _insert(db, _bot(1, bot_id="bot-a", entity_id="entity-a"))

    assert _verifier(db).verify_bot("bot-a") is True
    assert _verifier(db).verify_bot("missing-bot") is False


def test_verify_bot_remains_scoped_to_env(scoped_env, db):
    with avernet_tenant_scope(_TENANT):
        _insert(
            db,
            BotModel(
                bot_id="default",
                entity_id="other-env-entity",
                entity_type="staff",
                creator_id="owner-other-env",
                owner_id="owner-other-env",
                status="ACTIVE",
                env="other-env",
                avernet_tenant=_TENANT,
            ),
        )

    assert _verifier(db).verify_bot("default") is False


def test_verify_bot_remains_scoped_to_tenant(scoped_env, db):
    with avernet_tenant_scope("other-tenant"):
        _insert(
            db,
            BotModel(
                bot_id="default",
                entity_id="other-tenant-entity",
                entity_type="staff",
                creator_id="owner-other-tenant",
                owner_id="owner-other-tenant",
                status="ACTIVE",
                env=_ENV,
                avernet_tenant="other-tenant",
            ),
        )

    assert _verifier(db).verify_bot("default") is False
