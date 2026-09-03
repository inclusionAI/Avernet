"""Persistence contract for exact published Skill versions."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Event, Lock, Thread

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import BotSkillInstallation, Skill
from agentclaw.community.core.models.space_skill import SkillSpaceBinding, SkillVersion
from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.core.repository.capability_desired_state_types import (
    InstallationFlushPlan,
)
from agentclaw.community.core.repository.implementations.skill_center.skill import (
    SkillRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_version import (
    SkillVersionRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_version_lock import (
    lock_skill_then_exact_version,
    lock_skill_then_latest_published_version,
)
from agentclaw.community.core.skill_center.services.bot_capability_state_reader import (
    BotCapabilityStateReader,
)
from agentclaw.community.core.skill_center.services.skill_version_resolver import (
    SkillVersionResolver,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


class _Database:
    def __init__(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self._factory = sessionmaker(bind=self.engine)

    @contextmanager
    def orm_session(self):
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class _Flush:
    def flush_installations(self, **_kwargs) -> InstallationFlushPlan:
        return InstallationFlushPlan(
            member_skill_ids=frozenset(),
            skills_to_install=frozenset(),
            skills_to_uninstall=frozenset(),
        )


class _Bots:
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        return {
            "bot_id": bot_id,
            "owner_id": owner_id,
            "entity_id": owner_id,
            "env": "pre",
            "active_engine": "openclaw",
        }


def _add_version(
    db: _Database,
    *,
    tenant: str = "teamclaw",
    skill_id: int,
    version_id: int,
    ordinal: int,
    status: str,
    number: str,
    publication_attempt_id: int | None = None,
    name: str | None = None,
) -> None:
    with avernet_tenant_scope(tenant), db.orm_session() as session:
        session.add(
            SkillVersion(
                id=version_id,
                skill_id=skill_id,
                publication_attempt_id=publication_attempt_id,
                version_ordinal=ordinal,
                status=status,
                sc_version_number=number,
                sc_skill_id=1000 + skill_id,
                sc_version_id=2000 + version_id,
                name=name or f"skill-{skill_id}",
                description=None,
                metadata_json='{"mcp_dependencies": []}',
                published_at=(
                    datetime(2026, 8, 30, tzinfo=UTC) if status == "PUBLISHED" else None
                ),
                created_by="owner",
                env="pre",
            )
        )


def test_latest_query_ignores_materializing_and_is_tenant_scoped() -> None:
    db = _Database()
    repo = SkillVersionRepository(db)
    _add_version(
        db,
        skill_id=10,
        version_id=101,
        ordinal=1,
        status="PUBLISHED",
        number="1.0.0",
    )
    _add_version(
        db,
        skill_id=10,
        version_id=102,
        ordinal=2,
        status="MATERIALIZING",
        number="2.0.0",
    )
    _add_version(
        db,
        skill_id=20,
        version_id=201,
        ordinal=1,
        status="PUBLISHED",
        number="1.0.0",
    )
    _add_version(
        db,
        tenant="external",
        skill_id=10,
        version_id=103,
        ordinal=3,
        status="PUBLISHED",
        number="3.0.0",
    )

    with avernet_tenant_scope("teamclaw"):
        rows = repo.list_latest_published(env="pre", skill_ids=(10, 20))

    assert [(row["skill_id"], row["id"]) for row in rows] == [
        (10, 101),
        (20, 201),
    ]


def test_exact_query_requires_skill_env_status_and_tenant() -> None:
    db = _Database()
    repo = SkillVersionRepository(db)
    _add_version(
        db,
        skill_id=10,
        version_id=101,
        ordinal=1,
        status="PUBLISHED",
        number="1.0.0",
    )
    _add_version(
        db,
        skill_id=10,
        version_id=102,
        ordinal=2,
        status="MATERIALIZING",
        number="2.0.0",
    )

    with avernet_tenant_scope("teamclaw"):
        assert (
            repo.get_exact_published(env="pre", skill_id=10, skill_version_id=101)["id"]
            == 101
        )
        assert (
            repo.get_exact_published(env="pre", skill_id=10, skill_version_id=102)
            is None
        )
        assert (
            repo.get_exact_published(env="prod", skill_id=10, skill_version_id=101)
            is None
        )
        assert (
            repo.get_exact_published(env="pre", skill_id=20, skill_version_id=101)
            is None
        )


def test_public_market_version_can_exist_without_publication_attempt() -> None:
    db = _Database()
    _add_version(
        db,
        skill_id=10,
        version_id=101,
        ordinal=1,
        status="PUBLISHED",
        number="1.0.0",
    )

    with db.orm_session() as session:
        stored = session.get(SkillVersion, 101)
        assert stored is not None
        assert stored.publication_attempt_id is None


def test_reader_returns_runtime_ready_center_asset_from_installation_and_version() -> (
    None
):
    db = _Database()
    with db.orm_session() as session:
        session.add(
            Skill(
                id=10,
                name="stable-runtime-name",
                description="asset description",
                git_path="center://public-skill-code",
                skill_uuid="00000000-0000-4000-8000-000000000010",
                user_id="owner",
                bolt_id="default",
                env="pre",
            )
        )
        session.add(
            BotSkillInstallation(
                bot_id="default", owner_id="owner", skill_id=10, env="pre"
            )
        )
    _add_version(
        db,
        skill_id=10,
        version_id=101,
        ordinal=1,
        status="PUBLISHED",
        number="1.0.0",
    )
    reader = BotCapabilityStateReader(
        repository=_Flush(),
        bot_repo=_Bots(),
        pool_skills=SkillRepository(db),
        version_resolver=SkillVersionResolver(SkillVersionRepository(db)),
    )

    assets = reader.active_skill_assets(bot_id="default", owner_id="owner")

    assert len(assets) == 1
    assert assets[0].skill_id == 10
    assert assets[0].name == "stable-runtime-name"
    assert assets[0].skill_uuid == "00000000-0000-4000-8000-000000000010"
    assert assets[0].sc_version_number == "1.0.0"
    assert assets[0].mcp_dependencies == ()


def test_materialization_publish_is_one_tenant_scoped_compare_and_set() -> None:
    db = _Database()
    with db.orm_session() as session:
        session.add(
            Skill(
                id=10,
                name="weather",
                description="old description",
                status="PENDING",
                git_path="center://public-weather",
                skill_uuid="00000000-0000-4000-8000-000000000010",
                user_id="owner",
                bolt_id="default",
                env="pre",
            )
        )
    _add_version(
        db,
        skill_id=10,
        version_id=101,
        ordinal=1,
        status="MATERIALIZING",
        number="1.0.0",
    )
    repo = SkillVersionRepository(db)

    with avernet_tenant_scope("teamclaw"):
        target = repo.get_materialization_target(
            env="pre", skill_id=10, skill_version_id=101
        )
        published = repo.publish_materialized(
            env="pre",
            skill_id=10,
            skill_version_id=101,
            name="skill-10",
            metadata_json='{"mcp_dependencies":[]}',
            description="new description",
        )

    assert target is not None
    assert target.status == "MATERIALIZING"
    assert target.skill_uuid == "00000000-0000-4000-8000-000000000010"
    assert target.skill_code == "public-weather"
    assert published.status == "PUBLISHED"
    assert published.published_at is not None
    with db.orm_session() as session:
        version = session.get(SkillVersion, 101)
        skill = session.get(Skill, 10)
        assert version is not None and version.status == "PUBLISHED"
        assert version.metadata_json == '{"mcp_dependencies":[]}'
        assert version.sc_sha256 is None
        assert skill is not None and skill.description == "new description"
        assert skill.status == "PUBLISHED"


def test_public_first_publish_freezes_manifest_name_before_consumption() -> None:
    db = _Database()
    with db.orm_session() as session:
        session.add(
            Skill(
                id=10,
                name="Dima-cli-skill",
                git_path="center://dima-official-skill",
                skill_uuid="00000000-0000-4000-8000-000000000010",
                user_id=None,
                bolt_id="default",
                env="pre",
            )
        )
    _add_version(
        db,
        skill_id=10,
        version_id=101,
        ordinal=1,
        status="MATERIALIZING",
        number="1.0.0",
    )

    with avernet_tenant_scope("teamclaw"):
        published = SkillVersionRepository(db).publish_materialized(
            env="pre",
            skill_id=10,
            skill_version_id=101,
                name="dima",
                metadata_json='{"mcp_dependencies":[]}',
                description="Dima CLI",
            )

    assert published.name == "dima"
    with db.orm_session() as session:
        skill = session.get(Skill, 10)
        version = session.get(SkillVersion, 101)
        assert skill is not None and skill.name == "dima"
        assert version is not None and version.name == "dima"
        assert version.status == "PUBLISHED"


def test_public_name_convergence_fails_closed_after_installation_exists() -> None:
    db = _Database()
    with db.orm_session() as session:
        session.add(
            Skill(
                id=10,
                name="Dima-cli-skill",
                git_path="center://dima-official-skill",
                skill_uuid="00000000-0000-4000-8000-000000000010",
                user_id=None,
                bolt_id="default",
                env="pre",
            )
        )
        session.add(
            BotSkillInstallation(
                bot_id="bot-a", owner_id="owner", skill_id=10, env="pre"
            )
        )
    _add_version(
        db,
        skill_id=10,
        version_id=101,
        ordinal=1,
        status="MATERIALIZING",
        number="1.0.0",
    )

    with (
        avernet_tenant_scope("teamclaw"),
        pytest.raises(RuntimeError, match="name changed"),
    ):
        SkillVersionRepository(db).publish_materialized(
            env="pre",
            skill_id=10,
            skill_version_id=101,
                name="dima",
                metadata_json='{"mcp_dependencies":[]}',
                description="Dima CLI",
            )

    with db.orm_session() as session:
        skill = session.get(Skill, 10)
        version = session.get(SkillVersion, 101)
        assert skill is not None and skill.name == "Dima-cli-skill"
        assert version is not None and version.name == "skill-10"
        assert version.status == "MATERIALIZING"


def test_materialization_publish_replay_requires_the_same_frozen_facts() -> None:
    db = _Database()
    with db.orm_session() as session:
        session.add(
            Skill(
                id=10,
                name="weather",
                git_path="center://public-weather",
                skill_uuid="00000000-0000-4000-8000-000000000010",
                user_id="owner",
                bolt_id="default",
                env="pre",
            )
        )
    _add_version(
        db,
        skill_id=10,
        version_id=101,
        ordinal=1,
        status="PUBLISHED",
        number="1.0.0",
    )
    with db.orm_session() as session:
        row = session.get(SkillVersion, 101)
        assert row is not None
        row.description = "same"
        row.metadata_json = '{"mcp_dependencies":[]}'
        row.published_at = datetime(2026, 8, 30, 12, 0)

    repo = SkillVersionRepository(db)
    with avernet_tenant_scope("teamclaw"):
        replay = repo.publish_materialized(
            env="pre",
            skill_id=10,
            skill_version_id=101,
            name="skill-10",
            metadata_json='{"mcp_dependencies":[]}',
            description="same",
        )
        with pytest.raises(RuntimeError, match="conflicts"):
            repo.publish_materialized(
                env="pre",
                skill_id=10,
                skill_version_id=101,
                name="skill-10",
                metadata_json='{"mcp_dependencies":[{"code":"other"}]}',
                description="same",
            )

    assert replay.status == "PUBLISHED"


def test_space_publication_never_reactivates_terminally_offline_skill() -> None:
    db = _Database()
    offline_at = datetime(2026, 8, 30, 10, 0)
    with db.orm_session() as session:
        space = SpaceModel(
            space_code="space-publish",
            space_type="TEAM",
            name="Space",
            created_by="owner",
            updated_by="owner",
            env="pre",
        )
        skill = Skill(
            id=10,
            name="weather",
            git_path="center://space-weather",
            skill_uuid="00000000-0000-4000-8000-000000000010",
            offline_at=offline_at,
            offline_by="owner",
            env="pre",
        )
        session.add_all((space, skill))
        session.flush()
        session.add(
            SkillSpaceBinding(
                skill_id=10,
                space_id=space.id,
                created_by="owner",
                env="pre",
            )
        )
    _add_version(
        db,
        skill_id=10,
        version_id=101,
        ordinal=2,
        status="MATERIALIZING",
        number="2.0.0",
        publication_attempt_id=501,
        name="weather",
    )
    repo = SkillVersionRepository(db)

    with avernet_tenant_scope("teamclaw"):
        repo.publish_materialized(
            env="pre",
            skill_id=10,
            skill_version_id=101,
            name="weather",
            metadata_json='{"mcp_dependencies":[]}',
            description="online again",
        )
    with db.orm_session() as session:
        skill = session.get(Skill, 10)
        assert skill is not None
        assert skill.offline_at == offline_at
        assert skill.offline_by == "owner"

    with avernet_tenant_scope("teamclaw"):
        repo.publish_materialized(
            env="pre",
            skill_id=10,
            skill_version_id=101,
            name="weather",
            metadata_json='{"mcp_dependencies":[]}',
            description="online again",
        )
    with db.orm_session() as session:
        skill = session.get(Skill, 10)
        assert skill is not None
        assert skill.offline_at == offline_at
        assert skill.offline_by == "owner"


def test_sc_public_materialization_never_clears_offline_without_space_binding() -> None:
    db = _Database()
    offline_at = datetime(2026, 8, 30, 10, 0)
    with db.orm_session() as session:
        session.add(
            Skill(
                id=10,
                name="weather",
                git_path="center://public-weather",
                skill_uuid="00000000-0000-4000-8000-000000000010",
                offline_at=offline_at,
                offline_by="owner",
                env="pre",
            )
        )
    _add_version(
        db,
        skill_id=10,
        version_id=101,
        ordinal=2,
        status="MATERIALIZING",
        number="2.0.0",
        name="weather",
    )

    with avernet_tenant_scope("teamclaw"):
        SkillVersionRepository(db).publish_materialized(
            env="pre",
            skill_id=10,
            skill_version_id=101,
            name="weather",
            metadata_json='{"mcp_dependencies":[]}',
            description="updated",
        )

    with db.orm_session() as session:
        skill = session.get(Skill, 10)
        assert skill is not None
        assert skill.offline_at == offline_at
        assert skill.offline_by == "owner"


def test_space_binding_without_publication_attempt_never_clears_offline() -> None:
    db = _Database()
    offline_at = datetime(2026, 8, 30, 10, 0)
    with db.orm_session() as session:
        space = SpaceModel(
            space_code="space-null-attempt",
            space_type="TEAM",
            name="Space",
            created_by="owner",
            updated_by="owner",
            env="pre",
        )
        skill = Skill(
            id=10,
            name="weather",
            git_path="center://space-weather",
            skill_uuid="00000000-0000-4000-8000-000000000010",
            offline_at=offline_at,
            offline_by="owner",
            env="pre",
        )
        session.add_all((space, skill))
        session.flush()
        session.add(
            SkillSpaceBinding(
                skill_id=10,
                space_id=space.id,
                created_by="owner",
                env="pre",
            )
        )
    _add_version(
        db,
        skill_id=10,
        version_id=101,
        ordinal=2,
        status="MATERIALIZING",
        number="2.0.0",
        publication_attempt_id=None,
        name="weather",
    )

    with avernet_tenant_scope("teamclaw"):
        SkillVersionRepository(db).publish_materialized(
            env="pre",
            skill_id=10,
            skill_version_id=101,
            name="weather",
            metadata_json='{"mcp_dependencies":[]}',
            description="updated",
        )

    with db.orm_session() as session:
        skill = session.get(Skill, 10)
        assert skill is not None
        assert skill.offline_at == offline_at
        assert skill.offline_by == "owner"


class _LockCoordinator:
    """Model row locks used to expose overlapping lock acquisition intent."""

    def __init__(self) -> None:
        self.locks = {Skill: Lock(), SkillVersion: Lock()}
        self.publisher_has_skill = Event()
        self.offline_waits_for_skill = Event()
        self.orders: dict[str, list[type]] = {"publish": [], "offline": []}


class _LockQuery:
    def __init__(self, session: "_LockSession", model: type) -> None:
        self._session = session
        self._model = model

    def filter(self, *_criteria):
        return self

    def order_by(self, *_criteria):
        return self

    def with_for_update(self):
        return self

    def one_or_none(self):
        return self._lock()

    def first(self):
        return self._lock()

    def _lock(self):
        coordinator = self._session.coordinator
        if self._session.name == "offline" and self._model is Skill:
            coordinator.offline_waits_for_skill.set()
        lock = coordinator.locks[self._model]
        lock.acquire()
        self._session.held.append(lock)
        coordinator.orders[self._session.name].append(self._model)
        if self._session.name == "publish" and self._model is Skill:
            coordinator.publisher_has_skill.set()
            assert coordinator.offline_waits_for_skill.wait(timeout=2)
        return Skill(id=10, env="pre") if self._model is Skill else SkillVersion(id=101)


class _LockSession:
    def __init__(self, coordinator: _LockCoordinator, name: str) -> None:
        self.coordinator = coordinator
        self.name = name
        self.held: list[Lock] = []

    def query(self, model: type) -> _LockQuery:
        return _LockQuery(self, model)

    def release(self) -> None:
        for lock in reversed(self.held):
            lock.release()
        self.held.clear()


def test_materialization_and_offline_overlap_use_skill_then_version_lock_order() -> (
    None
):
    coordinator = _LockCoordinator()
    failures: list[BaseException] = []

    def _publish() -> None:
        session = _LockSession(coordinator, "publish")
        try:
            lock_skill_then_exact_version(
                session,
                env="pre",
                skill_id=10,
                skill_version_id=101,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)
        finally:
            session.release()

    def _offline() -> None:
        session = _LockSession(coordinator, "offline")
        try:
            lock_skill_then_latest_published_version(
                session,
                env="pre",
                skill_id=10,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)
        finally:
            session.release()

    publisher = Thread(target=_publish)
    publisher.start()
    assert coordinator.publisher_has_skill.wait(timeout=2)
    offliner = Thread(target=_offline)
    offliner.start()
    publisher.join(timeout=2)
    offliner.join(timeout=2)

    assert failures == []
    assert not publisher.is_alive() and not offliner.is_alive()
    assert coordinator.orders == {
        "publish": [Skill, SkillVersion],
        "offline": [Skill, SkillVersion],
    }
