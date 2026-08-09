"""Contract test — identity indirect cross-tenant isolation.

``identity`` (core/services/identity.py) has no own persistence layer — unlike
``routines``/``identity`` own modules, no table here is named ``ac_identity_*``.
Its file-I/O only touches the device filesystems; ALL of its bot-level identity
goes through two ``ac_bots``-guard entry points:

  1. ``IdentityService.read_identity_file`` (line 266) calls
     ``resolve_engine_for_bot(bot_id, entity_id, bot_repo=self._bot_repo)``
     → ``bot_repo.get_by_id_and_owner`` and ``bot_repo.get_by_id``
     (engine_resolver.py:53-65). Cross-tenant both return ``None``.
  2. ``IdentityService._identity_device_fs`` (line 214) calls
     ``self._resolver.resolve_for_bot(bot_id, owner_id)``
     → ``binding_repo.get_active_by_bot_and_owner(bot_id, user_id)`` (see
     device_context_resolver.py:78). That query JOINs ``BotModel ⟕
     EntityDeviceBinding``; the ``with_loader_criteria`` on ``BotModel``
     filters the JOIN's bot side by ``avernet_tenant == current tenant``,
     so cross-tenant the JOIN yields no rows → ``binding is None`` →
     ``DeviceContextResolver`` raises ``DeviceNotBoundError`` (line 81).

The cross-tenant path never reaches ``device_fs.read_file``/``write_file``
because step 2 raises before ``DeviceFilesystemDispatcher.dispatch_addressed``
runs. That is the load-bearing assertion of this test: a leak here would mean
the identity file of another tenant's bot could be read from the device's
disk — exactly what the column-feature exists to stop.

This is the spec's "indirect isolation holds" contract for the identity
flow. Session 0 wired the ``ac_bots`` guard for ``BotModel`` and made this
green; this test pins it against regression.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.devices.services.device_context import (
    DeviceNotBoundError,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.core.services.identity import IdentityService
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.repository.implementations.devices.device import DeviceRepository
from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

pytestmark = pytest.mark.integration


class _FileSqliteDB:
    """File-backed sqlite that inherits the ``ac_bots`` ORM guard wiring.

    Mirrors the helper in ``tests/community/plugins/test_bot_tenant_isolation.py``;
    the community ORM Session registers the ``before_insert`` +
    ``do_orm_execute`` guards eagerly at import time (plugin_api/models.py),
    so every ORM read in this module is filtered by
    ``BotModel.avernet_tenant == get_current_avernet_tenant()``.
    """

    def __init__(self, engine):
        self._factory = sessionmaker(
            bind=engine, autocommit=False, autoflush=False
        )

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    session = orm_session


def _seed_bot_and_binding(db, *, bot_id="bot-a", owner_id="own-a",
                          binding_status="ACTIVE"):
    """Seed a real BotModel row plus its EntityDeviceBinding row.

    Tests can then construct `BotRepository` and `DeviceRepository` against
    the same engine and the guarded ORM Session will treat them as tenant-a's.

    The bot's ``binding_id`` is wired to the real auto-incremented ``id`` of
    the binding row (a fresh sqlite sequence) so the JOIN inside
    ``DeviceRepository.get_active_by_bot_and_owner`` resolves in same-tenant.
    """
    with db.orm_session() as session:
        binding = EntityDeviceBinding(
            entity_id=owner_id,
            entity_type="staff",
            device_id="dev-1",
            device_provider="arca",
            status=binding_status,
            applied_by=owner_id,
        )
        session.add(binding)
        session.flush()  # populates binding.id from the autoincrement
        bot = BotModel(
            bot_id=bot_id,
            bot_name="Alpha",
            entity_id=owner_id,
            entity_type="staff",
            creator_id="emp1",
            owner_id=owner_id,
            owner_name="Alice",
            binding_id=binding.id,
            status="ACTIVE",
        )
        session.add(bot)
        session.flush()


@pytest.fixture
def bot_repo(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'identity_isolation.db'}",
        connect_args={"check_same_thread": False},
    )
    # Use Base.metadata.create_all so the full family of tables is present
    # (including resource and any future tables that inherit ``Base``).
    BotModel.__table__.create(engine)
    EntityDeviceBinding.__table__.create(engine)
    BotPublishModel.__table__.create(engine)
    BotCollaboratorModel.__table__.create(engine)
    return BotRepository(_FileSqliteDB(engine))


@pytest.fixture
def device_repo(bot_repo):
    return DeviceRepository(bot_repo._db)


@pytest.fixture
def bot_in_tenant_a(bot_repo):
    """Seed a bot + active binding under tenant-a. Both rows are stamped
    ``avernet_tenant == 'tenant-a'`` by the ``before_insert`` guard."""
    with avernet_tenant_scope("tenant-a"):
        _seed_bot_and_binding(
            bot_repo._db, bot_id="bot-a", owner_id="own-a",
            binding_status="ACTIVE",
        )
    return bot_repo


def _make_real_resolver(device_repo, bot_repo) -> DeviceContextResolver:
    """Real ``DeviceContextResolver`` wrapping the real SQLite repos.

    The per-provider builders are ``MagicMock``s — cross-tenant the resolver
    raises ``DeviceNotBoundError`` before any builder runs, so the mocks are
    only there to satisfy the constructor signature; they never get called
    on the cross-tenant path.
    """
    return DeviceContextResolver(
        binding_repository=device_repo,
        bot_repository=bot_repo,
        arca_builder=MagicMock(),
        baas_builder=MagicMock(),
        teclaw_builder=MagicMock(),
        local_builder=MagicMock(),
    )


def _make_identity_service(*, bot_repo, device_repo) -> IdentityService:
    """Build an ``IdentityService`` whose ``bot_repo``, ``resolver`` are real
    (so the ``avernet_tenant`` guard fires through the actual ORM Session).

    ``path_factory`` + ``publish_repo`` are ``MagicMock`` since the identity
    file-flow never reaches them. ``device_fs_dispatcher`` is the spy — we
    assert that ``dispatch_addressed`` is NOT called on cross-tenant (since
    the resolver must raise first).
    """
    return IdentityService(
        path_factory=MagicMock(),
        publish_repo=MagicMock(),
        bot_repo=bot_repo,
        resolver=_make_real_resolver(device_repo, bot_repo),
        device_fs_dispatcher=MagicMock(),
    )


@pytest.mark.asyncio
async def test_read_identity_file_cross_tenant_raises_before_device_fs(
    bot_repo, device_repo, bot_in_tenant_a,
):
    """``read_identity_file`` under tenant-b on tenant-a's bot must raise
    ``DeviceNotBoundError`` and never reach ``device_fs.read_file``.

    The path proving the leak is impossible:

      tenant-a seeded bot (bot-a/own-a, real binding_id pointing at the
                            EntityDeviceBinding row seeded with status=ACTIVE)
      ↓ tenant-b request
      resolve_engine_for_bot → BotRepository.get_by_id_and_owner
        ↓ ``with_loader_criteria`` filters BotModel.avernet_tenant='tenant-b'
        ↓ bot = None → returns DEFAULT_ENGINE_TYPE
      _identity_device_fs → DeviceContextResolver.resolve_for_bot
        ↓ binding_repo.get_active_by_bot_and_owner JOINs through BotModel
        ↓ JOIN yields None (bot filtered by tenant)
        ↓ raises DeviceNotBoundError ← guard fires here
      ✗ dispatch_addressed / device_fs.read_file never reached
    """
    svc = _make_identity_service(bot_repo=bot_repo, device_repo=device_repo)

    with avernet_tenant_scope("tenant-b"):
        with pytest.raises(DeviceNotBoundError):
            await svc.read_identity_file(
                entity_type="staff",
                entity_id="own-a",
                bot_id="bot-a",
                file_type="AGENTS.md",
                owner_id="own-a",
            )

    assert not svc._device_fs_dispatcher.dispatch_addressed.called
    assert not svc._device_fs_dispatcher.dispatch.called


@pytest.mark.asyncio
async def test_write_identity_file_cross_tenant_raises_before_device_fs(
    bot_repo, device_repo, bot_in_tenant_a,
):
    """``write_identity_file`` under tenant-b on tenant-a's bot must raise
    ``DeviceNotBoundError`` and never reach ``device_fs.write_file``.

    Same indirect-isolation path as ``read_identity_file`` — the write entry
    (identity.py:284) is the same ``resolve_engine_for_bot + _identity_device_fs``
    chain, just with a write instead of a read on the (here-unreachable)
    device_fs.
    """
    svc = _make_identity_service(bot_repo=bot_repo, device_repo=device_repo)

    with avernet_tenant_scope("tenant-b"):
        with pytest.raises(DeviceNotBoundError):
            await svc.write_identity_file(
                entity_type="staff",
                entity_id="own-a",
                bot_id="bot-a",
                file_type="AGENTS.md",
                content="hello",
                owner_id="own-a",
            )

    assert not svc._device_fs_dispatcher.dispatch_addressed.called
    assert not svc._device_fs_dispatcher.dispatch.called


@pytest.mark.asyncio
async def test_read_identity_file_same_tenant_reaches_device_fs(
    bot_repo, device_repo, bot_in_tenant_a,
):
    """Same-tenant ``read_identity_file`` must NOT be blocked by the guard —
    the bot and its binding are visible under their own tenant so the call
    reaches ``dispatch_addressed`` (and returns the mocked file content).

    Without this case, a regression that raised ``DeviceNotBoundError`` for
    every single call would silently pass the cross-tenant test.
    """
    svc = _make_identity_service(bot_repo=bot_repo, device_repo=device_repo)
    # ``read_file`` is async — wire a real DeviceFileSystem mock returning
    # file bytes. ``dispatch_addressed`` returns the device_fs the service
    # then awaits ``read_file`` on.
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=b"IDENTITY CONTENT")
    svc._device_fs_dispatcher.dispatch_addressed.return_value = device_fs

    with avernet_tenant_scope("tenant-a"):
        result = await svc.read_identity_file(
            entity_type="staff",
            entity_id="own-a",
            bot_id="bot-a",
            file_type="AGENTS.md",
            owner_id="own-a",
        )

    assert result == "IDENTITY CONTENT"
    svc._device_fs_dispatcher.dispatch_addressed.assert_called_once()
    device_fs.read_file.assert_awaited_once()
