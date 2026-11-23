from dataclasses import replace
import pytest
from sqlalchemy.exc import OperationalError

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_publication import (
    ProfilePublisherAllowlist,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.product_profile_publication import (
    ProductProfilePublicationService,
    ProductProfilePublishCommand,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.profile_repository import (
    SqlProductProfileRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.profile_models import (
    ProductProfileModel,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from tests.community.core.task.context_collab.swarm_workflow_support import (
    profile,
    PUBLISHER,
)


def publication(db, principals=(PUBLISHER.actor,)):
    return ProductProfilePublicationService(
        SqlProductProfileRepository(db), ProfilePublisherAllowlist(principals)
    )


def test_sql_profile_survives_reader_reconstruction_and_pins_exact_versions(db):
    first = profile()
    publication(db).publish(ProductProfilePublishCommand(first), PUBLISHER)
    second = profile(version=2)
    publication(db).publish(ProductProfilePublishCommand(second), PUBLISHER)
    reader = SqlProductProfileRepository(db)
    assert reader.get(first.ref.profile_id, 1) == first
    assert reader.get(first.ref.profile_id, 2) == second
    assert (
        first.ref in reader.capabilities().profiles
        and second.ref in reader.capabilities().profiles
    )
    with pytest.raises(ContextCollabValidationError):
        reader.get(first.ref.profile_id, 3)


def test_publication_replay_preserves_first_audit_and_conflict_does_not_overwrite(db):
    definition = profile()
    later = ActorContext(PrincipalRef("system", "another_publisher"), "test")
    service = publication(db, (PUBLISHER.actor, later.actor))
    first = service.publish(ProductProfilePublishCommand(definition), PUBLISHER)
    replay = service.publish(ProductProfilePublishCommand(definition), later)
    assert replay == first and replay.published_by == PUBLISHER.actor
    changed = replace(
        definition, governance=replace(definition.governance, policy="different/1")
    )
    with pytest.raises(ContextCollabVersionConflict):
        service.publish(ProductProfilePublishCommand(changed), PUBLISHER)
    assert service.repository.get(definition.ref.profile_id, 1) == definition


def test_publication_permission_is_separate_deny_by_default(db):
    with pytest.raises(ContextCollabValidationError, match="not authorized"):
        publication(db, ()).publish(ProductProfilePublishCommand(profile()), PUBLISHER)
    with pytest.raises(ContextCollabValidationError, match="unknown published"):
        SqlProductProfileRepository(db).get(profile().ref.profile_id, 1)


def test_same_reader_is_request_scope_safe_and_env_isolated(db, monkeypatch):
    reader = SqlProductProfileRepository(db)
    publication(db).publish(ProductProfilePublishCommand(profile()), PUBLISHER)
    with avernet_tenant_scope("external"):
        with pytest.raises(ContextCollabValidationError):
            reader.get(profile().ref.profile_id, 1)
        alternate = replace(
            profile(), governance=replace(profile().governance, policy="external/1")
        )
        publication(db).publish(ProductProfilePublishCommand(alternate), PUBLISHER)
        assert reader.get(alternate.ref.profile_id, 1) == alternate
    assert reader.get(profile().ref.profile_id, 1) == profile()
    monkeypatch.setenv("SERVER_ENV", "pre")
    assert not reader.capabilities().profiles
    with pytest.raises(ContextCollabValidationError):
        reader.get(profile().ref.profile_id, 1)


def test_case_sensitive_profile_ids_match_sql_and_snapshot_semantics(db):
    upper = replace(profile(), ref=VersionedProfileRef("Workflow", 1))
    lower = replace(profile(), ref=VersionedProfileRef("workflow", 1))
    publication(db).import_profiles((upper, lower), PUBLISHER)
    reader = SqlProductProfileRepository(db)
    assert reader.get("Workflow", 1) == upper
    assert reader.get("workflow", 1) == lower


def test_atomic_seed_rolls_back_earlier_rows_when_later_version_conflicts(db):
    service = publication(db)
    service.publish(ProductProfilePublishCommand(profile()), PUBLISHER)
    newcomer = replace(profile(), ref=VersionedProfileRef("newcomer", 1))
    conflict = replace(
        profile(), governance=replace(profile().governance, policy="different/1")
    )
    with pytest.raises(ContextCollabVersionConflict):
        service.import_profiles((newcomer, conflict), PUBLISHER)
    with pytest.raises(ContextCollabValidationError):
        service.repository.get("newcomer", 1)


def test_corrupt_db_digest_and_db_errors_are_not_static_catalog_fallbacks(db, engine):
    publication(db).publish(ProductProfilePublishCommand(profile()), PUBLISHER)
    with db.orm_session() as session:
        session.query(ProductProfileModel).filter_by(
            profile_id=profile().ref.profile_id
        ).one().definition_digest = "0" * 64
    with pytest.raises(ContextCollabValidationError, match="digest"):
        SqlProductProfileRepository(db).get(profile().ref.profile_id, 1)
    ProductProfileModel.__table__.drop(engine)
    with pytest.raises(OperationalError):
        SqlProductProfileRepository(db).get("task_commission", 1)


def test_explicit_installed_seed_is_repeatable_and_keeps_first_audit(db):
    from agentclaw.community.di.modules.context_collab_products import (
        import_installed_product_profiles,
    )

    service = publication(db)
    first = import_installed_product_profiles(service, PUBLISHER)
    assert import_installed_product_profiles(service, PUBLISHER) == first


def test_seed_verifies_actual_historical_refs_before_commit(db, tmp_path):
    from tests.community.core.task.context_collab.swarm_workflow_support import deploy

    deploy(db, tmp_path)
    with db.orm_session() as session:
        session.query(ProductProfileModel).filter_by(
            profile_id=profile().ref.profile_id
        ).delete()
    service = publication(db)
    with pytest.raises(
        ContextCollabValidationError, match="historical ProductProfile missing"
    ):
        service.import_profiles((profile(version=2),), PUBLISHER)
    with pytest.raises(ContextCollabValidationError):
        service.repository.get(profile().ref.profile_id, 2)
    service.import_profiles((profile(), profile(version=2)), PUBLISHER)
    assert service.repository.get(profile().ref.profile_id, 1) == profile()


def test_actual_sqlite_concurrent_same_version_publication_preserves_first_row(
    tmp_path,
):
    from contextlib import contextmanager
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from agentclaw.community.core.base import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'profile-concurrency.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    class Database:
        @contextmanager
        def transactional_orm_session(self):
            with factory.begin() as session:
                yield session

        orm_session = transactional_orm_session

    service = publication(Database())

    def publish(_):
        return service.publish(ProductProfilePublishCommand(profile()), PUBLISHER)

    with ThreadPoolExecutor(4) as pool:
        records = list(pool.map(publish, range(8)))
    assert all(record == records[0] for record in records)
    with factory() as session:
        assert session.query(ProductProfileModel).count() == 1
    engine.dispose()
