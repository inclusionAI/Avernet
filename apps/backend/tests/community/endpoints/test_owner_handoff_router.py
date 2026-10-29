"""Real handoff Service API + journal through each authenticated HTTP command.

External credential/runtime fixtures are explicit contract doubles. They cannot
establish enterprise AAC behavior, which remains a deployment acceptance gate.
"""

from agentclaw.community.api.owner_handoff_service import OwnerHandoffServiceProtocol
from agentclaw.community.api.space_service import SpaceServiceProtocol, SpaceAccessServiceProtocol
from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    DisabledBotManagerSync,
)
from agentclaw.community.core.bot_management.owner_handoff.contracts import (
    OwnerHandoffPolicy,
)
from agentclaw.community.core.bot_management.owner_handoff.service import (
    OwnerHandoffService,
)
from agentclaw.community.core.repository.implementations.bot.owner_handoff import (
    OwnerHandoffRepository,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community.core.bot_management.test_owner_handoff import (
    Passport,
    Runtime,
    Staff,
    Queue,
)
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


ROOT = "/api/bots/owner-handoffs"


def _register_command(method, suffix, *, forbidden):
    params = {}
    body = (
        {"bot_pk": 1, "new_owner_id": "B", "request_key": "http-request"}
        if not suffix
        else None
    )
    actor = "C" if forbidden else "B" if suffix.endswith("authorization") else "A"

    def seed(world):
        for user in ["A", "B", "C"]:
            make_staff_user(world, user_id=user)
        bot = make_bot(world, bot_id="service", owner_id="A")
        service = OwnerHandoffService(
            OwnerHandoffRepository(world.get(DatabasePlugin)),
            Passport(),
            Runtime(),
            Staff(),
            world.get(BotRepository),
            Queue(),
            OwnerHandoffPolicy(enabled=True, recovery_enabled=True),
            DisabledBotManagerSync(),
            world.get(SpaceServiceProtocol),
            world.get(SpaceAccessServiceProtocol),
        )
        world.injector.binder.bind(OwnerHandoffServiceProtocol, to=service, scope=None)
        if not suffix:
            body["bot_pk"] = bot["id"]
        else:
            row = service.create(
                bot_pk=bot["id"], actor_id="A", new_owner_id="B", request_key="seed"
            )
            params["operation_id"] = row["operation_id"]
            if suffix.endswith("authorization"):
                service.step(row["operation_id"])
                service.step(row["operation_id"])

    expected = {"success": True}
    if not suffix or suffix in {"/{operation_id}", "/{operation_id}/retry"}:
        expected["data"] = {"phase": "QUEUED", "entity_id": "A"}
    elif suffix.endswith("cancel"):
        expected["data"] = {"phase": "CANCELLED"}
    elif suffix.endswith("authorization"):
        expected["data"] = {"status": "ISSUED"}

    endpoint_test(
        method=method,
        path=ROOT + suffix,
        scenario="forbidden" if forbidden else "ok",
        input=CaseInput(
            path_params=params, headers={"x-user-id": actor}, json_body=body
        ),
        seed=seed,
        expect=ExpectError(status=403)
        if forbidden
        else ExpectSuccess(json_contains=expected),
    )(lambda: None)


for _method, _suffix in [
    ("POST", ""),
    ("GET", "/{operation_id}"),
    ("POST", "/{operation_id}/retry"),
    ("POST", "/{operation_id}/cancel"),
    ("GET", "/{operation_id}/authorization"),
]:
    for _forbidden in [False, True]:
        _register_command(_method, _suffix, forbidden=_forbidden)
