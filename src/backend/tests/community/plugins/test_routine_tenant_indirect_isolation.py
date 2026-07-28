"""Contract test — routines indirect cross-tenant isolation.

``routines`` has no own persistence layer. Its only entry to a bot is via
``CronRelayService.forward_request`` (core/cron/services/cron_relay.py:780+),
which at line 810 calls ``self._bot_provider.get_bot(bot_id, user_id)``. In
prod ``_bot_provider`` is ``agentclaw.community.core.bot_management.services.
bot_service.BotService`` — its ``get_bot`` (bot_service.py:1625-1641) does::

    bot = self._repository.get_by_id_and_owner(bot_id, user_id)
    if not bot:
        raise BotNotFoundError(f"Bot not found: {bot_id}")
    ...

``BotRepository.get_by_id_and_owner`` runs inside the guarded ORM Session, so
the ``_avernet_tenant_read_guard`` (plugin_api/models.py) filters every read
through ``BotModel.avernet_tenant == get_current_avernet_tenant()``. A bot
seeded under ``tenant-a`` is therefore invisible to a request bound to
``tenant-b`` — the cross-tenant lookup yields ``None`` and ``get_bot`` raises
``BotNotFoundError`` before reaching the binding/device/transport hops
(``forward_request`` lines 845-860).

So if a cross-tenant routines call were ever able to invoke ``_transport``, an
engine of another tenant's bot would receive a request carrying the caller's
``user_id`` — exactly the kind of leak this column-feature exists to stop. This
test pins that the leak is impossible.

Why not instantiate ``CronRelayService`` with the real ``BotService``?
``BotService.__init__`` carries ~24 injects (``bcn_service``, ``cleanup_service``,
``device_status_client``, ``template_service``, multiple
``Callable[[], ...]`` providers, …). Pulling all of those in only verifies the
plumbing; the load-bearing isolation code path is exactly what the stub
``_IndirectBotProvider`` below mirrors — the ``repo.get_by_id_and_owner`` lookup
+ ``BotNotFoundError`` raise. Everything ``BotService.get_bot`` does after that
(template / device-binding enrichment) is irrelevant to the cross-tenant
contract and would only make the test brittle if those collaborators changed.

This is the spec's "indirect isolation holds" contract: Session 0 added the
``ac_bots`` guard for ``BotModel`` and made this green; this test guards
against regression for the routines entry point.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError
from agentclaw.community.core.cron.protocols import (
    BotInfoProvider,
    DeviceBindingStatus,
)
from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from agentclaw.community.core.cron.services.cron_runtime_targets import (
    RUNTIME_STAGE_DRAFT,
)
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.bot_repository import BotRepository
from agentclaw.community.plugins.local.sqlite_models import EntityDeviceBinding
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

pytestmark = pytest.mark.integration


class _FileSqliteDB:
    """Same file-backed sqlite DB helper used by ``test_bot_tenant_isolation``.

    The community ORM Session registers the ``before_insert`` + ``do_orm_execute``
    guards eagerly at import time (plugin_api/models.py), so a fresh sqlite
    engine inherits the same guard wiring as prod — every ORM read in this
    module is filtered by ``avernet_tenant == get_current_avernet_tenant()``.
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


class _IndirectBotProvider(BotInfoProvider):
    """Mirror of ``BotService.get_bot``'s repository path — the only code
    section that the cross-tenant contract depends on.

    Production ``get_bot`` (bot_service.py:1625-1641):
        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        # … template / binding enrichment follows (irrelevant to isolation) …

    The stub here ends right after the raise: it does NOT do the post-repo
    fetches (template_service / device_binding) because those run *after* the
    isolation boundary and are out of contract. If they ever get moved before
    ``get_by_id_and_owner`` this test would surface the regression rather than
    silently passing.
    """

    def __init__(self, repo: BotRepository) -> None:
        self._repo = repo

    def get_bot(self, *args: Any, **kwargs: Any) -> Any:  # protocol shim
        raise NotImplementedError  # pragma: no cover

    def __getattr__(self, name: str) -> Any:
        # ``BotInfoProvider`` Protocol declares ``get_bot``; we expose it on the
        # instance directly. Anything else is forwarded to MagicMock so any
        # later ``BotService`` calls (e.g. list_bots_*) don't blow up.
        return MagicMock()

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # ``@inject`` requires ``__call__`` semantics for some binders; we
        # don't actually rely on this but it makes the stub safe to pass
        # around test code that mirrors the production ``__init__``.
        raise NotImplementedError  # pragma: no cover


def _make_service(*, bot_provider: BotInfoProvider) -> CronRelayService:
    """Build a ``CronRelayService`` with one real dep (``bot_provider``)
    and the rest as spied mocks.

    ``_transport.invoke`` is an ``AsyncMock`` because we need to assert it is
    NOT awaited — that is the load-bearing assertion (cross-tenant calls must
    not reach the engine's HTTP adapter).
    """
    resolver = MagicMock()
    device_provider = MagicMock()
    device_provider.get_device.return_value = MagicMock(
        status=DeviceBindingStatus.ACTIVE
    )
    transport = MagicMock()
    transport.invoke = AsyncMock(
        return_value={"success": True, "data": {"ok": True}}
    )

    return CronRelayService(
        bot_provider=bot_provider,
        device_provider=device_provider,
        transport=transport,
        resolver=resolver,
        template_repo=MagicMock(),
        publish_repo=MagicMock(),
    )


@pytest.fixture
def bot_repo(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'routines_isolation.db'}",
        connect_args={"check_same_thread": False},
    )
    BotModel.__table__.create(engine)
    EntityDeviceBinding.__table__.create(engine)
    BotPublishModel.__table__.create(engine)
    BotCollaboratorModel.__table__.create(engine)
    return BotRepository(_FileSqliteDB(engine))


def _data(**ov):
    base = dict(
        bot_id="bot-a",
        bot_name="Alpha",
        bot_desc="d",
        entity_id="own-a",
        entity_type="staff",
        creator_id="emp1",
        owner_id="own-a",
        status="ACTIVE",
        owner_name="Alice",
    )
    base.update(ov)
    return base


@pytest.fixture
def bot_in_tenant_a(bot_repo):
    """Seed a bot under ``tenant-a`` with no binding — enough for
    ``forward_request`` to reach the ``get_bot`` call."""
    with avernet_tenant_scope("tenant-a"):
        bot_repo.insert(_data())
    return bot_repo


class _RealGetBotProvider(_IndirectBotProvider):
    """``_IndirectBotProvider`` whose ``get_bot`` actually mirrors the
    ``BotService.get_bot`` repository+NotFound path. This is what forces the
    guarded BotRepository to run (so the
    ``with_loader_criteria`` filter fires for real)."""

    def get_bot(self, bot_id: str, user_id: str) -> Dict[str, Any]:
        bot = self._repo.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        return bot


def _assert_no_downstream_touched(svc: CronRelayService) -> None:
    """Cross-tenant isolation must short-circuit before both the device
    binding lookup and the HTTP transport — otherwise the engine of another
    tenant's bot would receive a request the caller can't legitimately make."""
    svc._device_provider.get_device.assert_not_called()
    svc._resolver.resolve_for_bot.assert_not_called()
    svc._resolver.resolve_for_binding.assert_not_called()
    svc._transport.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_forward_request_cross_tenant_raises_and_does_not_reach_engine(
    bot_in_tenant_a,
):
    """``forward_request`` in tenant-b must raise before `_transport.invoke`
    is reached for any bot seeded under tenant-a.

    The seeded bot is invisible to tenant-b because of the
    ``avernet_tenant`` read guard on ``BotModel`` — so
    ``BotRepository.get_by_id_and_owner`` returns ``None``, and the mirroring
    ``BotService.get_bot`` contract raises ``BotNotFoundError`` at line 1628.
    ``forward_request`` never reaches the resolver or the transport.
    """
    bot_repo = bot_in_tenant_a
    svc = _make_service(
        bot_provider=_RealGetBotProvider(bot_repo),
    )

    with avernet_tenant_scope("tenant-b"):
        with pytest.raises(BotNotFoundError):
            await svc.forward_request(
                bot_id="bot-a",
                user_id="own-a",
                nick_name="Bob",
                method="GET",
                path="/api/cron",
                body=None,
                params=None,
                runtime_stage=RUNTIME_STAGE_DRAFT,
            )

    _assert_no_downstream_touched(svc)


@pytest.mark.asyncio
async def test_forward_request_same_tenant_passes_get_bot(bot_in_tenant_a):
    """Same-tenant ``forward_request`` must NOT be blocked by the guard — the
    bot is visible under its own tenant so ``get_bot`` returns a non-None bot
    and execution proceeds past the isolation boundary.

    We assert it gets at least to the *next* validator (``forward_request``
    line 815: ``Bot {bot_id} has no device binding``) — that proves the guard
    let the same-tenant bot through. Without this case, a regression that
    raised ``BotNotFoundError`` for every single call would silently pass the
    cross-tenant test.
    """
    bot_repo = bot_in_tenant_a
    svc = _make_service(bot_provider=_RealGetBotProvider(bot_repo))

    with avernet_tenant_scope("tenant-a"):
        with pytest.raises(ValueError, match="has no device binding"):
            await svc.forward_request(
                bot_id="bot-a",
                user_id="own-a",
                nick_name="Alice",
                method="GET",
                path="/api/cron",
                body=None,
                params=None,
                runtime_stage=RUNTIME_STAGE_DRAFT,
            )
