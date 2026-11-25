from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.task.forum.interaction.target_verifier import (
    DatabaseForumInteractionTargetVerifier,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


class _NoopForumRepository:
    pass


class _SqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        finally:
            db.close()


def _make_verifier(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'forum_target_verifier.db'}",
        connect_args={"check_same_thread": False},
    )
    BotModel.__table__.create(engine)
    db = _SqliteDB(engine)
    return DatabaseForumInteractionTargetVerifier(_NoopForumRepository(), db), db


def _add_default_bot(db, owner_id):
    with db.orm_session() as s:
        s.add(
            BotModel(
                bot_id="default",
                entity_id=f"staff_{owner_id}",
                entity_type="staff",
                creator_id=owner_id,
                owner_id=owner_id,
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
        )


def test_verify_bot_resolves_owner_scoped_default_identity(tmp_path):
    verifier, db = _make_verifier(tmp_path)
    _add_default_bot(db, "149844")
    _add_default_bot(db, "200001")

    assert verifier.verify_bot("default:149844") is True
    assert verifier.verify_bot("default:200001") is True
    assert verifier.verify_bot("default:999999") is False


def test_verify_bot_rejects_bare_id_but_keeps_scoped_named_bot_id_behaviour(tmp_path):
    verifier, db = _make_verifier(tmp_path)
    with db.orm_session() as session:
        session.add(
            BotModel(
                bot_id="bot-a",
                entity_id="staff_149844",
                entity_type="staff",
                creator_id="149844",
                owner_id="149844",
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
        )

    assert verifier.verify_bot("bot-a:149844") is True
    assert verifier.verify_bot("bot-a:200001") is False
    assert verifier.verify_bot("bot-a") is False
    assert verifier.verify_bot("default") is False
    assert verifier.verify_bot("ghost") is False
