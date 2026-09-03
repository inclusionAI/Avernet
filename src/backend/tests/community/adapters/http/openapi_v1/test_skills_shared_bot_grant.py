"""An application with a grant on someone else's bot reaches its skills.

The skills group serves **shared** bots: its reads resolve through a
user-scoped query that admits a collaborator, so the bot an operation acts on
may be owned by someone other than the delegating user. ``bot_id`` alone does
not identify a bot — ``(owner_id, bot_id)`` does — so the grant check has to be
looked up against the owner the request addresses, not against the caller.

This file exists because that distinction was got wrong once already, in a way
no other test on this surface could see. ``test_legacy_parity.py`` drives every
retiring address against its replacement, but unauthenticated, so it compares
two ``401``\\ s and is blind to the success path. The failure it missed was a
clean ``404`` on a valid grant — the operation refused before its handler ran,
at the new address only, while the retiring address still answered ``200``.

So each of the six operations is driven twice here, at both addresses, by an
application that genuinely holds the grant, and asserted to succeed at both.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from tests.community.adapters.http.openapi_v1.conftest import public_router
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
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
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.gateway_principal import (
    AppPrincipal,
    GatewayApp,
    VerifiedCaller,
)

from .conftest import (
    SeamCollaborators,
    bind_bot_access_seam,
    mount_public_error_handlers,
    user_scoped_client,
)

APP_ID = 42
#: The delegating user — a collaborator on the bot, not its owner.
CALLER = "u-collab"
#: The bot's actual owner. The grant names *them*, which is the whole point.
OWNER = "u-owner"
BOT = "b-shared"
SKILL = "101"


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
    """One delegation, and its owner is **not** the delegating user.

    A grant keyed on the caller instead of the owner would make every assertion
    below pass for the wrong reason, so the fixture refuses that shape outright.
    """

    def find(self, *, bot_id: str, owner_id: str, user_id: str, app_id: int):
        if (bot_id, owner_id, user_id, app_id) != (BOT, OWNER, CALLER, APP_ID):
            return None
        return BotAppGrantRecord(
            id=1,
            app_id=app_id,
            app_name="partner",
            bot_id=bot_id,
            user_id=user_id,
            owner_id=owner_id,
            avernet_tenant="teamclaw",
            env="test",
            gmt_create=datetime(2026, 8, 10),
        )

    def list_for_app(self, *, app_id: int, user_id: str):
        return []


class _Skills:
    """One skill, on the shared bot, owned by ``OWNER``."""

    def get_local_skill(self, *, skill_id: str, actor_id: str):
        return {
            "id": int(skill_id),
            "bolt_id": BOT,
            "user_id": OWNER,
            "name": "shared-skill",
            "active": True,
            "gmt_created": datetime(2026, 8, 1),
            "gmt_modified": datetime(2026, 8, 2),
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
        assert (bot_id, owner_id) == (BOT, OWNER), (bot_id, owner_id)
        return 0, []

    async def delete_local_skill(self, *, skill_id: str, owner_id: str, user_id: str):
        return None

    def get_skill(self, *, skill_id: str, bot_id: str, owner_id: str, user_id: str):
        assert (bot_id, owner_id) == (BOT, OWNER)
        record = self.get_local_skill(skill_id=skill_id, actor_id=user_id)
        return record

    async def activate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ):
        assert (bot_id, owner_id) == (BOT, OWNER)
        return {
            **self.get_local_skill(skill_id=skill_id, actor_id=actor_id),
            "changed": True,
        }

    async def deactivate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ):
        assert (bot_id, owner_id) == (BOT, OWNER)
        return {
            **self.get_local_skill(skill_id=skill_id, actor_id=actor_id),
            "active": False,
            "changed": True,
        }

    async def get_content(self, **_kwargs):
        return "---\nname: shared-skill\n---\n# Shared"

    async def get_parameters(self, **_kwargs):
        return {"enabled": False}

    async def replace_parameters(self, *, parameters, **_kwargs):
        return parameters

    async def upload_local_skill(self, *, bot_id, owner_id, actor_id, package):
        assert (bot_id, owner_id) == (BOT, OWNER), (bot_id, owner_id)
        return {
            "operation": "created",
            "skill": self.get_local_skill(skill_id=SKILL, actor_id=actor_id),
        }


@pytest.fixture
def client():
    skills = _Skills()

    class _M(Module):
        def configure(self, binder):
            binder.bind(BotAppGrantServiceProtocol, to=_Grants())
            for protocol in (
                SkillQueryServiceProtocol,
                LocalSkillDeleteServiceProtocol,
                LocalSkillUploadServiceProtocol,
                DirectActivationServiceProtocol,
            ):
                binder.bind(protocol, to=skills)
            # The four ``{skill_id}`` operations and the two asset ones now
            # declare ``Check(MEMBER)``, so the seam adjudicates them before
            # the handler runs — and here the caller is **not** the owner, so
            # it really consults the collaborator service rather than
            # short-circuiting. ``CALLER`` is a collaborator on ``OWNER``'s
            # bot, which is the premise of this whole file, so MEMBER is what
            # that relation is. This does not weaken what is asserted below:
            # the grant is still the thing that has to be looked up against
            # the addressed owner, and a wrong lookup still answers 404.
            bind_bot_access_seam(
                binder,
                collaborators=SeamCollaborators(PermissionLevel.MEMBER),
            )

    app = FastAPI()
    # The assembled router, not a hand-mounted subset: what is under test is
    # partly *how* the group is mounted, so a fixture that mounted it its own
    # way could not see the defect this file was written for.
    app.include_router(public_router())
    app.dependency_overrides[require_principal] = _app_caller
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, CALLER)


#: ``(label, method, current address, retiring address)``. The owner is named in
#: the query at the current addresses and resolved from the skill record at the
#: retiring ones — that difference *is* the migration, and both must work.
PAIRS = [
    (
        "list",
        "GET",
        f"/openapi/v1/bots/{BOT}/skills?user_id={CALLER}&owner_id={OWNER}&source=LOCAL",
        f"/openapi/v1/bots/skills?user_id={CALLER}&bot_id={BOT}&owner_entity_id={OWNER}&source=LOCAL",
    ),
    (
        "get",
        "GET",
        f"/openapi/v1/bots/{BOT}/skills/{SKILL}?user_id={CALLER}&owner_id={OWNER}",
        f"/openapi/v1/bots/skills/{SKILL}?user_id={CALLER}",
    ),
    (
        "activate",
        "POST",
        f"/openapi/v1/bots/{BOT}/skills/{SKILL}/activate?user_id={CALLER}&owner_id={OWNER}",
        f"/openapi/v1/bots/skills/{SKILL}/activate?user_id={CALLER}",
    ),
    (
        "deactivate",
        "POST",
        f"/openapi/v1/bots/{BOT}/skills/{SKILL}/deactivate?user_id={CALLER}&owner_id={OWNER}",
        f"/openapi/v1/bots/skills/{SKILL}/deactivate?user_id={CALLER}",
    ),
    (
        "delete",
        "DELETE",
        f"/openapi/v1/bots/{BOT}/skills/{SKILL}?user_id={CALLER}&owner_id={OWNER}",
        f"/openapi/v1/bots/skills/{SKILL}?user_id={CALLER}",
    ),
]


@pytest.mark.parametrize(
    ("label", "method", "current", "legacy"), PAIRS, ids=[p[0] for p in PAIRS]
)
def test_a_granted_application_reaches_a_shared_bots_skills_at_both_addresses(
    client, label: str, method: str, current: str, legacy: str
) -> None:
    """A valid grant must not be answered with the not-found mask.

    ``404`` here is the specific regression: the grant check looked the
    delegation up against the delegating user rather than the owner the request
    addressed, so it found nothing and refused before the handler ran.
    """
    for address in (current, legacy):
        response = client.request(method, address)
        assert response.status_code == 200, (
            f"{label} at {address} answered {response.status_code}; an "
            "application holding a live grant on this bot must be admitted"
        )


def test_the_upload_reaches_a_shared_bot_at_both_addresses(client) -> None:
    """Separate because it carries a body, and a write is the worse failure."""
    package = b"PK\x03\x04 not a real zip, the service is stubbed"
    headers = {"content-type": "application/zip"}
    for address in (
        f"/openapi/v1/bots/{BOT}/skills?user_id={CALLER}&owner_id={OWNER}",
        f"/openapi/v1/bots/skills/upload?user_id={CALLER}&bot_id={BOT}"
        f"&owner_entity_id={OWNER}",
    ):
        response = client.post(address, content=package, headers=headers)
        assert response.status_code == 201, (
            f"upload at {address} answered {response.status_code}"
        )


def test_naming_the_wrong_owner_on_the_collection_is_still_refused(client) -> None:
    """The parameter is adjudicated, not believed.

    The grant is on ``(BOT, OWNER)``. Naming any other owner must not be
    admitted — otherwise ``owner_id`` would be a way to widen a grant rather
    than to say which bot is meant.
    """
    response = client.get(
        f"/openapi/v1/bots/{BOT}/skills?user_id={CALLER}&owner_id=u-someone-else"
    )
    assert response.status_code == 404


def test_a_skill_on_another_bot_is_not_reachable_through_this_address(client) -> None:
    """The ``{bot_id}`` segment is verified against the record, not decorative.

    The stub returns a skill on ``BOT`` whatever id is asked for, so a request
    naming a different bot is the case where the address and the record
    disagree — and it must be masked as absent.
    """
    response = client.get(f"/openapi/v1/bots/b-other/skills/{SKILL}?user_id={CALLER}")
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("GET", "content", None),
        ("GET", "parameters", None),
        ("PUT", "parameters", {"parameters": {"enabled": False}}),
    ],
)
def test_a_granted_application_reaches_the_unified_asset_operations(
    client, method: str, suffix: str, body
) -> None:
    response = client.request(
        method,
        f"/openapi/v1/bots/{BOT}/skills/{SKILL}/{suffix}?user_id={CALLER}&owner_id={OWNER}",
        json=body,
    )
    assert response.status_code == 200


def test_a_granted_application_cannot_aim_asset_operations_at_another_bot(
    client,
) -> None:
    response = client.get(
        f"/openapi/v1/bots/b-other/skills/{SKILL}/content?user_id={CALLER}"
    )
    assert response.status_code == 404
