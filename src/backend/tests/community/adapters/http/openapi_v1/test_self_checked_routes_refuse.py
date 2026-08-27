"""Every self-checking operation must actually refuse an ungranted application.

``test_admission_inventory.py`` asserts that each grant-checked operation runs
the grant dependencies — and then *excludes* the operations that check inside
their handlers, because for those the assertion is untrue by construction. That
exclusion is a hole exactly the size of this file: an operation can be named
self-checking, be excluded from the structural assertion on that basis, and
check nothing at all.

That is not a theoretical gap. It happened here. Removing the now-redundant
``caller.require_bot`` from ``list_skills`` and ``upload_skill`` — correct for
the current addresses, which get the check from a route dependency — silently
disarmed the two **retiring** addresses that reuse those same handler
functions, because ``legacy_route`` registers an endpoint and route-level
dependencies are not carried across. An application holding no grant at all
could read and write skills through them. The structural test could not see it.

So this drives every route in ``SELF_CHECKED_ROUTES`` and
``SKILL_SCOPED_OPERATIONS`` with an application principal that holds **no
grant**, and asserts two things: the response is the masked ``404``, and no
service that *acts* was reached.

"Acts" is doing real work in that sentence. The four skill-addressed operations
cannot avoid one read — resolving the skill is how they learn which bot and
owner to check the grant against, and that read is itself user-scoped, so
another user's skill is refused before the grant is consulted at all. What must
never happen is the listing, the upload, the delete, the state write or the
routine creation. Those are separated below, so this file asserts the strongest
thing that is actually true rather than a stricter one that would have to be
weakened the first time it ran.

Driven through ``build_public_router()`` rather than a hand-mounted subset,
because *how* these routes are mounted is half of what is under test.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.adapters.http.openapi_v1.admission import (
    HARNESS_SCOPED_OPERATIONS,
    SKILL_SCOPED_OPERATIONS,
)
from agentclaw.community.adapters.http.openapi_v1.deprecated import SELF_CHECKED_ROUTES
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.api.collaborator_service import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.api.local_skill_delete_service import (
    LocalSkillDeleteServiceProtocol,
)
from agentclaw.community.api.skill_query_service import (
    SkillQueryServiceProtocol,
)
from agentclaw.community.api.local_skill_upload_service import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.api.direct_activation_service import (
    DirectActivationServiceProtocol,
)
from agentclaw.community.core.gateway_principal import (
    AppPrincipal,
    GatewayApp,
    VerifiedCaller,
)

from .conftest import mount_public_error_handlers, user_scoped_client

APP_ID = 99
USER = "u-1"
BOT = "b-ungranted"
SKILL = "1"


def _app_caller() -> VerifiedCaller:
    return VerifiedCaller(
        principals=(
            AppPrincipal(
                tenant="teamclaw",
                app=GatewayApp(
                    app_id=APP_ID,
                    app_name="stranger",
                    owners="platform-team",
                    tenant="teamclaw",
                ),
            ),
        )
    )


class _NoGrants:
    """This application was never authorized for anything."""

    def find(self, **_kwargs):
        return None

    def list_for_app(self, **_kwargs):
        return []


class _GrantLookupFails:
    """The grant service itself fails while looking up the authorization."""

    def find(self, **_kwargs):
        raise RuntimeError("grant store unavailable")

    def list_for_app(self, **_kwargs):
        return []


class _NoBot:
    """The harness surface resolves the owner from the repository record."""

    def get_by_id(self, bot_id: str):
        return {
            "id": 1,
            "bot_id": bot_id,
            "owner_id": USER,
            "bot_name": "stub-bot",
            "status": "ACTIVE",
            "bot_type": "personal",
        }


class _NoCollaborators:
    """No live collaborator relationship."""

    def list_collaborators(self, **_kwargs):
        return []

    def check_collaborator_permission(self, **_kwargs):
        return {"has_permission": False}

    def get_permission_level(self, *_args, **_kwargs):
        return "none"

    def get_operable_permission_level(self, **_kwargs):
        return "none"

    def on_collaboration_changed(self, **_kwargs):
        return None


class _Services:
    """Records every call, so "refused" can be told from "refused too late"."""

    def __init__(self) -> None:
        #: Calls that resolve *which* resource is addressed. Permitted before a
        #: refusal — the skill-addressed operations have no other way to learn
        #: the bot whose grant they must check.
        self.resolved: list[str] = []
        #: Calls that read out or change something. Never permitted before a
        #: refusal, whatever the operation.
        self.acted: list[str] = []

    def _record(self, what: str):
        self.acted.append(what)

    # skills — the read is deliberately permissive, so that anything reaching it
    # gets a usable answer and a missing grant check shows up as a 200.
    def get_local_skill(self, *, skill_id: str, actor_id: str):
        self.resolved.append("get_local_skill")
        return {
            "id": int(skill_id),
            "bolt_id": BOT,
            "user_id": USER,
            "name": "s",
            "active": True,
            "gmt_created": datetime(2026, 8, 1),
            "gmt_modified": datetime(2026, 8, 2),
        }

    def list_bot_skills(self, **_kwargs):
        self._record("list_bot_skills")
        return 0, []

    async def delete_local_skill(self, **_kwargs):
        self._record("delete_local_skill")

    def get_skill(self, *, skill_id: str, bot_id: str, owner_id: str, user_id: str):
        return self.get_local_skill(skill_id=skill_id, actor_id=user_id)

    async def activate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ):
        self._record("activate_skill")
        return {
            **self.get_local_skill(skill_id=skill_id, actor_id=actor_id),
            "changed": True,
        }

    async def deactivate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ):
        self._record("deactivate_skill")
        return {
            **self.get_local_skill(skill_id=skill_id, actor_id=actor_id),
            "active": False,
            "changed": True,
        }

    async def upload_local_skill(self, *, bot_id, owner_id, actor_id, package):
        self._record("upload_local_skill")
        return {
            "operation": "created",
            "skill": self.get_local_skill(skill_id=SKILL, actor_id=actor_id),
        }

    # routines
    async def create_cron(self, *, bot_id, user_id, nick_name, body):
        self._record("create_cron")
        return {"data": {"id": "r-1", "name": body["name"], "enabled": True}}


@pytest.fixture
def services():
    return _Services()


def _make_client(services, grant_service):
    class _M(Module):
        def configure(self, binder):
            if grant_service is not None:
                binder.bind(BotAppGrantServiceProtocol, to=grant_service)
            binder.bind(BotRepository, to=_NoBot())
            binder.bind(CollaboratorServiceProtocol, to=_NoCollaborators())
            binder.bind(CronRelayServiceProtocol, to=services)
            for protocol in (
                SkillQueryServiceProtocol,
                LocalSkillDeleteServiceProtocol,
                LocalSkillUploadServiceProtocol,
                DirectActivationServiceProtocol,
            ):
                binder.bind(protocol, to=services)

    app = FastAPI()
    app.include_router(build_public_router())
    app.dependency_overrides[require_principal] = _app_caller
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, USER)


@pytest.fixture
def client(services):
    return _make_client(services, _NoGrants())


@pytest.fixture
def client_without_grant_service(services):
    return _make_client(services, None)


@pytest.fixture
def client_with_failing_grant_lookup(services):
    return _make_client(services, _GrantLookupFails())


def _concrete(path: str) -> str:
    """A callable URL for a route template.

    Path parameters are interpolated directly. ``user_id`` and an extra
    ``bot_id`` query parameter are appended for routes that need them:
    the retiring collection addresses require ``bot_id`` in the query, and
    on a route that does not declare it an extra query parameter is ignored.
    Harness paths already carry ``bot_id`` in the path.
    """
    filled = "/".join(
        {"{skill_id}": SKILL, "{routine_id}": "r-1", "{bot_id}": BOT}.get(
            segment, segment
        )
        for segment in path.split("/")
    )
    query = f"?user_id={USER}"
    if "{bot_id}" not in path:
        query += f"&bot_id={BOT}"
    return f"{filled}{query}"


def test_harness_refuses_application_when_grant_service_is_unbound(
    client_without_grant_service, services
) -> None:
    """ ``require_harness_bot_access`` fails closed when no grant reader is wired."""
    response = client_without_grant_service.request(
        "POST",
        _concrete("/openapi/v1/bots/{bot_id}/harness/diagnose"),
        json={"entity_type": "staff", "entity_id": USER},
    )
    assert response.status_code == 404, response.text
    assert not services.acted, (
        "refusing an application with no grant reader must not reach the operation"
    )


def test_harness_refuses_application_when_grant_lookup_raises(
    client_with_failing_grant_lookup, services
) -> None:
    """An unexpected grant-store failure is mapped to the same masked 404."""
    response = client_with_failing_grant_lookup.request(
        "POST",
        _concrete("/openapi/v1/bots/{bot_id}/harness/diagnose"),
        json={"entity_type": "staff", "entity_id": USER},
    )
    assert response.status_code == 404, response.text
    assert not services.acted, (
        "refusing an application when grant lookup raises must not reach the operation"
    )


#: All three sets, because they are the same risk with different lifetimes:
#: the retiring addresses go with the deprecated package, the four skill-
#: addressed ones are the current contract, and the harness operations resolve
#: the bot owner from the repository record rather than from a wire parameter.
SELF_CHECKED = sorted(
    set(SELF_CHECKED_ROUTES) | set(SKILL_SCOPED_OPERATIONS) | set(HARNESS_SCOPED_OPERATIONS)
)

#: What each write needs on the wire to get past validation and reach the point
#: where a missing grant check would show. A row that 422s would pass this test
#: for the wrong reason.
BODIES: dict[tuple[str, str], dict] = {
    ("POST", "/openapi/v1/bots/routines"): {
        "json": {
            "bot_id": BOT,
            "name": "r",
            "command": "echo hi",
            "trigger": {"cron": "0 0 * * *"},
        }
    },
    ("POST", "/openapi/v1/bots/{bot_id}/harness/diagnose"): {
        "json": {"entity_type": "staff", "entity_id": USER},
    },
    ("POST", "/openapi/v1/bots/{bot_id}/harness/preview"): {
        "json": {"entity_type": "staff", "entity_id": USER, "patch_id_list": [1]},
    },
    ("POST", "/openapi/v1/bots/{bot_id}/harness/apply"): {
        "json": {"entity_type": "staff", "entity_id": USER, "patch_id_list": [1]},
    },
    ("POST", "/openapi/v1/bots/{bot_id}/harness/rollback"): {
        "json": {"entity_type": "staff", "entity_id": USER, "patch_id": 1},
    },
    ("POST", "/openapi/v1/bots/skills/upload"): {
        "content": b"PK\x03\x04",
        "headers": {"content-type": "application/zip"},
    },
    ("POST", "/openapi/v1/bots/{bot_id}/skills"): {
        "content": b"PK\x03\x04",
        "headers": {"content-type": "application/zip"},
    },
    ("PUT", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters"): {
        "json": {"parameters": {}},
    },
}


@pytest.mark.parametrize(
    ("method", "path"), SELF_CHECKED, ids=[f"{m} {p}" for m, p in SELF_CHECKED]
)
def test_an_ungranted_application_is_refused_and_reaches_no_service(
    client, services, method: str, path: str
) -> None:
    response = client.request(method, _concrete(path), **BODIES.get((method, path), {}))

    assert response.status_code == 404, (
        f"{method} {path} answered {response.status_code} to an application "
        "holding no grant. This operation is excluded from the structural "
        "check in test_admission_inventory.py on the promise that it checks "
        f"the grant itself; it does not. Body: {response.text[:300]}"
    )
    assert not services.acted, (
        f"{method} {path} refused an ungranted application only *after* calling "
        f"{services.acted} — by then it had already read out or changed "
        "something on a bot this application holds no grant for"
    )


def test_the_set_under_test_is_not_empty() -> None:
    """A guard that silently covers nothing is worse than no guard.

    If both sets ever empty out — which is the intended end state, once the
    deprecated package goes and the skills read is re-keyed — this file should
    be deleted deliberately rather than kept as a green test asserting nothing.
    """
    assert SELF_CHECKED, "no self-checking routes left; delete this file"
