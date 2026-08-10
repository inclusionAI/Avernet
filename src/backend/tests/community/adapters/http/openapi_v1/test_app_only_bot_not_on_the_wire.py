"""The seven operations the shared grant check cannot bind, for an app caller.

Four name a skill, one carries its bot in the body, and two name a bot but
address an owner under their own parameter (``owner_entity_id``). The shared
dependency **defers** for exactly these seven — they are named in
``admission.py``, not detected by their shape — and each handler binds the grant
to the ``(bot, owner)`` it actually acts on before acting.

This file exists because these are the operations most likely to be quietly
wrong. The services beneath the skill routes scope by *user* alone, so an
application holding a grant on one of a user's bots would otherwise reach that
user's skills on **every** bot they own; and a body-carried bot id arrives after
every dependency has already run. Both failures are silent — a `200` with data
the caller should not have — which is exactly the kind a test has to catch.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.routines import (
    router as routines_router,
)
from agentclaw.community.adapters.http.openapi_v1.skills import router as skills_router
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
from agentclaw.community.api.local_skill_delete_service import (
    LocalSkillDeleteServiceProtocol,
)
from agentclaw.community.api.local_skill_query_service import (
    LocalSkillQueryServiceProtocol,
)
from agentclaw.community.api.local_skill_state_service import (
    LocalSkillStateServiceProtocol,
)
from agentclaw.community.api.local_skill_upload_service import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.core.bot_app_grant.models import BotAppGrantRecord
from agentclaw.community.core.gateway_principal import (
    AppPrincipal,
    GatewayApp,
    VerifiedCaller,
)
from tests.community.adapters.http.openapi_v1.conftest import (
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

    def find(self, *, bot_id: str, user_id: str, app_id: int):
        if (bot_id, user_id, app_id) != (GRANTED_BOT, USER, APP_ID):
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
        record = self.find(bot_id=GRANTED_BOT, user_id=user_id, app_id=app_id)
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

    async def delete_local_skill(self, *, skill_id: str, actor_id: str):
        self.deleted.append(skill_id)

    async def set_local_skill_active(self, *, skill_id: str, actor_id: str, active):
        self.activated.append(skill_id)
        return {**self.get_local_skill(skill_id=skill_id, actor_id=actor_id),
                "changed": True}

    def list_local_skills(self, *, bot_id, owner_id, actor_id, page, page_size,
                          active=None, keyword=None):
        self.listed.append((bot_id, owner_id))
        return 0, []

    async def upload_local_skill(self, *, bot_id, owner_id, actor_id, package):
        self.uploaded.append((bot_id, owner_id))
        return {"operation": "created",
                "skill": self.get_local_skill(skill_id=GRANTED_SKILL,
                                              actor_id=actor_id)}


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
            binder.bind(LocalSkillQueryServiceProtocol, to=skills)
            binder.bind(LocalSkillDeleteServiceProtocol, to=skills)
            binder.bind(LocalSkillStateServiceProtocol, to=skills)
            binder.bind(LocalSkillUploadServiceProtocol, to=skills)
            binder.bind(CronRelayServiceProtocol, to=cron)

    app = FastAPI()
    app.include_router(skills_router)
    app.include_router(routines_router)
    app.dependency_overrides[require_principal] = _app_caller
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, USER)


# ── the four skill operations ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "template"),
    [
        ("get", "/openapi/v1/bots/skills/{skill}"),
        ("delete", "/openapi/v1/bots/skills/{skill}"),
        ("post", "/openapi/v1/bots/skills/{skill}/activate"),
        ("post", "/openapi/v1/bots/skills/{skill}/deactivate"),
    ],
)
def test_a_skill_on_an_ungranted_bot_is_refused(client, method, template):
    """The failure this check exists for, and it is a silent one without it.

    ``OTHER_SKILL`` belongs to the *same user* on a bot they did not grant. The
    service beneath scopes by user, so it would happily serve this — a ``200``
    carrying a skill the application has no authorization for.
    """
    response = getattr(client, method)(template.format(skill=OTHER_SKILL))

    assert response.status_code == 404, response.json()


@pytest.mark.parametrize(
    ("method", "template"),
    [
        ("get", "/openapi/v1/bots/skills/{skill}"),
        ("delete", "/openapi/v1/bots/skills/{skill}"),
        ("post", "/openapi/v1/bots/skills/{skill}/activate"),
        ("post", "/openapi/v1/bots/skills/{skill}/deactivate"),
    ],
)
def test_a_skill_on_the_granted_bot_is_served(client, method, template):
    """The same four operations still work where the grant covers them."""
    response = getattr(client, method)(template.format(skill=GRANTED_SKILL))

    assert response.status_code == 200, response.json()


def test_the_refusal_happens_before_the_skill_is_touched(client, skills):
    """Refused *before* acting, not reported after.

    A delete that ran and then answered 404 would be the worst of both.
    """
    client.delete(f"/openapi/v1/bots/skills/{OTHER_SKILL}")
    client.post(f"/openapi/v1/bots/skills/{OTHER_SKILL}/activate")

    assert skills.deleted == []
    assert skills.activated == []


# ── the body-carried bot id ──────────────────────────────────────────────────


def _routine(bot_id: str) -> dict:
    return {
        "bot_id": bot_id,
        "name": "nightly",
        "trigger": {"cron": "0 9 * * *"},
        "command": "echo hi",
    }


def test_creating_a_routine_on_an_ungranted_bot_is_refused(client, cron):
    """The bot arrives in the body, after every dependency has run.

    So the check is in the handler — and it must be the *first* thing, or the
    refusal arrives after the routine exists.
    """
    response = client.post("/openapi/v1/bots/routines", json=_routine(OTHER_BOT))

    assert response.status_code == 404, response.json()
    assert cron.created == [], "refused before the routine was created"


def test_creating_a_routine_on_the_granted_bot_works(client, cron):
    response = client.post("/openapi/v1/bots/routines", json=_routine(GRANTED_BOT))

    assert response.status_code == 201, response.json()
    assert cron.created == [GRANTED_BOT]


# ── the two operations that address an owner of their own ────────────────────


def test_listing_skills_on_an_ungranted_bot_is_refused(client, skills):
    """``owner_entity_id`` names whose bot this reads, and the grant must match.

    Classifying these as plainly owner-scoped was wrong in both directions: a
    grant on the caller's own same-named bot would authorize a read of someone
    else's, and a legitimate grant on a shared bot would be refused.
    """
    response = client.get(
        "/openapi/v1/bots/skills", params={"bot_id": OTHER_BOT}
    )

    assert response.status_code == 404, response.json()
    assert skills.listed == [], "refused before the read"


def test_listing_skills_on_the_granted_bot_works(client, skills):
    response = client.get(
        "/openapi/v1/bots/skills", params={"bot_id": GRANTED_BOT}
    )

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
        "/openapi/v1/bots/skills",
        params={"bot_id": GRANTED_BOT, "owner_entity_id": "someone-else"},
    )

    assert response.status_code == 404, response.json()
    assert skills.listed == []


def test_uploading_a_skill_to_an_ungranted_bot_is_refused(client, skills):
    """A write makes the mis-binding worse — it would create a skill there."""
    response = client.post(
        "/openapi/v1/bots/skills/upload",
        params={"bot_id": OTHER_BOT},
        content=b"PK\x03\x04",
        headers={"content-type": "application/zip"},
    )

    assert response.status_code == 404, response.json()
    assert skills.uploaded == []
