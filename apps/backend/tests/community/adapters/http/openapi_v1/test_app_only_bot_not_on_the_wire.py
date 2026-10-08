"""The operations whose bot an application could otherwise reach unchecked.

Four name a **skill** and no owner: the services beneath them scope by *user*
alone, so an application holding a grant on one of a user's bots would reach
that user's skills on **every** bot they own unless the handler binds the grant
to the ``(bot, owner)`` on the record. Those four are named in
``admission.SKILL_SCOPED_OPERATIONS`` and are the whole of what ``TODO(#960)``
has left; the routines create and the two skills collection reads moved to the
shared dependency when bot-first addressing gave them somewhere to put the bot
and the owner.

This file exists because these are the operations most likely to be quietly
wrong, and the failure is silent — a `200` with data the caller should not have
— which is exactly the kind a test has to catch. The mirror case, a valid grant
wrongly *refused*, is ``test_skills_shared_bot_grant.py``.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import Depends, FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.principal import (
    require_granted_own_bot,
)
from agentclaw.community.adapters.http.openapi_v1.routines import (
    router as routines_router,
)
from agentclaw.community.adapters.http.openapi_v1.skills import router as skills_router
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
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
from agentclaw.community.core.bot_app_grant.models import BotAppGrantRecord
from agentclaw.community.core.gateway_principal import (
    AppPrincipal,
    GatewayApp,
    VerifiedCaller,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    bind_bot_access_seam,
    mount_public_error_handlers,
    user_scoped_client,
)

APP_ID = 42
USER = "u-1"
#: The bot the application was granted.
GRANTED_BOT = "b-granted"
#: Another bot the same user owns, and did **not** grant. The skill routes scope
#: by user alone underneath, so this is the bot a missing check would expose.
OTHER_BOT = "b-other"
GRANTED_SKILL = "101"
OTHER_SKILL = "202"


def _app_caller() -> VerifiedCaller:
    return VerifiedCaller(
        principals=(
            AppPrincipal(
                tenant="teamclaw",
                app=GatewayApp(
                    app_id=APP_ID,
                    app_name="partner",
                    owners="platform-team",
                    tenant="teamclaw",
                ),
            ),
        )
    )


class _Grants:
    """One delegation: ``APP_ID`` may act as ``USER`` on ``GRANTED_BOT``."""

    def find(self, *, bot_id: str, owner_id: str, user_id: str, app_id: int):
        # ``USER`` owns the granted bot here, so the addressed owner is them.
        if (bot_id, owner_id, user_id, app_id) != (GRANTED_BOT, USER, USER, APP_ID):
            return None
        return BotAppGrantRecord(
            id=1,
            app_id=app_id,
            app_name="partner",
            bot_id=bot_id,
            user_id=user_id,
            owner_id=user_id,
            avernet_tenant="teamclaw",
            env="test",
            gmt_create=datetime(2026, 8, 10),
        )

    def list_for_app(self, *, app_id: int, user_id: str):
        record = self.find(
            bot_id=GRANTED_BOT, owner_id=user_id, user_id=user_id, app_id=app_id
        )
        return [record] if record else []


class _Skills:
    """Two skills on two bots, both owned by ``USER``.

    The user-scoped read admits both — which is the point. Only the grant
    separates them.
    """

    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.activated: list[str] = []
        self.listed: list[tuple[str, str]] = []
        self.uploaded: list[tuple[str, str]] = []

    def get_local_skill(self, *, skill_id: str, actor_id: str):
        bot = GRANTED_BOT if skill_id == GRANTED_SKILL else OTHER_BOT
        return {
            "id": int(skill_id),
            "bolt_id": bot,
            # The bot's owner, carried on the record. Both skills belong to
            # ``USER``'s own bots here; the cross-owner case has its own test.
            "user_id": USER,
            "name": f"skill-{skill_id}",
            "active": True,
            "gmt_created": datetime(2026, 8, 1),
            "gmt_modified": datetime(2026, 8, 2),
        }

    async def delete_local_skill(self, *, skill_id: str, owner_id: str, user_id: str):
        self.deleted.append(skill_id)

    def get_skill(self, *, skill_id: str, bot_id: str, owner_id: str, user_id: str):
        return self.get_local_skill(skill_id=skill_id, actor_id=user_id)

    async def activate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ):
        self.activated.append(skill_id)
        return {
            **self.get_local_skill(skill_id=skill_id, actor_id=actor_id),
            "changed": True,
        }

    async def deactivate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ):
        self.activated.append(skill_id)
        return {
            **self.get_local_skill(skill_id=skill_id, actor_id=actor_id),
            "active": False,
            "changed": True,
        }

    def list_bot_skills(
        self,
        *,
        bot_id,
        owner_id,
        actor_id,
        page,
        page_size,
        active=None,
        keyword=None,
        source=None,
    ):
        self.listed.append((bot_id, owner_id))
        return 0, []

    async def upload_local_skill(self, *, bot_id, owner_id, actor_id, package):
        self.uploaded.append((bot_id, owner_id))
        return {
            "operation": "created",
            "skill": self.get_local_skill(skill_id=GRANTED_SKILL, actor_id=actor_id),
        }


class _Cron:
    def __init__(self) -> None:
        self.created: list[str] = []

    async def create_cron(self, *, bot_id: str, user_id: str, nick_name: str, body):
        self.created.append(bot_id)
        return {"data": {"id": "r-1", "name": body["name"], "enabled": True}}


@pytest.fixture
def skills():
    return _Skills()


@pytest.fixture
def cron():
    return _Cron()


@pytest.fixture
def client(skills, cron):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotAppGrantServiceProtocol, to=_Grants())
            binder.bind(SkillQueryServiceProtocol, to=skills)
            binder.bind(LocalSkillDeleteServiceProtocol, to=skills)
            binder.bind(LocalSkillUploadServiceProtocol, to=skills)
            binder.bind(DirectActivationServiceProtocol, to=skills)
            binder.bind(CronRelayServiceProtocol, to=cron)
            # The four ``{skill_id}`` operations declare ``Check(MEMBER)``
            # now, so the seam runs on them. It is not what this file is
            # about — the grant check is — but it fails closed against an
            # unwired app and would answer 404 for every case below,
            # including the ones that must be served. ``USER`` is the
            # granted owner here, so the level resolves to OWNER and the
            # grant stays the only thing deciding these outcomes.
            bind_bot_access_seam(binder)

    # Mounted exactly as build_public_router mounts them, because this file is
    # about what an application caller may reach and the mount is half of that.
    # Routines is a wholly own-bot group and gets its dependency at include;
    # skills is mounted bare — its two collection routes carry the
    # addressed-bot dependency in their own decorators (which travel with the
    # router), and its four ``{skill_id}`` routes check the grant in their
    # handlers (``SKILL_SCOPED_OPERATIONS``). A group-level dependency on
    # skills here would assert against an assembly production does not have.
    app = FastAPI()
    app.include_router(skills_router)
    app.include_router(routines_router, dependencies=[Depends(require_granted_own_bot)])
    app.dependency_overrides[require_principal] = _app_caller
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, USER)


# ── the four skill operations ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "template"),
    [
        ("get", "/openapi/v1/bots/{bot}/skills/{skill}"),
        ("delete", "/openapi/v1/bots/{bot}/skills/{skill}"),
        ("post", "/openapi/v1/bots/{bot}/skills/{skill}/activate"),
        ("post", "/openapi/v1/bots/{bot}/skills/{skill}/deactivate"),
    ],
)
def test_a_skill_on_an_ungranted_bot_is_refused(client, method, template):
    """The failure this check exists for, and it is a silent one without it.

    ``OTHER_SKILL`` belongs to the *same user* on a bot they did not grant. The
    service beneath scopes by user, so it would happily serve this — a ``200``
    carrying a skill the application has no authorization for.
    """
    response = getattr(client, method)(
        template.format(bot=OTHER_BOT, skill=OTHER_SKILL)
    )

    assert response.status_code == 404, response.json()


@pytest.mark.parametrize(
    ("method", "template"),
    [
        ("get", "/openapi/v1/bots/{bot}/skills/{skill}"),
        ("delete", "/openapi/v1/bots/{bot}/skills/{skill}"),
        ("post", "/openapi/v1/bots/{bot}/skills/{skill}/activate"),
        ("post", "/openapi/v1/bots/{bot}/skills/{skill}/deactivate"),
    ],
)
def test_a_skill_on_the_granted_bot_is_served(client, method, template):
    """The same four operations still work where the grant covers them."""
    response = getattr(client, method)(
        template.format(bot=GRANTED_BOT, skill=GRANTED_SKILL)
    )

    assert response.status_code == 200, response.json()


def test_the_refusal_happens_before_the_skill_is_touched(client, skills):
    """Refused *before* acting, not reported after.

    A delete that ran and then answered 404 would be the worst of both.
    """
    client.delete(f"/openapi/v1/bots/{OTHER_BOT}/skills/{OTHER_SKILL}")
    client.post(f"/openapi/v1/bots/{OTHER_BOT}/skills/{OTHER_SKILL}/activate")

    assert skills.deleted == []
    assert skills.activated == []


# ── the body-carried bot id ──────────────────────────────────────────────────


def _routine() -> dict:
    return {
        "name": "nightly",
        "trigger": {"cron": "0 9 * * *"},
        "command": "echo hi",
    }


def test_creating_a_routine_on_an_ungranted_bot_is_refused(client, cron):
    """The bot is the address, so the shared dependency refuses before the
    handler runs at all.

    This used to be the one operation whose bot arrived in the body, after
    every dependency had already resolved — so its grant check lived in the
    handler and had to be the *first* statement there, or the refusal would
    arrive after the routine existed. Bot-first addressing removed the
    exception rather than the guarantee; what is asserted is unchanged.
    """
    response = client.post(f"/openapi/v1/bots/{OTHER_BOT}/routines", json=_routine())

    assert response.status_code == 404, response.json()
    assert cron.created == [], "refused before the routine was created"


def test_creating_a_routine_on_the_granted_bot_works(client, cron):
    response = client.post(f"/openapi/v1/bots/{GRANTED_BOT}/routines", json=_routine())

    assert response.status_code == 201, response.json()
    assert cron.created == [GRANTED_BOT]


# ── the two operations that address an owner of their own ────────────────────


def test_listing_skills_on_an_ungranted_bot_is_refused(client, skills):
    """``owner_id`` names whose bot this reads, and the grant must match.

    Classifying these as plainly owner-scoped was wrong in both directions: a
    grant on the caller's own same-named bot would authorize a read of someone
    else's, and a legitimate grant on a shared bot would be refused.
    """
    response = client.get(f"/openapi/v1/bots/{OTHER_BOT}/skills")

    assert response.status_code == 404, response.json()
    assert skills.listed == [], "refused before the read"


def test_listing_skills_on_the_granted_bot_works(client, skills):
    response = client.get(f"/openapi/v1/bots/{GRANTED_BOT}/skills")

    assert response.status_code == 200, response.json()
    assert skills.listed == [(GRANTED_BOT, USER)]


def test_listing_another_owners_bot_is_refused_even_with_a_same_named_grant(
    client, skills
):
    """The over-permissive half, concretely.

    The grant names ``GRANTED_BOT`` owned by ``USER``. Naming another owner for
    the *same* bot id addresses a different bot, and the grant does not cover
    it.
    """
    response = client.get(
        f"/openapi/v1/bots/{GRANTED_BOT}/skills",
        params={"owner_id": "someone-else"},
    )

    assert response.status_code == 404, response.json()
    assert skills.listed == []


def test_uploading_a_skill_to_an_ungranted_bot_is_refused(client, skills):
    """A write makes the mis-binding worse — it would create a skill there."""
    response = client.post(
        f"/openapi/v1/bots/{OTHER_BOT}/skills",
        content=b"PK\x03\x04",
        headers={"content-type": "application/zip"},
    )

    assert response.status_code == 404, response.json()
    assert skills.uploaded == []
