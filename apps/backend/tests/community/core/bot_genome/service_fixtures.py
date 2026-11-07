"""A registry and a promotion service over in-memory SQLite, a tmp content store and fakes."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.content_store.service import ContentStoreService
from agentclaw.community.core.bot_genome.models import BotRef, Provenance, ProvenanceKind
from agentclaw.community.core.apply_engine.completion import ApplyOutcome
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.bot_genome.promotion.service import PROMOTABLE, GenomePromotionService
from agentclaw.community.core.bot_genome.promotion.settle import PromotionSettler
from agentclaw.community.core.bot_genome.registry import GenomeRegistryService
from agentclaw.community.core.bot_genome.spec_checks import SpecChecks
from agentclaw.community.core.repository.implementations.bot.genome import (
    GenomePatchRepository,
    GenomePromotionRepository,
    GenomeRefRepository,
    GenomeRevisionRepository,
)
from agentclaw.community.core.repository.implementations.bot.content_store import ContentStoreRepository
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from tests.community.core.bot_genome.test_capture import FakeReader
from tests.community.core.bot_genome.test_spec_checks import FakeAuth, FakeConfig

BOT = BotRef(
    entity_id="staff_user_owner_5", bot_id="bot_123", owner_id="user_owner_5", engine_type="openclaw", bot_type="arca"
)
OTHER_BOT = BotRef(
    entity_id="staff_user_owner_9",
    bot_id="bot_weather",
    owner_id="user_owner_9",
    engine_type="openclaw",
    bot_type="arca",
)
OWNER = Provenance(ProvenanceKind.USER, "user_owner_5")
RUN = Provenance(ProvenanceKind.STRATEGY_RUN, "clawevolve/bot-evolution@2.0.0", run_id="run_7f3", step="propose")


class InMemorySqliteDB:
    def __init__(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        from agentclaw.community.core.base import Base

        Base.metadata.create_all(engine)
        self._session_factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    transactional_orm_session = orm_session


class FakeApplier:
    """Accepts by default; ``refuse`` makes preflight raise it, ``fail`` makes start raise it.

    ``start`` answers an apply id and records the call; nothing is applied. The world's ``deliver`` plays
    the apply's end: it publishes an outcome to the real settler, as the apply engine would.
    """

    def __init__(self) -> None:
        self.refuse: Exception | None = None
        self.on_preflight = None
        self.fail: Exception | None = None
        self.preflights: list = []
        self.starts: list = []

    def preflight(self, bot, state) -> None:
        self.preflights.append((bot, state))
        if self.on_preflight is not None:
            self.on_preflight()  # something that happens while the service is between its read and its write
        if self.refuse is not None:
            raise self.refuse

    def start(self, bot, *, promotion_id, principal_id, audit_actor) -> str:
        self.starts.append((bot, promotion_id, principal_id, audit_actor))
        if self.fail is not None:
            raise self.fail
        return f"ap_{len(self.starts)}"


class World:
    """Everything a registry test needs, built once per test."""

    def __init__(self, tmp_path: Path) -> None:
        self.db = InMemorySqliteDB()
        self.revisions = GenomeRevisionRepository(self.db)
        self.refs = GenomeRefRepository(self.db)
        self.patches = GenomePatchRepository(self.db)
        self.promotions = GenomePromotionRepository(self.db)
        self.content = ContentStoreService(ContentStoreRepository(self.db), tmp_path / "store")
        self.reader = FakeReader()
        self.auth, self.config = FakeAuth(), FakeConfig()
        self.validator = SkillPackageValidator(SkillParser())
        self.registry = GenomeRegistryService(
            revisions=self.revisions,
            refs=self.refs,
            patches=self.patches,
            promotions=self.promotions,
            content=self.content,
            state_reader=lambda: self.reader,
            checks=SpecChecks(validator=self.validator, mcp_auth=self.auth, mcp_config=self.config),
            validator=self.validator,
            env=lambda: "dev",
        )
        self.applier = FakeApplier()
        self.promotion = GenomePromotionService(
            registry=self.registry,
            refs=self.refs,
            promotions=self.promotions,
            applier=self.applier,
            env=lambda: "dev",
        )
        self.settler = PromotionSettler(promotions=self.promotions, promotable=tuple(s.value for s in PROMOTABLE))

    def deliver(self, promotion, status: ApplyStatus = ApplyStatus.SUCCEEDED, summary: str = "applied"):
        """The promotion's apply ends with ``status``: publish it to the settler, answer the promotion as settled."""
        self.settler.settle(
            env="dev",
            entity_id=BOT.entity_id,
            bot_id=BOT.bot_id,
            promotion_id=promotion.id,
            outcome=ApplyOutcome(promotion.apply_id or "ap_0", status, summary),
        )
        return self.promotion.get_promotion(BOT, promotion.id)

    async def promote(self, revision, *, expected, actor, reason="r", status: ApplyStatus = ApplyStatus.SUCCEEDED):
        """Promote and let the delivery end with ``status``; answers the settled promotion."""
        promoted = await self.promotion.promote(
            BOT, revision=revision, ref="active", expected_revision=expected, actor=actor, reason=reason
        )
        return self.deliver(promoted, status)
