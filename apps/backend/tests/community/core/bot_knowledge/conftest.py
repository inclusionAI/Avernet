from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.bot_knowledge.repository.store import (
    BotKnowledgeRepository,
)
from agentclaw.community.core.bot_knowledge.service import BotKnowledgeService
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.kernel.knowledge import (
    BotKnowledgeScope,
)
from agentclaw.community.plugin_api.models import BotModel


class TestDatabase:
    __test__ = False

    def __init__(self, engine):
        self.factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def transactional_orm_session(self):
        with self.factory.begin() as session:
            yield session

    orm_session = transactional_orm_session


class LocalCatalog:
    def __init__(self):
        self.authorized = {"kb"}

    def list_authorized(self, **kwargs):
        return tuple(self.authorized)

    def list_in_space(self, **kwargs):
        return {"items": [], "next_cursor": None}


@pytest.fixture
def knowledge(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'knowledge.db'}")
    Base.metadata.create_all(engine)
    db = TestDatabase(engine)
    with db.orm_session() as session:
        session.add(
            SpaceModel(
                id=1,
                space_code="team",
                space_type="TEAM",
                name="Team",
                env="dev",
                created_by="manager",
                updated_by="manager",
            )
        )
        session.add_all(
            [
                SpaceMemberModel(
                    space_id=1,
                    user_id=u,
                    role="MEMBER",
                    env="dev",
                    created_by="manager",
                )
                for u in ("owner", "other", "manager", "viewer")
            ]
        )
        session.add_all(
            [
                BotModel(
                    id=i,
                    bot_id=b,
                    bot_name=b,
                    entity_id=u,
                    entity_type="staff",
                    creator_id=u,
                    owner_id=u,
                    env="dev",
                    space_id=1,
                    bot_type="personal",
                )
                for i, b, u in (
                    (1, "bot", "owner"),
                    (2, "bot2", "owner"),
                    (3, "bot", "other"),
                )
            ]
        )
    repo = BotKnowledgeRepository(db)
    yield (
        db,
        repo,
        BotKnowledgeService(repo, LocalCatalog()),
        BotKnowledgeScope("bot", "owner", "dev"),
    )
    engine.dispose()


@pytest.fixture
def observation(knowledge):
    from agentclaw.community.core.bot_knowledge.publication_selection import (
        BotKnowledgePublicationSelection,
    )
    from agentclaw.community.core.repository.implementations.config.bot_common_config import (
        BotCommonConfigRepository,
    )
    from agentclaw.community.core.service_bot.repository.models import (
        BotPublishModel,
        PublishStatus,
    )

    db, _, current, scope = knowledge
    current.catalog.authorized = ("kb", "kb-b")
    # Seed with the normal current selection path before becoming a service Bot.
    current.set_enabled(
        scope=scope, actor_id="owner", kb_id="kb", enabled=True, expected_revision=0
    )
    with db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 1).update({"bot_type": "service"})
        session.add(
            BotPublishModel(
                id=101,
                source_bot_pk=1,
                source_bot_id="bot",
                publish_bot_id="botpub1",
                name="Bot",
                owner_id="legacy-owner",
                status=PublishStatus.BUILDING,
                version=1,
                env="dev",
                permission_owner="owner",
            )
        )
    return (
        db,
        BotKnowledgePublicationSelection(db, BotCommonConfigRepository(db)),
        scope,
    )
