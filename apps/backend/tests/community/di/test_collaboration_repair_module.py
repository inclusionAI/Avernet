"""Request-scoped collaboration repair wiring: loop separation and refusal.

The factory is a **composition object**, not a Core dependency: an HTTP
adapter submits only the filtered authentication headers, and every
``open`` builds the delivery plugins on the caller's own loop and closes
them again in ``finally``. Nothing here borrows the background
``ManagerSyncStack`` instances — those are born on the worker loop, and an
async client may not cross one.

The HTTP plugin classes are replaced by spies at the *defining* module so
the real production source still constructs them; each spy records the
loop it was built on, the sends it received, and whether its owned client
was released.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from injector import Injector, Module, singleton

from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.core.repository.protocols.bot.collaboration_repair import (
    CollaborationRepairReadRepository,
)
from agentclaw.community.core.repository.protocols.bot.manager_sync import (
    ManagerSyncReadRepository,
)
from agentclaw.community.core.repository.protocols.bot.ownership_sync import (
    OwnershipSyncReadRepository,
)
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.di import config as cfg
from agentclaw.community.di.bot_manager_sync_config import ManagerSyncConfig
from agentclaw.community.plugins.community.bcs_collaboration_metadata import (
    BcnCollaborationMetadataAdapter,
)
from agentclaw.community.di.config import SecretNamesConfig
from agentclaw.community.di.modules import collaboration_repair_module
from agentclaw.community.di.modules.bot_manager_sync_module import (
    BotManagerSyncModule,
    ManagerSyncStack,
)
from agentclaw.community.di.modules.collaboration_repair_module import (
    CollaborationRepairFactory,
    CollaborationRepairModule,
)
from agentclaw.community.kernel.bot_manager_sync import ManagerSetChange
from agentclaw.community.kernel.bot_ownership_sync import OwnershipChange, OwnershipFacts
from agentclaw.community.kernel.collaboration_repair import (
    RepairCommand,
    RepairPreconditionError,
    RepairPreconditionErrorCode,
)
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.plugin_api.bcs_collaboration_metadata import (
    BcsCollaborationMetadataPlugin,
)
from agentclaw.community.plugin_api.bcs_managers import BcsManagersPlugin
from agentclaw.community.plugin_api.bcs_ownership import BcsOwnershipPlugin
from agentclaw.community.plugins.community import bcs_managers as http_managers_module
from agentclaw.community.plugins.community import bcs_ownership as http_ownership_module
from agentclaw.community.plugins.community.bcs_managers import HttpBcsManagersPlugin
from agentclaw.community.plugins.community.bcs_ownership import HttpBcsOwnershipPlugin
from tests.community.core.bot_management.collaboration_repair.factories import (
    CURRENT_OWNER,
    KEY,
    STABLE,
    default_manager_facts,
    default_repair_facts,
)

_SECRET = "test-token"
_BCS_BASE_URL = "http://127.0.0.1:9999"
_HEADERS = {"Authorization": "Bearer caller-token", "Cookie": "session=1"}
_COMMAND = RepairCommand(KEY, CURRENT_OWNER)


def _running_loop() -> asyncio.AbstractEventLoop | None:
    """The loop this construction happened on, or None when there is none."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


class _OwnershipSpy(HttpBcsOwnershipPlugin):
    """The real HTTP ownership plugin: one scripted confirmation, plus probes."""

    instances: list[_OwnershipSpy] = []

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.sent: list[Any] = []
        self.closed = False
        self.loop = _running_loop()
        # A real owned client, so ``aclose`` has something to release; the
        # scripted send below never touches the wire.
        self._client = httpx.AsyncClient(base_url=self._base_url)
        type(self).instances.append(self)

    async def replace_ownership(
        self, snapshot, *, timeout_seconds: float
    ) -> OwnershipChange:
        self.sent.append(snapshot)
        return OwnershipChange(
            snapshot.bcs_bot_id,
            snapshot.owner_user_id,
            "owner-old",
            True,
            "req-ownership",
        )

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


class _ManagersSpy(HttpBcsManagersPlugin):
    """The real HTTP managers plugin: one scripted confirmation, plus probes."""

    instances: list[_ManagersSpy] = []
    fail_close: bool = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.sent: list[Any] = []
        self.closed = False
        self.loop = _running_loop()
        self._client = httpx.AsyncClient(base_url=self._base_url)
        type(self).instances.append(self)

    async def replace_managers(
        self, snapshot, *, timeout_seconds: float
    ) -> ManagerSetChange:
        self.sent.append(snapshot)
        return ManagerSetChange(
            added=len(snapshot.manager_user_ids),
            removed=0,
            total=len(snapshot.manager_user_ids),
            request_id="req-managers",
        )

    async def aclose(self) -> None:
        self.closed = True
        if type(self).fail_close:
            raise RuntimeError("pool close failed")
        await super().aclose()


class _MetadataSpy(BcnCollaborationMetadataAdapter):
    """The real BCN adapter, with an observable close for the factory loop."""

    instances: list[_MetadataSpy] = []

    def __init__(self, bcn_service, profile_headers) -> None:
        super().__init__(bcn_service, profile_headers)
        self.closed = False
        self.headers = dict(profile_headers)
        type(self).instances.append(self)

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


def _reader(facts: object) -> Mock:
    reader = Mock()
    reader.read_facts.return_value = facts
    return reader


def _bcn_service() -> Mock:
    """A transport double answering every method the metadata adapter sends."""
    bcn = Mock()
    bcn.get_attributes.return_value = {"user_visibility": "public"}
    bcn.onboard_bot.return_value = {"onboarded": True, "bot_uuid": STABLE}
    bcn.sync_bot_caller_config.return_value = {
        "sync_status": "applied",
        "provider_bot_ref": STABLE,
    }
    return bcn


class Settings(Module):
    """Explicit config, secrets, transport and read-fact doubles."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        ownership: bool = True,
        secret: str = _SECRET,
        provider_id: str = "provider",
        provider_token: str = "token",
        registration: bool = True,
        timeout: float = 7.5,
    ) -> None:
        self._config = ManagerSyncConfig(
            enabled=enabled,
            ownership_enabled=ownership,
            env="dev",
            bcs_base_url=_BCS_BASE_URL,
        )
        self._secret = secret
        self._bcn_config = cfg.BcnConfig(
            provider_id=provider_id,
            provider_admin_token=provider_token,
            provider_registration_enabled=registration,
        )
        self._timeout = timeout

    def configure(self, binder) -> None:
        binder.bind(ManagerSyncConfig, to=self._config, scope=singleton)
        binder.bind(SecretNamesConfig, to=SecretNamesConfig(), scope=singleton)
        resolver = Mock()
        resolver.get_secret.return_value = (
            SimpleNamespace(secret_value=self._secret) if self._secret else None
        )
        binder.bind(SecretResolver, to=resolver, scope=singleton)
        binder.bind(cfg.BcnConfig, to=self._bcn_config, scope=singleton)
        bcn = _bcn_service()
        bcn._timeout = self._timeout
        binder.bind(BcnService, to=bcn, scope=singleton)
        binder.bind(
            CollaborationRepairReadRepository,
            to=_reader(default_repair_facts()),
            scope=singleton,
        )
        binder.bind(
            OwnershipSyncReadRepository,
            to=_reader(OwnershipFacts(KEY, "b", "e", CURRENT_OWNER, bot_type="service")),
            scope=singleton,
        )
        binder.bind(
            ManagerSyncReadRepository,
            to=_reader(default_manager_facts()),
            scope=singleton,
        )


def _injector(**kwargs: Any) -> Injector:
    return Injector(
        [CollaborationRepairModule(), BotManagerSyncModule(), Settings(**kwargs)],
        auto_bind=False,
    )


async def _repair_once(factory: CollaborationRepairFactory):
    async with factory.open(_HEADERS) as service:
        return await service.repair(_COMMAND)


def _run_in_own_loop(action) -> dict[str, Any]:
    """Run ``action`` on a brand-new event loop in a throwaway thread."""
    outcome: dict[str, Any] = {}

    def target() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            outcome["loop"] = loop
            outcome["value"] = loop.run_until_complete(action())
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            outcome["error"] = exc
        finally:
            loop.close()

    thread = threading.Thread(target=target)
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive(), "request loop did not finish"
    if "error" in outcome:
        raise outcome["error"]
    return outcome


@pytest.fixture(autouse=True)
def spies(monkeypatch):
    """Point the production source at the spies, on the dev/scope-allowed env."""
    monkeypatch.setattr(http_ownership_module, "HttpBcsOwnershipPlugin", _OwnershipSpy)
    monkeypatch.setattr(http_managers_module, "HttpBcsManagersPlugin", _ManagersSpy)
    monkeypatch.setattr(
        collaboration_repair_module, "BcnCollaborationMetadataAdapter", _MetadataSpy
    )
    monkeypatch.setattr(collaboration_repair_module, "get_current_env", lambda: "dev")
    for registry in (_OwnershipSpy, _ManagersSpy, _MetadataSpy):
        registry.instances.clear()
    _ManagersSpy.fail_close = False
    yield
    for registry in (_OwnershipSpy, _ManagersSpy, _MetadataSpy):
        registry.instances.clear()
    _ManagersSpy.fail_close = False


def test_each_request_loop_gets_its_own_plugins_and_closes_them():
    """Two request loops: fresh plugins each time, all closed, none borrowed."""
    injector = _injector()
    background = injector.get(ManagerSyncStack)
    # The background stack's own instances already exist before any request.
    background_side = {*_OwnershipSpy.instances, *_ManagersSpy.instances}
    assert background_side == {background.ownership, background.bcs}
    factory = injector.get(CollaborationRepairFactory)

    first = _run_in_own_loop(lambda: _repair_once(factory))
    second = _run_in_own_loop(lambda: _repair_once(factory))

    assert first["value"].status == "succeeded"
    assert second["value"].status == "succeeded"
    assert first["loop"] is not second["loop"]

    # One plugin pair per request, every instrumented loop is the request's.
    ownerships = [p for p in _OwnershipSpy.instances if p not in background_side]
    managers = [p for p in _ManagersSpy.instances if p not in background_side]
    assert len(ownerships) == 2
    assert len(managers) == 2
    assert ownerships[0].loop is first["loop"]
    assert ownerships[1].loop is second["loop"]
    assert managers[0].loop is first["loop"]
    assert managers[1].loop is second["loop"]

    # Both request-side plugins closed their owned clients.
    for plugin in (*ownerships, *managers):
        assert plugin.closed is True
        assert plugin._client is None

    # The background stack was never borrowed: distinct instances, untouched,
    # and born outside any request loop.
    assert background.bcs not in {*ownerships, *managers}
    assert background.ownership not in {*ownerships, *managers}
    assert background.bcs.closed is False
    assert background.ownership.closed is False
    assert background.bcs.loop is None
    assert background.ownership.loop is None

    # Each repair went through the existing BCN transport exactly once per item,
    # with this request's filtered auth headers handed to the metadata adapter.
    bcn = injector.get(BcnService)
    assert bcn.get_attributes.call_count == 2
    assert bcn.onboard_bot.call_count == 2
    bcn.sync_bot_caller_config.assert_not_called()
    assert bcn.onboard_bot.call_args.kwargs["request_headers"] == _HEADERS


@pytest.mark.asyncio
async def test_service_inside_open_uses_the_transport_timeout_as_one_budget():
    injector = _injector(timeout=7.5)
    factory = injector.get(CollaborationRepairFactory)
    async with factory.open(_HEADERS) as service:
        assert service._timeout_seconds == injector.get(BcnService)._timeout == 7.5


@pytest.mark.asyncio
async def test_close_failure_is_logged_and_does_not_strand_the_remaining_plugins(
    caplog,
):
    """Chosen contract: a raising close is logged, and the rest still close."""
    injector = _injector()
    factory = injector.get(CollaborationRepairFactory)
    _ManagersSpy.fail_close = True

    with caplog.at_level(logging.WARNING):
        async with factory.open(_HEADERS) as service:
            assert (await service.repair(_COMMAND)).status == "succeeded"

    (managers,) = _ManagersSpy.instances
    (ownership,) = _OwnershipSpy.instances
    (metadata,) = _MetadataSpy.instances

    # The failing plugin was asked to close; the two after it in the loop were
    # still closed, so no client outlives the request.
    assert managers.closed is True
    assert ownership.closed is True
    assert ownership._client is None
    assert metadata.closed is True
    # The adapter kept this request's filtered auth context, never widened it.
    assert metadata.headers == _HEADERS
    assert "plugin close failed" in caplog.text
    assert "RuntimeError" in caplog.text
    # Never the request's credentials, which were handed to the adapter.
    assert "Bearer" not in caplog.text

    if managers._client is not None:  # its own close never ran
        await managers._client.aclose()


@pytest.mark.parametrize(
    ("settings", "reason"),
    [
        ({"enabled": False}, "disabled"),
        ({"ownership": False}, "ownership-disabled"),
        ({"secret": ""}, "principal-signing-key-required"),
        ({"provider_id": "", "provider_token": ""}, "provider-scope-unavailable"),
        ({"registration": False}, "provider-scope-unavailable"),
        ({"timeout": 0.0}, "invalid-budget"),
    ],
)
@pytest.mark.asyncio
async def test_unavailable_deployment_refuses_before_constructing_plugins(
    settings, reason
):
    """Every missing capability is one explicit precondition, never a fallback."""
    injector = _injector(**settings)
    factory = injector.get(CollaborationRepairFactory)

    with pytest.raises(RepairPreconditionError) as excinfo:
        async with factory.open(_HEADERS):
            pytest.fail("an unavailable factory yielded a repair service")

    assert excinfo.value.code == (
        RepairPreconditionErrorCode.COLLABORATION_SYNC_UNAVAILABLE
    )
    assert factory._refusal_reason() == reason
    assert _OwnershipSpy.instances == []
    assert _ManagersSpy.instances == []


@pytest.mark.asyncio
async def test_test_profile_repairs_through_recording_delivery_only():
    """A local profile delivers the repair without credentials or test-token BCN."""
    injector = build_injector(
        profile=DeployProfile.TEST,
        extra_modules=[
            Settings(secret="", provider_id="", provider_token="", registration=False)
        ],
    )
    metadata = injector.get(BcsCollaborationMetadataPlugin)
    metadata.existing.add(STABLE)
    factory = injector.get(CollaborationRepairFactory)

    assert factory._refusal_reason() == ""
    async with factory.open(_HEADERS) as service:
        report = await service.repair(_COMMAND)

    assert report.status == "succeeded"
    assert [name for name, _value in metadata.calls] == ["exists", "profile"]
    assert [call.bcs_bot_id for call in injector.get(BcsOwnershipPlugin).calls] == [
        STABLE
    ]
    assert [call.bcs_bot_id for call in injector.get(BcsManagersPlugin).calls] == [
        STABLE
    ]
    # The factory closed the request's plugins, recordings included.
    assert metadata.closed is True
