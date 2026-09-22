"""Product-shaped contract for combined Skill and MCP full replacement."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.bot_config_manifest.apply.activation_delegates import (
    DeviceActivation,
)
from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.apply.delivery import ArcaDelivery
from agentclaw.community.core.bot_config_manifest.apply.materialisers.mcp import (
    McpMaterialiser,
)
from agentclaw.community.core.bot_config_manifest.apply.materialisers.skills import (
    SkillsMaterialiser,
)
from agentclaw.community.core.bot_config_manifest.apply.orchestrator import (
    ApplyOrchestrator,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyStatus,
    EntryOutcome,
)
from agentclaw.community.core.bot_config_manifest.apply.source_resolver import (
    DeclaredSourceResolver,
)
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
    resolve_capabilities,
)
from agentclaw.community.core.bot_config_manifest.schema.validator import (
    validate_document,
)
from agentclaw.community.core.models.mcp import (
    BotMCPConfig,
    BotMCPInstallation,
    SkillSetMCPServer,
)
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill import (
    SkillRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_version import (
    SkillVersionRepository,
)
from agentclaw.community.core.skill_center.policies.platform_default_mcp import (
    PlatformDefaultMcpPolicy,
)
from agentclaw.community.core.skill_center.services.bot_capability_state_reader import (
    BotCapabilityStateReader,
)
from agentclaw.community.core.skill_center.services.direct_activation_service import (
    DirectActivationService,
)
from agentclaw.community.core.skill_center.services.skill_version_resolver import (
    SkillVersionResolver,
)

from ._fakes import (
    FakeManifestContent,
    FakeObjectCredentials,
    OSS_AUTH,
    OSS_BUCKET,
    build_skill_zip,
    declared_session,
    real_validator,
    seeded_object_store,
)


class _Database:
    def __init__(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def orm_session(self):
        with self.transactional_orm_session() as session:
            yield session

    @contextmanager
    def transactional_orm_session(self):
        session = self.sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class _Authorization:
    def can_manage_bot(self, **_kwargs) -> bool:
        return True


class _Audit:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert(self, row: dict) -> None:
        self.rows.append(row)


class _Center:
    def check_mcp_permission_detail(self, _actor_id: str, _server_code: str) -> dict:
        return {"has_permission": True, "access_level": "PUBLIC"}


class _McpConfig:
    def validate_bot_override(self, **_kwargs) -> dict:
        return {"valid": True, "error": None}


class _Runtime:
    def __init__(self) -> None:
        self.scopes: list[object] = []

    async def snapshot_skill_mappings(self, **_kwargs):
        return ()

    async def project(self, *, scope, **_kwargs) -> None:
        self.scopes.append(scope)

    def resolve_plan(self, *, bot_id: str, owner_id: str, **_kwargs):
        return SimpleNamespace(
            bot_id=bot_id,
            owner_id=owner_id,
            projection=SimpleNamespace(skill_mappings=()),
        )

    async def apply_plan(self, *, scope, **_kwargs):
        self.scopes.append(scope)
        return None


class _PersistedSkillPackages:
    """Complete package writes backed by the real Skill repository."""

    def __init__(self, skills: SkillRepository) -> None:
        self._skills = skills
        self._validator = real_validator()
        self.packages: dict[str, bytes] = {}

    async def upload_local_skill(
        self, *, bot_id: str, owner_id: str, actor_id: str, package: bytes
    ) -> dict:
        validated = self._validator.validate_zip(package)
        existing = next(
            (
                asset
                for asset in self._skills.list_bot_local_assets(
                    env="dev", owner_id=owner_id, bot_id=bot_id
                )
                if asset.name == validated.name
            ),
            None,
        )
        if existing is None:
            row = self._skills.create(
                {
                    "name": validated.name,
                    "description": validated.description,
                    "git_path": f"local://{validated.name}",
                    "is_public": False,
                    "user_id": owner_id,
                    "bolt_id": bot_id,
                    "status": "PUBLISHED",
                    "source_type": "upload",
                }
            )
            operation = "created"
        else:
            row = self._skills.update(
                str(existing.skill_id),
                {"description": validated.description},
            )
            operation = "replaced"
        assert row is not None
        self.packages[validated.name] = validated.canonical_zip
        return {
            "operation": operation,
            "skill": {**row, "active": False},
            "actor_id": actor_id,
        }

    async def delete_local_skill(
        self, *, skill_id: str, name: str, bot_id: str, owner_id: str,
        actor_id: str,
    ) -> None:
        assert self._skills.delete_bot_local_skill(
            skill_id=skill_id, owner_id=owner_id, bot_id=bot_id
        )
        self.packages.pop(name, None)


class _PersistedSkillQuery:
    """Narrow query adapter over the same real Skill repository."""

    def __init__(self, skills: SkillRepository) -> None:
        self._skills = skills

    def resolve_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, user_id: str
    ) -> dict:
        row = self._skills.get_by_id(skill_id)
        assert row is not None
        assert str(row["bolt_id"]) == bot_id
        assert str(row["user_id"]) == owner_id == user_id
        return row


def _seed(db: _Database, bots: BotRepository, skills: SkillRepository) -> int:
    bots.insert(
        {
            "bot_id": "bot-1",
            "bot_name": "Manifest contract",
            "owner_id": "owner",
            "owner_name": "Owner",
            "entity_id": "entity-1",
            "entity_type": "staff",
            "creator_id": "owner",
            "status": "ACTIVE",
            "active_engine": "moltis",
            "bot_type": "personal",
        }
    )
    dependent = skills.create(
        {
            "name": "dependent",
            "description": "old package",
            "git_path": "local://dependent",
            "is_public": False,
            "user_id": "owner",
            "bolt_id": "bot-1",
            "status": "PUBLISHED",
            "mcp_dependencies": [{"code": "mcp.dep"}],
        }
    )
    skill_id = int(dependent["id"])
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="ordinary",
            user_id="owner",
            bolt_id="bot-1",
            engine_type="moltis",
            is_active=True,
            env="dev",
        )
        session.add(skill_set)
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=skill_set.id, skill_id=skill_id, env="dev"
                ),
                SkillSetMCPServer(
                    skill_set_id=skill_set.id,
                    server_code="mcp.explicit",
                    name="explicit",
                    env="dev",
                ),
                SkillSetMCPServer(
                    skill_set_id=skill_set.id,
                    server_code="mcp.omit",
                    name="omit",
                    env="dev",
                ),
                BotSkillInstallation(
                    bot_id="bot-1", owner_id="owner", skill_id=skill_id, env="dev"
                ),
                BotMCPInstallation(
                    bot_id="bot-1", owner_id="owner",
                    server_code="mcp.explicit", env="dev",
                ),
                BotMCPInstallation(
                    bot_id="bot-1", owner_id="owner",
                    server_code="mcp.omit", env="dev",
                ),
                BotMCPConfig(
                    bot_id="bot-1", owner_id="owner", server_code="mcp.dep",
                    config='{"headers":{"X-Stale":"value"}}', env="dev",
                ),
            ]
        )
    return skill_id


def _context(bot: dict, *, apply_id: str | None) -> ApplyContext:
    capabilities = resolve_capabilities(
        active_engine="moltis",
        bot_type="personal",
        is_teclaw=lambda _engine: False,
    )
    return ApplyContext(
        bot_id="bot-1",
        owner_id="owner",
        actor_id="owner",
        entity_id="entity-1",
        env="dev",
        tenant="default",
        engine_type="moltis",
        bot_type="personal",
        bot=bot,
        capabilities=capabilities,
        apply_id=apply_id,
        source_session=declared_session(),
    )


@pytest.mark.asyncio
async def test_combined_full_replace_dry_run_and_apply_use_real_persistence():
    db = _Database()
    bots = BotRepository(db)
    skills = SkillRepository(db)
    desired = CapabilityDesiredStateRepository(db)
    original_skill_id = _seed(db, bots, skills)
    versions = SkillVersionResolver(SkillVersionRepository(db))
    reader = BotCapabilityStateReader(desired, bots, skills, versions)
    runtime = _Runtime()
    direct = DirectActivationService(
        desired,
        bots,
        _PersistedSkillQuery(skills),
        runtime,
        _Authorization(),
        _Audit(),
        _Center(),
        reader,
        PlatformDefaultMcpPolicy(lambda _bot_id: {}),
        MagicMock(),
    )
    activation = DeviceActivation(direct)
    uploads = _PersistedSkillPackages(skills)
    package = build_skill_zip("dependent")
    key = "skills/dependent.zip"
    objects = seeded_object_store({key: package})
    fetcher = DeclaredSourceResolver(
        FakeManifestContent(), FakeObjectCredentials(), objects
    )
    orchestrator = ApplyOrchestrator(
        {
            ManifestCategory.SKILLS: SkillsMaterialiser(
                uploads, activation, reader, real_validator(), fetcher
            ),
            ManifestCategory.MCP: McpMaterialiser(
                activation, _Center(), _McpConfig(), reader
            ),
        },
        steps=ArcaDelivery(lambda: None).steps_for,
    )
    digest = "sha256:" + hashlib.sha256(package).hexdigest()
    document = f"""schema_version: 1
manifest:
  skills:
    - name: dependent
      source:
        protocol: oss
        bucket: {OSS_BUCKET}
        key: {key}
        auth: {OSS_AUTH}
      digest: {digest}
  mcp:
    - server_code: mcp.explicit
      config:
        headers: {{}}
"""
    capabilities = resolve_capabilities(
        active_engine="moltis",
        bot_type="personal",
        is_teclaw=lambda _engine: False,
    )
    parsed = validate_document(document, capabilities).parsed
    bot = bots.get_by_id_and_owner("bot-1", "owner")
    assert bot is not None

    dry_run = await orchestrator.apply(
        _context(bot, apply_id=None),
        parsed,
        apply_id="",
        trigger="explicit",
        started_at=datetime.now(),
        dry_run=True,
    )

    dry_categories = {category.construct: category for category in dry_run.categories}
    assert dry_run.status is ApplyStatus.SUCCEEDED
    assert dry_categories[ManifestCategory.SKILLS].entries[0].outcome is EntryOutcome.UPDATED
    assert dry_categories[ManifestCategory.MCP].removals == ("mcp.omit",)
    assert runtime.scopes == []
    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 1
        assert session.query(SkillSetMCPServer).count() == 2
        assert {row.server_code for row in session.query(BotMCPInstallation)} == {
            "mcp.explicit",
            "mcp.omit",
        }

    report = await orchestrator.apply(
        _context(bot, apply_id="apply-1"),
        parsed,
        apply_id="apply-1",
        trigger="explicit",
        started_at=datetime.now(),
    )

    categories = {category.construct: category for category in report.categories}
    assert report.status is ApplyStatus.SUCCEEDED
    assert categories[ManifestCategory.SKILLS].entries[0].outcome is EntryOutcome.UPDATED
    assert categories[ManifestCategory.MCP].removals == ("mcp.omit",)
    dependency = next(
        entry
        for entry in categories[ManifestCategory.MCP].entries
        if entry.identity == "mcp.dep"
    )
    assert dependency.outcome is EntryOutcome.UPDATED
    assert "Skill dependency" in (dependency.note or "")
    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 0
        assert session.query(SkillSetMCPServer).count() == 0
        assert {row.skill_id for row in session.query(BotSkillInstallation)} == {
            original_skill_id
        }
        assert {row.server_code for row in session.query(BotMCPInstallation)} == {
            "mcp.explicit"
        }
        overrides = session.query(BotMCPConfig).all()
        assert [row.server_code for row in overrides] == ["mcp.explicit"]
        assert json.loads(overrides[0].config) == {"headers": {}}
    assets = reader.active_skill_assets(bot_id="bot-1", owner_id="owner", bot=bot)
    derived = {
        str(dependency["code"])
        for asset in assets
        for dependency in asset.mcp_dependencies
    }
    assert reader.active_mcp_server_codes(
        bot_id="bot-1", owner_id="owner", bot=bot
    ) | derived == {"mcp.explicit", "mcp.dep"}
    assert uploads.packages.keys() == {"dependent"}
    assert runtime.scopes
