"""Runtime consumers resolve stable locators before current-Owner authorization."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.engine_runtime.bot_lookup import RuntimeBotLookup
from agentclaw.community.core.engine_runtime.stage import resolve_stage_device_context
from tests.community.core.engine_runtime.test_connection import _Devices, _svc
from tests.community.core.engine_runtime.test_relay import (
    _BindingStatusRepo,
    _Collaborators,
    _PublishRecord,
    _PublishRepo,
    _Resolver,
    _Transport,
    _relay,
)

BOT = "handoff-runtime-bot"
ENTITY = "original-owner"
OWNER = "current-owner"
PK = 100


class BotRows:
    """Repository boundary double; resource lookup never grants actor rights."""

    def __init__(self):
        self.rows = [
            {
                "id": PK,
                "bot_id": BOT,
                "owner_id": OWNER,
                "entity_id": ENTITY,
                "bot_type": "service",
                "active_engine": "openclaw",
                "env": "test",
            }
        ]

    def get_by_id_and_owner(self, bot_id, owner_id):
        return next(
            (
                dict(row)
                for row in self.rows
                if row["bot_id"] == bot_id and row["owner_id"] == owner_id
            ),
            None,
        )

    def list_matches_by_legacy_locator(self, bot_id, locator):
        return [
            dict(row)
            for row in self.rows
            if row["bot_id"] == bot_id
            and locator in {row["entity_id"], row["owner_id"]}
        ]


def make_runtime():
    rows = BotRows()
    # Use the production lightweight lookup and real compatibility resolver.
    bots = RuntimeBotLookup(rows)
    resolver, transport, collaborators = _Resolver(), _Transport(), _Collaborators()
    publishes = _PublishRepo(
        {PK: [_PublishRecord({"binding": {"online": 930, "verify": 920}})]}
    )
    bindings = _BindingStatusRepo({920: "ACTIVE"})
    relay = _relay(
        bot_service=bots,
        resolver=resolver,
        transport=transport,
        collaborators=collaborators,
        publish_repo=publishes,
        binding_repo=bindings,
    )
    return SimpleNamespace(
        rows=rows,
        bots=bots,
        resolver=resolver,
        transport=transport,
        collaborators=collaborators,
        publishes=publishes,
        bindings=bindings,
        relay=relay,
    )


@pytest.fixture
def runtime():
    return make_runtime()


@pytest.mark.parametrize("locator", [ENTITY, OWNER])
@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
@pytest.mark.asyncio
async def test_handoff_owner_uses_one_bot_and_stage_binding(runtime, locator, stage):
    facts = runtime.relay.resolve_bot(BOT, locator, OWNER)
    assert facts.owner_id == OWNER
    await runtime.relay.call(
        bot_id=BOT,
        owner_id=locator,
        facts=facts,
        stage=stage,
        method="GET",
        path="/api/sessions",
    )
    if stage == "draft":
        assert runtime.resolver.calls == [(BOT, OWNER)]
    else:
        assert runtime.resolver.binding_calls == [
            (930 if stage == "online" else 920, OWNER, BOT)
        ]
        assert runtime.publishes.calls[0][0] == PK


@pytest.mark.parametrize("actor", [ENTITY, "unrelated-user"])
def test_stable_locator_does_not_authorize_old_owner_or_stranger(runtime, actor):
    with pytest.raises(BotNotFoundError):
        runtime.relay.resolve_bot(BOT, ENTITY, actor)
    assert not runtime.resolver.calls and not runtime.resolver.binding_calls
    assert not runtime.transport.calls


def test_ambiguous_legacy_locator_never_reaches_a_device(runtime):
    runtime.rows.rows.append(
        {
            **runtime.rows.rows[0],
            "id": 101,
            "entity_id": "different-entity",
            "owner_id": ENTITY,
        }
    )
    with pytest.raises(BotLookupAmbiguousError):
        runtime.relay.resolve_bot(BOT, ENTITY, OWNER)
    assert not runtime.transport.calls


@pytest.mark.parametrize("locator", [ENTITY, OWNER])
def test_websocket_uses_current_owner_and_the_same_online_binding(runtime, locator):
    devices = _Devices()
    service = _svc(
        bots=runtime.bots,
        devices=devices,
        collaborators=runtime.collaborators,
        publish_repo=runtime.publishes,
    )
    service.build(bot_id=BOT, owner_id=locator, caller_id=OWNER, stage="online")
    assert devices.kwargs["binding_id"] == 930
    assert devices.kwargs["operator"].staff_id == OWNER


@pytest.mark.parametrize("locator", [ENTITY, OWNER])
def test_stage_files_resolve_source_bot_pk_from_a_compatible_locator(runtime, locator):
    resolver = Mock()
    resolve_stage_device_context(
        resolver,
        runtime.publishes,
        runtime.bindings,
        runtime.rows,
        bot_id=BOT,
        owner_id=locator,
        stage="online",
    )
    resolver.resolve_for_binding.assert_called_once_with(930, OWNER, bot_id=BOT)


@pytest.mark.parametrize("locator", [ENTITY, OWNER])
def test_existing_member_can_still_operate_after_handoff(runtime, locator):
    from agentclaw.community.core.bot_collaborator.models import PermissionLevel

    runtime.collaborators._levels[(PK, "member")] = PermissionLevel.MEMBER
    facts = runtime.relay.resolve_bot(BOT, locator, "member")
    assert facts.owner_id == OWNER
    assert runtime.collaborators.calls[-1] == (PK, "member", OWNER)


def test_duplicated_default_id_stays_with_the_addressed_personal_bot(runtime):
    runtime.rows.rows[0].update(
        bot_id="default", bot_type="personal", entity_id="staff_current-owner"
    )
    runtime.rows.rows.append(
        {
            **runtime.rows.rows[0],
            "id": 101,
            "entity_id": "staff_other-user",
            "owner_id": "other-user",
        }
    )
    facts = runtime.relay.resolve_bot("default", OWNER, OWNER)
    assert facts.owner_id == OWNER
    with pytest.raises(BotNotFoundError):
        runtime.relay.resolve_bot("default", OWNER, "other-user")


def test_missing_locator_does_not_fall_back_to_bot_id(runtime):
    with pytest.raises(BotNotFoundError):
        runtime.relay.resolve_bot(BOT, "unrelated-locator", OWNER)
    assert not runtime.resolver.calls and not runtime.resolver.binding_calls
