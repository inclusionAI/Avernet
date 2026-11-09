"""Owner-scoped HTTP contract for the one-shot collaboration repair.

The route is the only entry point: ``POST /openapi/v1/bots/{bot_id}/collaboration/repair``
takes no business body and is reachable by the Bot's current owner alone. The
report is an ``Envelope`` whose per-item statuses are the service's, never
flattened to success, and the authentication headers the delivery plugins need
are forwarded to the factory without ever entering the command or the response.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import InstanceProvider, Injector, Module

from agentclaw.community.adapters.http.openapi_v1.bots.collaboration_repair import (
    router as collaboration_repair_router,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.errors_collaboration_repair import (
    FACTS_STATUS,
    PRECONDITION_STATUS,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.bot_management.collaboration_repair.service_protocol import (
    CollaborationRepairServiceProtocol,
)
from agentclaw.community.di.modules.collaboration_repair_module import (
    CollaborationRepairFactory,
)
from agentclaw.community.kernel.bot_manager_sync import SyncKey
from agentclaw.community.kernel.collaboration_repair import (
    RepairCommand,
    RepairFactsError,
    RepairFactsErrorCode,
    RepairItem,
    RepairItemErrorCode,
    RepairPreconditionError,
    RepairPreconditionErrorCode,
    RepairReport,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    public_router,
    user_scoped_client,
)

_PATH = "/openapi/v1/bots/b-1/collaboration/repair"
_TENANT = "teamclaw"
_ENV = "dev"
_BOT_PK = 11


def test_manager_read_failure_returns_partial_report_after_real_writes():
    from dataclasses import replace

    from agentclaw.community.kernel.bot_manager_sync import SnapshotUnavailable
    from tests.community.core.bot_management.collaboration_repair.factories import (
        default_repair_facts,
        make_repair_case,
    )

    facts = default_repair_facts(
        key=SyncKey(_TENANT, _ENV, _BOT_PK), owner_user_id="u1"
    )
    facts = replace(facts, profile=replace(facts.profile, bot_id="b-1", bcs_bot_id="b-1:e"))
    case = make_repair_case(facts=facts)
    case.managers_reader.read_facts.side_effect = SnapshotUnavailable("read_error")
    client, factory, _, _ = _build()
    factory.service = case.service
    response = client.post(_PATH)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "partial_failed"
    assert [item["status"] for item in data["items"]] == [
        "succeeded", "succeeded", "failed"
    ]
    assert data["items"][2]["error_code"] == "collaboration_facts_read_error"
    case.ownership.replace_ownership.assert_awaited_once()
    case.metadata.update_profile.assert_awaited_once()
    case.managers.replace_managers.assert_not_awaited()


def _report() -> RepairReport:
    """Task 4's report shape: only managers is unknown, everything else passed."""
    return RepairReport(
        bot_id="b-1",
        status="partial_failed",
        items=(
            RepairItem(component="ownership", status="succeeded"),
            RepairItem(component="profile", status="succeeded"),
            RepairItem(
                component="managers",
                status="unknown",
                error_code=RepairItemErrorCode.DELIVERY_TIMEOUT,
            ),
        ),
    )


class _Factory:
    """The request-scoped factory double; records what the route submitted."""

    def __init__(self, service: AsyncMock, *, open_error: Exception | None = None):
        self.service = service
        self.open_error = open_error
        self.profile_headers: list[dict[str, str]] = []
        self.opens = 0

    @asynccontextmanager
    async def open(self, profile_headers):
        self.profile_headers.append(dict(profile_headers))
        if self.open_error is not None:
            raise self.open_error
        self.opens += 1
        yield self.service


class _Bindings(Module):
    def __init__(self, bots, factory) -> None:
        self.bots = bots
        self.factory = factory

    def configure(self, binder):
        binder.bind(BotServiceProtocol, to=InstanceProvider(self.bots))
        binder.bind(CollaborationRepairFactory, to=InstanceProvider(self.factory))


def _build(
    *,
    bot_row: dict | None = None,
    bot_error: Exception | None = None,
    principal: object | None = None,
    caller_user_id: str = "u1",
    open_error: Exception | None = None,
    service: AsyncMock | None = None,
    client_kwargs: dict | None = None,
) -> tuple:
    bots = Mock()
    if bot_error is not None:
        bots.get_bot.side_effect = bot_error
    else:
        bots.get_bot.return_value = bot_row or {
            "id": _BOT_PK,
            "bot_id": "b-1",
            "entity_id": "entity-1",
            "env": _ENV,
        }
    service = service or AsyncMock(spec=CollaborationRepairServiceProtocol)
    service.repair.return_value = _report()
    factory = _Factory(service, open_error=open_error)

    app = FastAPI()
    app.include_router(collaboration_repair_router)
    app.dependency_overrides[require_principal] = lambda: principal or {
        "user_id": caller_user_id
    }
    attach_injector(app, Injector([_Bindings(bots, factory)]))
    mount_public_error_handlers(app)
    return (
        user_scoped_client(app, caller_user_id, **(client_kwargs or {})),
        factory,
        bots,
        service,
    )


def test_owner_receives_the_report_without_flattening_items(caplog):
    client, factory, _, service = _build()

    with caplog.at_level(logging.INFO):
        response = client.post(
            _PATH,
            headers={
                "Authorization": "Bearer secret-token",
                "Cookie": "session=secret-cookie",
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "partial_failed"
    assert [item["component"] for item in data["items"]] == [
        "ownership",
        "profile",
        "managers",
    ]
    assert data["items"][2]["status"] == "unknown"
    assert data["items"][2]["error_code"] == "delivery_timeout"
    rendered = str(response.json()).lower()
    assert "token" not in rendered
    assert "secret-cookie" not in rendered

    # Only the two forwardable authentication headers reach the factory, keyed
    # deterministically in lowercase regardless of how the wire spelled them.
    assert factory.profile_headers == [
        {"authorization": "Bearer secret-token", "cookie": "session=secret-cookie"}
    ]

    command = service.repair.await_args.args[0]
    assert command == RepairCommand(
        key=SyncKey(_TENANT, _ENV, _BOT_PK), actor_user_id="u1"
    )

    # One bounded outcome line, carrying only the finite component vocabulary.
    outcome = next(
        r for r in caplog.records if "[COLLABORATION-REPAIR]" in r.getMessage()
    )
    text = outcome.getMessage()
    assert "bot_id='b-1'" in text
    assert "status=partial_failed" in text
    assert "managers/unknown/delivery_timeout" in text
    assert "secret-token" not in text
    assert "secret-cookie" not in text


def test_command_cannot_be_aimed_by_client_supplied_address_fields():
    client, _, _, service = _build()

    response = client.post(
        _PATH,
        params={
            "tenant": "other-tenant",
            "env": "prod",
            "bot_pk": "999",
            "bcs_bot_id": "b-1:someone-else",
        },
    )

    assert response.status_code == 200
    command = service.repair.await_args.args[0]
    assert command == RepairCommand(
        key=SyncKey(_TENANT, _ENV, _BOT_PK), actor_user_id="u1"
    )


def test_non_owner_is_refused_before_the_factory_is_opened():
    client, factory, bots, service = _build(
        bot_error=BotNotFoundError("Bot not found")
    )

    response = client.post(_PATH)

    assert response.status_code == 404
    assert response.json()["data"] is None
    assert factory.profile_headers == []
    service.repair.assert_not_awaited()


def test_non_owner_with_a_body_is_still_404_not_400():
    """Ownership resolution precedes the body check; the masked answer wins."""
    client, factory, _, service = _build(
        bot_error=BotNotFoundError("Bot not found")
    )

    response = client.post(_PATH, json={"tenant": "other-tenant"})

    assert response.status_code == 404
    assert response.json()["data"] is None
    assert factory.profile_headers == []
    service.repair.assert_not_awaited()


def test_a_write_payload_is_refused_before_the_factory_is_opened():
    client, factory, _, service = _build()

    response = client.post(_PATH, json={"tenant": "other-tenant"})

    assert response.status_code == 400
    assert response.json()["data"] is None
    assert factory.profile_headers == []
    service.repair.assert_not_awaited()


def test_unavailable_factory_is_503_not_a_report():
    client, _, _, _ = _build(
        open_error=RepairPreconditionError(
            RepairPreconditionErrorCode.COLLABORATION_SYNC_UNAVAILABLE
        )
    )

    response = client.post(_PATH)

    assert response.status_code == 503
    assert response.json()["data"] is None


def test_non_family_failure_from_open_still_takes_the_500_path():
    """Only the two known families are mapped; anything else stays unhandled."""
    client, _, _, _ = _build(
        open_error=RuntimeError("unexpected delivery construction failure"),
        client_kwargs={"raise_server_exceptions": False},
    )

    response = client.post(_PATH)

    assert response.status_code == 500
    assert "Internal Server Error" in response.text


def test_an_unknown_code_inside_a_known_family_is_an_enveloped_500():
    """A new kernel code must fail loudly, never become a pretend outcome."""
    service = AsyncMock(spec=CollaborationRepairServiceProtocol)
    service.repair.side_effect = RepairPreconditionError("brand_new_kernel_code")
    client, _, _, _ = _build(service=service)

    response = client.post(_PATH)

    assert response.status_code == 500
    assert response.json()["data"] is None


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (
            RepairPreconditionError(
                RepairPreconditionErrorCode.COLLABORATION_SYNC_UNAVAILABLE
            ),
            502,
        ),
        (
            RepairPreconditionError(
                RepairPreconditionErrorCode.COLLABORATION_BOT_NOT_FOUND
            ),
            404,
        ),
        (RepairPreconditionError(RepairPreconditionErrorCode.OWNER_CHANGED), 409),
        (
            RepairPreconditionError(
                RepairPreconditionErrorCode.REPAIR_DEADLINE_EXCEEDED
            ),
            504,
        ),
        (
            RepairFactsError(RepairFactsErrorCode.COLLABORATION_BOT_NOT_FOUND),
            404,
        ),
        (
            RepairFactsError(
                RepairFactsErrorCode.COLLABORATION_BOT_IDENTITY_UNRESOLVED
            ),
            409,
        ),
    ],
)
def test_service_preconditions_map_to_their_documented_status(error, status):
    service = AsyncMock(spec=CollaborationRepairServiceProtocol)
    service.repair.side_effect = error
    client, _, _, _ = _build(service=service)

    response = client.post(_PATH)

    assert response.status_code == status
    assert response.json()["data"] is None


@pytest.mark.parametrize(
    ("error", "status", "level"),
    [
        (
            RepairPreconditionError(
                RepairPreconditionErrorCode.REPAIR_DEADLINE_EXCEEDED
            ),
            504,
            logging.ERROR,
        ),
        (
            RepairPreconditionError(RepairPreconditionErrorCode.OWNER_CHANGED),
            409,
            logging.WARNING,
        ),
    ],
)
def test_mapped_failure_writes_a_redacted_public_error_record(
    caplog, error, status, level
):
    """Returning a response bypasses ``@envelope_errors``, so this is the trail."""
    service = AsyncMock(spec=CollaborationRepairServiceProtocol)
    service.repair.side_effect = error
    client, _, _, _ = _build(service=service)

    with caplog.at_level(logging.DEBUG):
        response = client.post(
            _PATH,
            headers={
                "Authorization": "Bearer secret-token",
                "Cookie": "session=secret-cookie",
            },
        )

    assert response.status_code == status
    record = next(
        r for r in caplog.records if f"[Public {status}]" in r.getMessage()
    )
    message = record.getMessage()
    assert type(error).__name__ in message
    assert "secret-token" not in message
    assert "secret-cookie" not in message
    assert record.levelno == level


def test_every_kernel_error_code_has_an_http_decision():
    """A new kernel code must fail here instead of silently becoming a 500.

    Kept in the test rather than an import-time assert: the live tables are the
    contract, and this is the only assertion that has to enumerate the kernel
    vocabularies. ``collaboration_sync_unavailable`` is excluded on the
    precondition side because its status is decided by the raise site (503 at
    factory open, 502 after the probe).
    """
    site_mapped = {RepairPreconditionErrorCode.COLLABORATION_SYNC_UNAVAILABLE}
    missing_preconditions = (
        set(RepairPreconditionErrorCode) - site_mapped - set(PRECONDITION_STATUS)
    )
    missing_facts = set(RepairFactsErrorCode) - set(FACTS_STATUS)

    assert not missing_preconditions, missing_preconditions
    assert not missing_facts, missing_facts


def test_app_only_caller_without_a_grant_never_reaches_the_factory():
    class _AppOnlyPrincipal:
        """Verified app with no end user, as ``VerifiedCaller`` models it."""

        app_id = 7
        user_id = None
        has_user = False
        principals = ()

    client, factory, _, _ = _build(principal=_AppOnlyPrincipal())

    response = client.post(_PATH)

    assert response.status_code == 404
    assert factory.profile_headers == []


def test_after_handoff_the_old_owner_is_refused_and_the_new_owner_repairs():
    bots = Mock()

    def get_bot(bot_id, owner_id):
        if owner_id != "new-owner":
            raise BotNotFoundError("Bot not found")
        return {
            "id": _BOT_PK,
            "bot_id": bot_id,
            "entity_id": "entity-1",
            "env": _ENV,
        }

    bots.get_bot.side_effect = get_bot
    service = AsyncMock(spec=CollaborationRepairServiceProtocol)
    service.repair.return_value = _report()
    factory = _Factory(service)

    def app_for(principal_user: str) -> FastAPI:
        app = FastAPI()
        app.include_router(collaboration_repair_router)
        app.dependency_overrides[require_principal] = lambda: {
            "user_id": principal_user
        }
        attach_injector(app, Injector([_Bindings(bots, factory)]))
        mount_public_error_handlers(app)
        return app

    old = user_scoped_client(app_for("old-owner"), "old-owner")
    assert old.post(_PATH).status_code == 404
    assert factory.opens == 0
    service.repair.assert_not_awaited()

    new = user_scoped_client(app_for("new-owner"), "new-owner")
    assert new.post(_PATH).status_code == 200
    command = service.repair.await_args.args[0]
    assert command.key == SyncKey(_TENANT, _ENV, _BOT_PK)
    assert command.actor_user_id == "new-owner"


def test_retired_bcs_addresses_stay_404():
    """The assembled surface owns the address table; a one-router app cannot."""
    app = FastAPI()
    app.include_router(public_router())
    client = user_scoped_client(app, "u1")

    for path in (
        "/openapi/v1/bots/b-1/bcs/repair",
        "/openapi/v1/bcs/repair",
    ):
        assert client.post(path).status_code == 404


def test_the_document_declares_the_report_and_every_precondition_status():
    app = FastAPI()
    app.include_router(collaboration_repair_router)
    operation = app.openapi()["paths"][
        "/openapi/v1/bots/{bot_id}/collaboration/repair"
    ]["post"]

    for status in ("200", "401", "403", "404", "409", "502", "503", "504"):
        assert status in operation["responses"], status
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ]
    assert "CollaborationRepairReport" in ref
    for status in ("401", "403", "404", "409", "502", "503", "504"):
        ref = operation["responses"][status]["content"]["application/json"]["schema"][
            "$ref"
        ]
        assert ref == "#/components/schemas/ErrorEnvelope", status
    description = operation["responses"]["200"]["description"].lower()
    assert "not mean" in description or "does not mean" in description
