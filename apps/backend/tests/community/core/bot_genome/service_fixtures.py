"""A registry (and later a promotion service) over in-memory SQLite, a tmp content store and fakes."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.content.service import ManifestContentService
from agentclaw.community.core.bot_genome.models import ApplyResult, BotRef, Provenance, ProvenanceKind
from agentclaw.community.core.bot_genome.promotion.service import GenomePromotionService
from agentclaw.community.core.bot_genome.registry import GenomeRegistryService
from agentclaw.community.core.bot_genome.spec_checks import SpecChecks
from agentclaw.community.core.repository.implementations.bot.genome import (
    GenomePatchRepository,
    GenomePromotionRepository,
    GenomeRefRepository,
    GenomeRevisionRepository,
)
from agentclaw.community.core.repository.implementations.bot.manifest_content import ManifestContentRepository
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from tests.community.core.bot_genome.test_capture import FakeReader
from tests.community.core.bot_genome.test_spec_checks import FakeAuth, FakeConfig

BOT = BotRef(owner_id="user_owner_5", bot_id="bot_123", entity_id="ent_5", engine_type="openclaw", bot_type="arca")
OTHER_BOT = BotRef(owner_id="user_owner_9", bot_id="bot_weather", entity_id="ent_9", engine_type="openclaw", bot_type="arca")
OWNER = Provenance(ProvenanceKind.USER, "user_owner_5")
RUN = Provenance(ProvenanceKind.STRATEGY_RUN, "strategy:tune", run_id="run_7f3", step="propose")


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


class FakeApplies:
    """``last_apply`` only: the report id the registry joins to a promotion."""

    def __init__(self) -> None:
        self.reports: dict[tuple[str, str], Any] = {}

    def last_apply(self, *, entity_id: str, bot_id: str):
        return self.reports.get((entity_id, bot_id))


class FakeApplier:
    """Accepts by default; ``refuse`` makes preflight raise it, ``fail`` makes start raise it."""

    kind = "fake"

    def __init__(self) -> None:
        self.refuse: Exception | None = None
        self.on_preflight = None
        self.fail: Exception | None = None
        self.preflights: list = []
        self.starts: list = []
        self.next_reference = "apply_1"

    def preflight(self, bot, compiled) -> None:
        self.preflights.append((bot, compiled))
        if self.on_preflight is not None:
            self.on_preflight()  # something that happens while the service is between its read and its write
        if self.refuse is not None:
            raise self.refuse

    async def start(self, bot, compiled, *, actor, reason) -> ApplyResult:
        self.starts.append((bot, compiled, actor, reason))
        if self.fail is not None:
            raise self.fail
        return ApplyResult(kind=self.kind, reference=self.next_reference, detail="started")


class World:
    """Everything a registry test needs, built once per test."""

    def __init__(self, tmp_path: Path) -> None:
        self.db = InMemorySqliteDB()
        self.revisions = GenomeRevisionRepository(self.db)
        self.refs = GenomeRefRepository(self.db)
        self.patches = GenomePatchRepository(self.db)
        self.promotions = GenomePromotionRepository(self.db)
        self.content = ManifestContentService(ManifestContentRepository(self.db), tmp_path / "store")
        self.reader = FakeReader()
        self.auth, self.config = FakeAuth(), FakeConfig()
        self.applies = FakeApplies()
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
            applies=self.applies,
            env=lambda: "dev",
        )
        self.applier = FakeApplier()
        self.promotion = GenomePromotionService(
            registry=self.registry, revisions=self.revisions, refs=self.refs, promotions=self.promotions, applier=self.applier, env=lambda: "dev"
        )

    def report(self, bot: BotRef, apply_id: str, status: ApplyStatus = ApplyStatus.SUCCEEDED) -> None:
        self.applies.reports[(bot.entity_id, bot.bot_id)] = SimpleNamespace(apply_id=apply_id, status=status)
