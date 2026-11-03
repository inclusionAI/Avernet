"""Exercise stable addresses across a real persisted service Bot owner change."""

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.bot_management.addressing import BotAddressResolver
from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import (
    CollaboratorLockService,
)
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.repository.implementations.bot.collaborator import (
    CollaboratorRepository,
)
from agentclaw.community.core.repository.implementations.bot.collab_lock import (
    BotCollabLockRepository,
)
from agentclaw.community.core.repository.implementations.chat.expert_chat import (
    ExpertChatRepository,
)
from agentclaw.community.core.repository.implementations.chat.expert_chat_instance import (
    ExpertChatInstanceRepository,
)
from agentclaw.community.core.repository.implementations.governance.whitelist import (
    GovernanceWhitelistRepository,
)
from agentclaw.community.core.repository.implementations.platform.quality import (
    QualityTaskRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill import (
    SkillRepository,
)
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.core.skill_center.services.skill_query_service import (
    SkillQueryService,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    OrmTaskReader,
    upsert_discovered_tasks,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


class Database:
    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        ExpertChatRepository(self)  # Registers the lazily loaded chat tables.
        QualityTaskRepository(self)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def orm_session(self):
        with self.sessions.begin() as session:
            yield session

    transactional_orm_session = orm_session


@pytest.fixture
def persisted(monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "dev")
    db = Database()
    bots = BotRepository(db)
    bot = bots.insert(
        {
            "bot_id": "service",
            "entity_id": "A",
            "entity_type": "staff",
            "owner_id": "A",
            "creator_id": "A",
            "bot_type": "service",
            "active_engine": "moltis",
            "status": "ACTIVE",
        }
    )
    yield db, bots, bot
    db.engine.dispose()


def handoff(db, pk):
    # No transfer product entrypoint is introduced by B4. Change the fixture's
    # responsibility only, leaving every persisted resource in its original place.
    with db.orm_session() as session:
        session.get(BotModel, pk).owner_id = "B"


class DenyCollaborators:
    def check_collaborator_permission(self, bot_id, owner_id, actor, level, **_):
        assert owner_id == "B"
        assert level == PermissionLevel.MEMBER
        return {"has_permission": False}


def test_local_skill_old_entity_and_new_owner_resolve_same_resource(persisted):
    db, bots, bot = persisted
    skills = SkillRepository(db)
    skill = skills.create(
        {
            "name": "local",
            "git_path": "local://local",
            "user_id": "A",
            "bolt_id": "service",
            "is_public": False,
        }
    )
    handoff(db, bot["id"])
    query = SkillQueryService(
        skills,
        bots,
        DenyCollaborators(),
        object(),
        object(),
        object(),
        object(),
        object(),
        object(),
        object(),
        object(),
    )
    for locator in ("A", "B"):
        resolved, row, resource_id = query._resolve(
            skill_id=str(skill["id"]), bot_id="service", owner_id=locator, user_id="B"
        )
        assert resolved["id"] == skill["id"]
        assert row["owner_id"] == "B"
        assert resource_id == "A"
    assert (
        query.get_local_skill(skill_id=str(skill["id"]), actor_id="B")["id"]
        == skill["id"]
    )
    with pytest.raises(LocalSkillNotFoundError):
        query.get_local_skill(skill_id=str(skill["id"]), actor_id="A")


def test_chat_whitelist_quality_and_discovery_keep_original_keys(persisted):
    db, bots, bot = persisted
    chat = ExpertChatRepository(db)
    white = GovernanceWhitelistRepository(db)
    instances = ExpertChatInstanceRepository(db)
    instances.upsert_instance("viewer", "service", "A", "SUCCESS", {"binding_id": 10})
    quality = QualityTaskRepository(db)
    chat.add_chat_bot("viewer", "service", "A")
    chat.save_session("viewer", "service", "A", "session-1")
    chat.add_owned_session("viewer", "service", "A", "session-1")
    white.add(bot_id="service", owner_id="A", created_by="operator")
    record = quality.create(
        uuid="quality-1",
        task_type="eval",
        biz_type="bot",
        bot_id="service",
        owner_id="A",
    )
    handoff(db, bot["id"])
    assert chat.get_session("viewer", "service", "B") == "session-1"
    assert instances.get_instance("viewer", "service", "B")["ext"]["binding_id"] == 10
    assert instances.update_instance("viewer", "service", "B", status="UPGRADING")
    assert instances.get_instance("viewer", "service", "A")["status"] == "UPGRADING"
    assert chat.get_owned_session("viewer", "service", "B", "session-1") is not None
    assert chat.get_owned_session("stranger", "service", "B", "session-1") is None
    assert white.is_whitelisted("service", "B")
    rows, count = quality.list_by_conditions(
        task_type="eval", biz_type="bot", owner_id="B"
    )
    assert count == 1 and rows[0].id == record.id
    new = quality.create(
        uuid="quality-2",
        task_type="eval",
        biz_type="bot",
        bot_id="service",
        owner_id="B",
    )
    assert new.owner_id == "A"
    upsert_discovered_tasks(
        db,
        [
            {
                "task_id": "task-1",
                "bot_id": "service",
                "owner_id": "B",
                "dt": "2026-10-22",
                "title": "Task",
            }
        ],
    )
    tasks = OrmTaskReader(db).read_pending_tasks_for_bot("service", "B", "2026-10-22")
    assert len(tasks) == 1 and tasks[0].owner_id == "A"
    assert chat.remove_chat_bot("viewer", "service", "B")
    assert white.remove(bot_id="service", owner_id="B")


def test_collaborator_pk_and_lock_survive_owner_change(persisted):
    db, bots, bot = persisted
    collaborators = CollaboratorRepository(db)
    member = collaborators.insert(
        {
            "bot_pk": bot["id"],
            "bot_id": "service",
            "owner_id": "A",
            "user_id": "C",
            "user_name": "C",
            "role": "MEMBER",
            "operator_id": "A",
        }
    )
    locks = CollaboratorLockService(
        BotCollabLockRepository(db), object(), object(), BotAddressResolver(bots)
    )
    lock = locks.acquire_lock("service", "A", "C")
    handoff(db, bot["id"])
    assert [r.id for r in collaborators.list_by_bot("service", "B", "dev")] == [
        member.id
    ]
    assert [
        r.id for r in collaborators.list_by_bot_owner_pairs([("service", "B")], "dev")
    ] == [member.id]
    from agentclaw.community.core.bot_collaborator.services.editor_policy import (
        EditorPolicy,
    )

    policy = object.__new__(EditorPolicy)
    policy._collaborator_repo = collaborators
    assert (
        policy.resolve_record(
            bot=bots.get_by_pk(bot["id"]),
            bot_id="service",
            collaborator_id=member.id,
            env="dev",
        ).id
        == member.id
    )
    assert locks.acquire_lock("service", "B", "C").id == lock.id
    assert locks.acquire_lock("service", "B", "other") is None


def test_resource_lookup_rejects_cross_match_and_respects_tenant(persisted):
    db, bots, bot = persisted
    white = GovernanceWhitelistRepository(db)
    white.add(bot_id="service", owner_id="A", created_by="operator")
    handoff(db, bot["id"])
    with avernet_tenant_scope("another"):
        assert not white.is_whitelisted("service", "B")
    bots.insert(
        {
            "bot_id": "service",
            "entity_id": "other",
            "entity_type": "staff",
            "owner_id": "A",
            "creator_id": "A",
            "bot_type": "service",
        }
    )
    with pytest.raises(BotLookupAmbiguousError):
        white.is_whitelisted("service", "A")


def test_openapi_owner_role_comes_from_live_bot_not_legacy_locator(persisted):
    from agentclaw.community.adapters.http.openapi_v1.bot_access import _level

    class NoGrants:
        def get_operable_permission_level(self, **_kwargs):
            return PermissionLevel.NONE

    db, bots, bot = persisted
    handoff(db, bot["id"])
    for locator in ("A", "B"):
        assert (
            _level(bots, NoGrants(), bot_id="service", caller_id="B", owner_id=locator)
            == PermissionLevel.OWNER
        )
        assert (
            _level(bots, NoGrants(), bot_id="service", caller_id="A", owner_id=locator)
            == PermissionLevel.NONE
        )


def test_device_binding_uses_entity_and_detects_unbound_cross_match(persisted):
    from agentclaw.community.core.repository.implementations.devices.device import (
        DeviceRepository,
    )

    db, bots, bot = persisted
    bindings = DeviceRepository(db)
    binding_pk = bindings.insert_binding(
        entity_id="A",
        entity_type="staff",
        device_id="device",
        device_provider="local",
        env="dev",
        device_props={},
        status="ACTIVE",
        apply_reason=None,
        applied_by="A",
    )
    with db.orm_session() as session:
        session.get(BotModel, bot["id"]).binding_id = binding_pk
    handoff(db, bot["id"])
    assert bindings.get_active_by_bot_and_owner("service", "A").id == binding_pk
    assert bindings.get_active_by_bot_and_owner("service", "B").id == binding_pk
    # An unbound second Bot still makes the legacy locator ambiguous.
    bots.insert(
        {
            "bot_id": "service",
            "entity_id": "other",
            "entity_type": "staff",
            "owner_id": "A",
            "creator_id": "A",
            "bot_type": "service",
        }
    )
    with pytest.raises(BotLookupAmbiguousError):
        bindings.get_active_by_bot_and_owner("service", "A")


def test_complete_bot_filter_does_not_expand_normalized_entity_as_person(persisted):
    db, bots, bot = persisted
    quality = QualityTaskRepository(db)
    quality.create(
        uuid="first", task_type="eval", biz_type="bot", bot_id="service", owner_id="A"
    )
    handoff(db, bot["id"])
    bots.insert(
        {
            "bot_id": "service",
            "entity_id": "C",
            "entity_type": "staff",
            "owner_id": "A",
            "creator_id": "A",
            "bot_type": "service",
        }
    )
    quality.create(
        uuid="second", task_type="eval", biz_type="bot", bot_id="service", owner_id="C"
    )
    rows, total = quality.list_by_conditions(
        task_type="eval", biz_type="bot", bot_id="service", owner_id="B"
    )
    assert total == 1 and rows[0].uuid == "first"


def test_owner_list_subquery_cannot_use_other_tenant_or_environment_bots(
    persisted, monkeypatch
):
    db, bots, _bot = persisted
    quality = QualityTaskRepository(db)
    quality.create(
        uuid="detached",
        task_type="eval",
        biz_type="bot",
        bot_id="isolated",
        owner_id="Z",
    )
    with avernet_tenant_scope("another"):
        rows, total = quality.list_by_conditions(
            task_type="eval", biz_type="bot", owner_id="B"
        )
    assert total == 0 and rows == []
    with monkeypatch.context() as scope:
        scope.setenv("SERVER_ENV", "pre")
        bots.insert(
            {
                "bot_id": "isolated",
                "entity_id": "Z",
                "entity_type": "staff",
                "owner_id": "B",
                "creator_id": "B",
                "bot_type": "service",
            }
        )
    rows, total = quality.list_by_conditions(
        task_type="eval", biz_type="bot", owner_id="B"
    )
    assert total == 0 and rows == []


def test_caller_artifact_uses_stable_pk_while_bot_detail_uses_current_owner(persisted):
    from types import SimpleNamespace
    from agentclaw.community.core.expert_chat.services.expert_chat_instance_service import (
        ExpertChatInstanceService,
    )

    db, bots, bot = persisted
    handoff(db, bot["id"])
    artifact = SimpleNamespace(status="success", ext={"migration_path": "/existing/build"})

    class Published:
        def list_by_source_bot(self, source_bot_pk, env):
            # Responsibility changes from A to B; neither legacy locator may
            # switch the stable publication identity or deployment environment.
            assert source_bot_pk == bot["id"]
            assert env == "dev"
            return [SimpleNamespace(status="draft", ext={}), artifact]

    class BotDetails:
        def get_bot(self, *, bot_id, user_id):
            assert user_id == "B"
            return bots.get_by_id_and_owner(bot_id, user_id)

    service = object.__new__(ExpertChatInstanceService)
    service._bot_repo = bots
    service._publish_repo = Published()
    service._bot_service = BotDetails()
    for locator in ("A", "B"):
        assert service._resolve_build_artifact("service", locator) == (
            artifact,
            "/existing/build",
        )
        assert service._load_service_bot("service", locator)["id"] == bot["id"]


@pytest.mark.asyncio
async def test_discovery_person_aggregation_uses_current_owners(persisted):
    from types import SimpleNamespace
    from agentclaw.community.core.task.task_discovery.discovery_service import (
        DiscoveryService,
    )

    db, bots, bot = persisted
    bots.insert(
        {
            "bot_id": "second",
            "entity_id": "A",
            "entity_type": "staff",
            "owner_id": "A",
            "creator_id": "A",
            "bot_type": "service",
        }
    )
    handoff(db, bot["id"])

    class Reader:
        def read_pending_tasks(self):
            return [
                SimpleNamespace(bot_id=bot_id, owner_id="A")
                for bot_id in ("service", "second")
            ]

    class LiveBots:
        def list_bots(self, **_):
            return {"items": [{"bot_id": "service"}, {"bot_id": "second"}]}

    service = DiscoveryService(
        Reader(),
        object(),
        object(),
        bot_service=LiveBots(),
        bot_addresses=BotAddressResolver(bots),
    )
    scheduled = []

    async def discover(**address):
        scheduled.append(address["bot_id"])
        return []

    service.discover = discover
    await service.discover_all_bots()
    assert set(scheduled) == {"service", "second"}


@pytest.mark.asyncio
@pytest.mark.parametrize("locator", ["owner", "B"])
async def test_upload_preserves_entity_key_after_owner_change(persisted, locator):
    from tests.community.core.skill_center import (
        test_local_skill_upload_service as upload,
    )

    db, bots, _bot = persisted
    bot = bots.insert(
        {
            "bot_id": "bot",
            "entity_id": "owner",
            "entity_type": "staff",
            "owner_id": "owner",
            "creator_id": "owner",
            "bot_type": "service",
            "active_engine": "openclaw",
            "status": "ACTIVE",
        }
    )
    handoff(db, bot["id"])
    repo = upload._Repo()
    observer = upload._CapturedSecurityObserver()
    service = upload._service(
        upload._Filesystem(), repo=repo, bot=bots, security_observer=observer
    )
    service._collaborators = DenyCollaborators()
    result = await service.upload_local_skill(
        bot_id="bot",
        owner_id=locator,
        actor_id="B",
        package=upload._zip({"SKILL.md": upload._skill_md()}),
    )
    assert result["operation"] == "created"
    assert repo.created[0]["user_id"] == "owner"
    assert observer.calls[0]["owner_id"] == "owner"
    assert observer.calls[0]["actor_id"] == "B"
    with pytest.raises(LocalSkillNotFoundError):
        await service.upload_local_skill(
            bot_id="bot",
            owner_id=locator,
            actor_id="owner",
            package=upload._zip({"SKILL.md": upload._skill_md()}),
        )
    assert len(repo.created) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("locator", ["owner", "B"])
async def test_delete_preserves_entity_key_after_owner_change(persisted, locator):
    from tests.community.core.skill_center import (
        test_local_skill_delete_service as delete,
    )

    db, bots, _bot = persisted
    bot = bots.insert(
        {
            "bot_id": "bot",
            "entity_id": "owner",
            "entity_type": "staff",
            "owner_id": "owner",
            "creator_id": "owner",
            "bot_type": "service",
            "active_engine": "openclaw",
            "status": "ACTIVE",
        }
    )
    handoff(db, bot["id"])
    service, files, skills, guard = delete._service()
    service._bot_repo = bots
    service._bot_addresses = BotAddressResolver(bots)
    service._collaborators = DenyCollaborators()
    with pytest.raises(LocalSkillNotFoundError):
        await service.delete_local_skill(
            skill_id="9", owner_id=locator, user_id="owner"
        )
    assert not skills.deleted and files.files
    guard.events.clear()
    await service.delete_local_skill(skill_id="9", owner_id=locator, user_id="B")
    assert skills.deleted and files.files == {}
    assert guard.events == [("dev", "owner", "bot"), "release"]


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [False, True])
async def test_missing_bot_discovery_only_allows_empty_noop(persisted, pending):
    from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError
    from agentclaw.community.core.task.task_discovery.discovery_service import (
        DiscoveryService,
    )

    _db, bots, _bot = persisted

    class Reader:
        def read_pending_tasks_for_bot(self, bot_id, owner_id, dt):
            return [object()] if pending else []

    service = DiscoveryService(
        Reader(), object(), object(), bot_addresses=BotAddressResolver(bots)
    )
    if pending:
        with pytest.raises(BotAddressNotFoundError):
            await service.discover(bot_id="missing", owner_id="A", agent_id="missing")
    else:
        assert (
            await service.discover(bot_id="missing", owner_id="A", agent_id="missing")
            == []
        )
