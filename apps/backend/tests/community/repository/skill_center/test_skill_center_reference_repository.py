"""Persistence contract for SC Public Reference batches and items."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from uuid import UUID

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import SkillSpaceBinding, SkillVersion
from agentclaw.community.core.repository.implementations.skill_center.skill_center_reference import (
    SkillCenterReferenceRepository,
)
from agentclaw.community.core.skill_center.reference_contract import (
    ReferenceIdempotencyConflictError,
    SkillCenterReferenceStatus,
)
from agentclaw.community.core.skill_center.public_center_identity import (
    PublicCenterSkillIdentity,
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


def _ensure_public(repo, *, env: str, skill_code: str, **facts):
    identity = PublicCenterSkillIdentity.derive(
        tenant="teamclaw", env=env, skill_code=skill_code
    )
    return repo.ensure_public_version(
        env=env,
        locator=identity.locator,
        skill_uuid=identity.skill_uuid,
        **facts,
    )


def test_create_replays_the_original_batch_and_rejects_key_reuse() -> None:
    repo = SkillCenterReferenceRepository(_Database())

    with avernet_tenant_scope("teamclaw"):
        first = repo.create_or_get_batch(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_set_id="42",
            actor_id="actor-a",
            idempotency_key="reference-key",
            request_hash="hash-a",
            skill_codes=("public-a", "public-b"),
            request_id="request-a",
            reference_ids=("reference-a", "reference-b"),
        )
        replay = repo.create_or_get_batch(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_set_id="42",
            actor_id="actor-a",
            idempotency_key="reference-key",
            request_hash="hash-a",
            skill_codes=("public-a", "public-b"),
            request_id="ignored-request",
            reference_ids=("ignored-a", "ignored-b"),
        )
        case_distinct = repo.create_or_get_batch(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_set_id="42",
            actor_id="actor-a",
            idempotency_key="Reference-Key",
            request_hash="hash-case",
            skill_codes=("public-c",),
            request_id="request-case",
            reference_ids=("reference-case",),
        )

        with pytest.raises(ReferenceIdempotencyConflictError):
            repo.create_or_get_batch(
                env="pre",
                bot_id="bot-a",
                owner_id="owner-a",
                skill_set_id="42",
                actor_id="actor-a",
                idempotency_key="reference-key",
                request_hash="hash-b",
                skill_codes=("public-a",),
                request_id="request-b",
                reference_ids=("reference-c",),
            )

    assert first.created is True
    assert replay.created is False
    assert case_distinct.created is True
    assert replay.batch.request_id == "request-a"
    assert tuple(item.reference_id for item in replay.batch.items) == (
        "reference-a",
        "reference-b",
    )
    assert {item.status for item in replay.batch.items} == {
        SkillCenterReferenceStatus.QUEUED
    }


def test_collection_detail_survive_terminal_transition_without_live_set_lookup() -> None:
    repo = SkillCenterReferenceRepository(_Database())
    with avernet_tenant_scope("teamclaw"):
        created = repo.create_or_get_batch(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_set_id="42",
            actor_id="actor-a",
            idempotency_key="reference-key",
            request_hash="hash-a",
            skill_codes=("public-a", "public-b"),
            request_id="request-a",
            reference_ids=("reference-a", "reference-b"),
        )
        repo.update_item(
            env="pre",
            reference_id="reference-a",
            status=SkillCenterReferenceStatus.COMPLETED,
            sc_version_number="1.0.0",
            skill_version_id=101,
            resolved_skill_id=10,
        )
        # Terminal items are permanent even if a stale task is replayed.
        repo.update_item(
            env="pre",
            reference_id="reference-a",
            status=SkillCenterReferenceStatus.FAILED,
            error_code="STALE_REPLAY",
        )

        total, items = repo.list_items(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_set_id="42",
            request_id=created.batch.request_id,
            status=SkillCenterReferenceStatus.COMPLETED,
            offset=0,
            limit=20,
        )
        detail = repo.get_item(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_set_id="42",
            reference_id="reference-a",
        )

    assert total == 1
    assert [item.reference_id for item in items] == ["reference-a"]
    assert detail is not None
    assert detail.status is SkillCenterReferenceStatus.COMPLETED
    assert detail.skill_id == "10"
    assert detail.error_code is None


def test_public_code_reuses_its_center_locator_without_reusing_same_name_local() -> None:
    db = _Database()
    repo = SkillCenterReferenceRepository(db)
    with avernet_tenant_scope("teamclaw"), db.orm_session() as session:
        session.add(
            Skill(
                id=1,
                name="same-name",
                git_path="local://same-name",
                is_public=False,
                env="pre",
                avernet_tenant="teamclaw",
            )
        )

    with avernet_tenant_scope("teamclaw"):
        first = _ensure_public(
            repo,
            env="pre",
            actor_id="actor",
            skill_code="external-code",
            skill_name="same-name",
            description="public description",
            sc_skill_id=9001,
            sc_version_number="1.0.0",
            sc_version_id=10001,
        )
        replay = _ensure_public(
            repo,
            env="pre",
            actor_id="actor",
            skill_code="external-code",
            skill_name="same-name",
            description="public description",
            sc_skill_id=9001,
            sc_version_number="1.0.0",
            sc_version_id=10001,
        )
        renamed = _ensure_public(
            repo,
            env="pre",
            actor_id="actor",
            skill_code="external-code",
            skill_name="new-display-name",
            description="renamed upstream presentation",
            sc_skill_id=9001,
            sc_version_number="1.0.0",
            sc_version_id=10001,
        )
        case_variant = _ensure_public(
            repo,
            env="pre",
            actor_id="actor",
            skill_code="External-Code",
            skill_name="case-distinct",
            description=None,
            sc_skill_id=9002,
            sc_version_number="1.0.0",
            sc_version_id=10002,
        )

    assert replay == first
    assert renamed == first
    assert case_variant.skill_id != first.skill_id
    assert first.skill_id != 1
    with db.orm_session() as session:
        public = session.get(Skill, first.skill_id)
        version = session.get(SkillVersion, first.skill_version_id)
        assert public is not None
        assert public.git_path == "center://external-code"
        assert UUID(public.skill_uuid).version == 4
        assert version is not None
        assert version.publication_attempt_id is None


def test_group4_ddl_declares_durable_identity_and_collection_indexes() -> None:
    sql_dir = (
        Path(__file__).parents[4]
        / "src"
        / "agentclaw"
        / "community"
        / "core"
        / "skill_center"
        / "sql"
    )
    ddl = (sql_dir / "2026_08_30_phase2_group4_reference.sql").read_text()
    verify = (
        sql_dir / "2026_08_30_phase2_group4_reference_verify.sql"
    ).read_text()

    assert "uk_sc_reference_idempotency" in ddl
    assert "uk_sc_reference_code" in ddl
    assert "idx_sc_reference_collection" in ddl
    assert "idx_skill_center_public_locator" in ddl
    assert ddl.count("COLLATE utf8mb4_bin") == 2
    assert "ac_skill_center_reference_batch" in verify
    assert "ac_skill_center_reference_item" in verify


def test_sync_inventory_excludes_unready_assets_and_space_published_skills() -> None:
    db = _Database()
    repo = SkillCenterReferenceRepository(db)
    with avernet_tenant_scope("teamclaw"):
        public = _ensure_public(
            repo,
            env="pre",
            actor_id="actor",
            skill_code="public-ready",
            skill_name="ready",
            description=None,
            sc_skill_id=9001,
            sc_version_number="1.0.0",
            sc_version_id=10001,
        )
        _ensure_public(
            repo,
            env="pre",
            actor_id="actor",
            skill_code="public-unready",
            skill_name="unready",
            description=None,
            sc_skill_id=9002,
            sc_version_number="1.0.0",
            sc_version_id=10002,
        )
        with db.orm_session() as session:
            session.get(SkillVersion, public.skill_version_id).status = "PUBLISHED"
            space_skill = Skill(
                name="team-owned",
                git_path="center://team-owned",
                is_public=True,
                env="pre",
                skill_uuid="space-owned-uuid",
                avernet_tenant="teamclaw",
            )
            session.add(space_skill)
            session.flush()
            session.add(
                SkillSpaceBinding(
                    skill_id=space_skill.id,
                    space_id=77,
                    created_by="owner",
                    env="pre",
                    avernet_tenant="teamclaw",
                )
            )
            session.add(
                SkillVersion(
                    skill_id=space_skill.id,
                    version_ordinal=1,
                    status="PUBLISHED",
                    sc_version_number="1.0.0",
                    sc_skill_id=9003,
                    sc_version_id=10003,
                    name="team-owned",
                    created_by="owner",
                    env="pre",
                    avernet_tenant="teamclaw",
                )
            )

        assets = repo.list_materialized_public_assets(env="pre")

    assert [(asset.skill_id, asset.skill_code) for asset in assets] == [
        (public.skill_id, "public-ready")
    ]
