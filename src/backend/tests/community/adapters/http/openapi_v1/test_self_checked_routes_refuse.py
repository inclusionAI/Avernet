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
    SKILL_SCOPED_OPERATIONS,
)
from agentclaw.community.adapters.http.openapi_v1.deprecated import SELF_CHECKED_ROUTES
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
from agentclaw.community.api.local_skill_delete_service import (
    LocalSkillDeleteServiceProtocol,
)
from agentclaw.community.api.local_skill_query_service import (
    LocalSkillQueryServiceProtocol,
)
from agentclaw.community.api.local_skill_upload_service import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.api.bot_skill_asset_service import BotSkillAssetServiceProtocol
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

    async def set_local_skill_active(self, *, skill_id: str, actor_id: str, active):
        self._record("set_local_skill_active")
        return {
            **self.get_local_skill(skill_id=skill_id, actor_id=actor_id),
            "changed": True,
        }

    def get_skill(self, *, skill_id: str, bot_id: str, actor_id: str):
        return self.get_local_skill(skill_id=skill_id, actor_id=actor_id)

    async def set_active(self, *, skill_id: str, bot_id: str, actor_id: str, active):
        return await self.set_local_skill_active(
            skill_id=skill_id, actor_id=actor_id, active=active
        )

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


@pytest.fixture
def client(services):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotAppGrantServiceProtocol, to=_NoGrants())
            binder.bind(CronRelayServiceProtocol, to=services)
            for protocol in (
                LocalSkillQueryServiceProtocol,
                LocalSkillDeleteServiceProtocol,
                LocalSkillUploadServiceProtocol,
                BotSkillAssetServiceProtocol,
            ):
                binder.bind(protocol, to=services)

    app = FastAPI()
    app.include_router(build_public_router())
    app.dependency_overrides[require_principal] = _app_caller
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, USER)


def _concrete(path: str) -> str:
    """A callable URL for a route template, with the bot named in the query.

    ``bot_id`` is appended unconditionally: the retiring collection addresses
    require it there, and on a route that does not declare it an extra query
    parameter is ignored — so one spelling serves every row.
    """
    filled = "/".join(
        {"{skill_id}": SKILL, "{routine_id}": "r-1", "{bot_id}": BOT}.get(
            segment, segment
        )
        for segment in path.split("/")
    )
    return f"{filled}?user_id={USER}&bot_id={BOT}"


#: Both sets, because they are the same risk with different lifetimes: the
#: retiring addresses go with the deprecated package, the four skill-addressed
#: ones are the current contract.
SELF_CHECKED = sorted(set(SELF_CHECKED_ROUTES) | set(SKILL_SCOPED_OPERATIONS))

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
