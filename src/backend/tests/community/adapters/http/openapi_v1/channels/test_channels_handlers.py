"""Bot-scoped public Channels handler and contract tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import Request
from pydantic import ValidationError

from agentclaw.community.adapters.http.openapi_v1.authorization import (
    AUTHORIZATION,
    Check,
    EDIT_LOCK,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.adapters.http.openapi_v1.channels.router import (
    create_channel,
    delete_channel,
    get_channel,
    list_channels,
    update_channel,
    update_channel_status,
)
from agentclaw.community.adapters.http.openapi_v1.channels.schemas import (
    Channel,
    ChannelCreate,
    ChannelStatusUpdate,
    ChannelUpdate,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.channel.errors import (
    ChannelBindingConflictError,
    ChannelModeViolationError,
)
from agentclaw.community.core.channel.models import ChannelRecord
from agentclaw.community.core.engine_runtime.models import BotFacts
from agentclaw.community.di.config import AixConfig


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "headers": [],
            "path": "/",
            "query_string": b"",
        }
    )


def _record(channel_id: int = 1, **overrides) -> ChannelRecord:
    values = {
        "id": channel_id,
        "type": "dingding",
        "description": "DingTalk",
        "identity_id": "owner-1",
        "bind_bot_id": "bot-1",
        "config": {
            "client_id": "client-1",
            "client_secret": "secret-1",
            "card_template_id": "card-1",
            "card_template_key": "key-1",
            "allowlist": ["*"],
        },
        "status": "0",
        "deleted": 0,
        "gmt_create": datetime(2026, 8, 19, tzinfo=UTC),
        "gmt_modified": datetime(2026, 8, 19, tzinfo=UTC),
        "env": "pre",
        "stage": "draft",
    }
    values.update(overrides)
    return ChannelRecord(**values)


class _Relay:
    async def resolve_bot_off_loop(self, bot_id: str, owner_id: str, caller_id: str):
        return BotFacts(
            bot_id=bot_id,
            bot_type="personal",
            active_engine="openclaw",
            owner_id=owner_id,
        )


class _Locks:
    def __init__(
        self,
        *,
        has_collaborators: bool = False,
        holder_user_id: str | None = None,
    ):
        self.has_collaborators = has_collaborators
        self.holder_user_id = holder_user_id
        self.calls: list[dict] = []

    def get_lock_info(self, **kwargs):
        self.calls.append(kwargs)
        lock = (
            SimpleNamespace(holder_user_id=self.holder_user_id)
            if self.holder_user_id is not None
            else None
        )
        return SimpleNamespace(
            has_collaborators=self.has_collaborators,
            lock=lock,
        )


class _Channels:
    def __init__(self, records: list[ChannelRecord] | None = None):
        self.records = {record.id: record for record in (records or [])}
        self.calls: list[tuple[str, object]] = []
        self.next_id = max(self.records, default=0) + 1

    def list_channels(self, **kwargs):
        self.calls.append(("list", kwargs))
        return list(self.records.values())

    def create_channel(self, **kwargs):
        self.calls.append(("create", kwargs))
        channel_id = self.next_id
        self.next_id += 1
        self.records[channel_id] = _record(
            channel_id,
            type=kwargs["type"],
            description=kwargs["description"],
            identity_id=kwargs["identity_id"],
            bind_bot_id=kwargs["bind_bot_id"],
            config=kwargs["config"],
            status=kwargs["status"],
            stage=kwargs["stage"],
        )
        return channel_id

    def get_channel_by_id(self, channel_id: int):
        return self.records.get(channel_id)

    def update_channel(self, **kwargs):
        self.calls.append(("update", kwargs))
        record = self.records[kwargs["channel_id"]]
        record.type = kwargs["type"]
        record.description = kwargs["description"]
        record.identity_id = kwargs["identity_id"]
        record.bind_bot_id = kwargs["bind_bot_id"]
        record.config = kwargs["config"]
        record.status = kwargs["status"]
        record.stage = kwargs["stage"]

    async def sync_active_channel(self, channel_id: int):
        self.calls.append(("sync", channel_id))

    async def set_channel_status(self, channel_id: int, status: str):
        self.calls.append(("status", status))
        self.records[channel_id].status = status

    def delete(self, channel_id: int):
        self.calls.append(("delete", channel_id))
        self.records[channel_id].deleted = 1

    async def remove_channel(self, channel_id: int):
        self.calls.append(("remove", channel_id))
        self.delete(channel_id)


class _MissingBotChannels(_Channels):
    async def set_channel_status(self, channel_id: int, status: str):
        raise BotNotFoundError("runtime Bot is unavailable")


class _ModeViolationChannels(_Channels):
    async def set_channel_status(self, channel_id: int, status: str):
        raise ChannelModeViolationError("mode violation")


class _ConflictChannels(_Channels):
    async def set_channel_status(self, channel_id: int, status: str):
        raise ChannelBindingConflictError("conflict")


@pytest.mark.asyncio
async def test_list_returns_only_owned_draft_dingding_channels():
    service = _Channels(
        [
            _record(1),
            _record(2, identity_id="aideskdingding"),
            _record(3, stage="online"),
            _record(4, deleted=1),
            _record(5, type="unsupported"),
        ]
    )

    result = await list_channels(
        bot_id="bot-1",
        request=_request(),
        user_id="collaborator-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
    )

    assert [item.id for item in result.data] == [1]
    assert service.calls[0] == (
        "list",
        {"type": "dingding", "identity_id": "owner-1", "bind_bot_id": "bot-1"},
    )
    assert result.data[0].config.has_client_secret is True
    assert "client_secret" not in result.data[0].config.model_dump()


@pytest.mark.asyncio
async def test_list_preserves_an_explicitly_empty_allowlist():
    record = _record()
    record.config["allowlist"] = []

    result = await list_channels(
        bot_id="bot-1",
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=_Channels([record]),
    )

    assert result.data[0].config.allowlist == []


@pytest.mark.asyncio
async def test_create_derives_scope_stores_secret_and_returns_safe_projection():
    service = _Channels()
    body = ChannelCreate.model_validate(
        {
            "type": "dingding",
            "description": "Primary",
            "config": {"client_id": "client-new", "client_secret": "secret-new"},
        }
    )

    result = await create_channel(
        bot_id="bot-1",
        body=body,
        request=_request(),
        user_id="collaborator-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        locks=_Locks(),
        aix_config=AixConfig(preview_url="https://preview.example"),
    )

    create_args = service.calls[0][1]
    assert create_args["identity_id"] == "owner-1"
    assert create_args["bind_bot_id"] == "bot-1"
    assert create_args["status"] == "0"
    assert create_args["stage"] == "draft"
    assert create_args["config"]["client_secret"] == "secret-new"
    assert create_args["config"]["aix_preview_url"] == "https://preview.example"
    assert result.code == 201000
    assert result.data.status == "inactive"
    assert result.data.config.has_client_secret is True
    assert "client_secret" not in result.data.config.model_dump()


@pytest.mark.asyncio
async def test_update_preserves_null_secret_clears_nullable_fields_and_syncs_active():
    service = _Channels([_record(status="1")])
    body = ChannelUpdate.model_validate(
        {
            "config": {
                "client_secret": None,
                "card_template_id": None,
                "card_template_key": None,
                "enable_streaming_cards": True,
            }
        }
    )

    result = await update_channel(
        bot_id="bot-1",
        channel_id=1,
        body=body,
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        locks=_Locks(),
        aix_config=AixConfig(preview_url="https://preview.example"),
    )

    update_args = next(value for name, value in service.calls if name == "update")
    assert update_args["description"] == "DingTalk"
    assert update_args["config"]["client_secret"] == "secret-1"
    assert update_args["config"]["card_template_id"] is None
    assert update_args["config"]["card_template_key"] is None
    assert update_args["config"]["enable_streaming_cards"] is True
    assert ("sync", 1) in service.calls
    assert result.data.status == "active"


@pytest.mark.asyncio
async def test_status_maps_public_values_to_internal_values():
    service = _Channels([_record()])

    result = await update_channel_status(
        bot_id="bot-1",
        channel_id=1,
        body=ChannelStatusUpdate(status="active"),
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        locks=_Locks(),
    )

    assert ("status", "1") in service.calls
    assert result.data.status == "active"


@pytest.mark.asyncio
async def test_runtime_bot_lookup_failure_is_normalized_to_upstream_error():
    response = await update_channel_status(
        bot_id="bot-1",
        channel_id=1,
        body=ChannelStatusUpdate(status="active"),
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=_MissingBotChannels([_record()]),
        locks=_Locks(),
    )

    assert response.status_code == 502
    assert json.loads(response.body)["message"] == "Channel synchronization failed"


@pytest.mark.asyncio
async def test_update_status_maps_mode_violation_to_422():
    response = await update_channel_status(
        bot_id="bot-1",
        channel_id=1,
        body=ChannelStatusUpdate(status="active"),
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=_ModeViolationChannels([_record()]),
        locks=_Locks(),
    )
    assert response.status_code == 422
    assert json.loads(response.body)["message"] == "Channel mode violation"


@pytest.mark.asyncio
async def test_update_status_maps_binding_conflict_to_409():
    response = await update_channel_status(
        bot_id="bot-1",
        channel_id=1,
        body=ChannelStatusUpdate(status="active"),
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=_ConflictChannels([_record()]),
        locks=_Locks(),
    )
    assert response.status_code == 409
    assert json.loads(response.body)["message"] == "Channel binding conflict"


@pytest.mark.asyncio
async def test_delete_deactivates_active_channel_before_deleting():
    service = _Channels([_record(status="1")])

    result = await delete_channel(
        bot_id="bot-1",
        channel_id=1,
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        locks=_Locks(),
    )

    assert service.calls == [("status", "0"), ("remove", 1), ("delete", 1)]
    assert result.code == 200000
    assert result.data.deleted is True


@pytest.mark.asyncio
async def test_get_masks_channel_from_another_bot_as_not_found():
    service = _Channels([_record(bind_bot_id="bot-other")])

    response = await get_channel(
        bot_id="bot-1",
        channel_id=1,
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
    )

    assert response.status_code == 404
    assert json.loads(response.body)["message"] == "Not found"


def test_the_four_writes_still_require_bot_admin():
    """The bar ``_require_admin`` enforced, asserted where it is enforced now.

    This drove ``update_channel_status`` with a collaborator double that said
    no and asserted a masked 404 plus an untouched service. The helper is gone:
    the four writes declare ``Check(PermissionLevel.ADMIN)`` and ``bot_access``
    refuses ahead of the handler, with the same masked 404 — so there is no
    longer a double to say no with, and the refusal itself is covered by
    ``test_bot_access.py``.

    What is still this group's own is that the writes really carry ADMIN while
    the reads carry MEMBER. A write that slipped to MEMBER would let any
    collaborator rebind a Bot's DingTalk channel.
    """
    writes = [
        ("POST", "/openapi/v1/bots/{bot_id}/channels"),
        ("PATCH", "/openapi/v1/bots/{bot_id}/channels/{channel_id}"),
        ("PUT", "/openapi/v1/bots/{bot_id}/channels/{channel_id}/status"),
        ("DELETE", "/openapi/v1/bots/{bot_id}/channels/{channel_id}"),
    ]
    reads = [
        ("GET", "/openapi/v1/bots/{bot_id}/channels"),
        ("GET", "/openapi/v1/bots/{bot_id}/channels/{channel_id}"),
    ]
    for key in writes:
        rule = AUTHORIZATION[key]
        assert isinstance(rule, Check), f"{key[0]} {key[1]} is not adjudicated"
        assert rule.level is PermissionLevel.ADMIN, (
            f"{key[0]} {key[1]} moved off the ADMIN bar _require_admin enforced"
        )
        assert rule.edit_lock is EDIT_LOCK
    for key in reads:
        rule = AUTHORIZATION[key]
        assert isinstance(rule, Check) and rule.level is PermissionLevel.MEMBER, (
            f"{key[0]} {key[1]} is not the member-level read it was"
        )


@pytest.mark.asyncio
async def test_write_requires_edit_lock_when_bot_has_collaborators():
    service = _Channels([_record()])
    locks = _Locks(has_collaborators=True)

    response = await update_channel_status(
        bot_id="bot-1",
        channel_id=1,
        body=ChannelStatusUpdate(status="active"),
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        locks=locks,
    )

    assert response.status_code == 423
    assert json.loads(response.body)["message"] == "Edit lock required"
    assert locks.calls == [
        {"bot_id": "bot-1", "owner_id": "owner-1", "user_id": "owner-1"}
    ]
    assert not any(name == "status" for name, _ in service.calls)


@pytest.mark.asyncio
async def test_write_succeeds_when_caller_holds_edit_lock():
    service = _Channels([_record()])

    result = await update_channel_status(
        bot_id="bot-1",
        channel_id=1,
        body=ChannelStatusUpdate(status="active"),
        request=_request(),
        user_id="admin-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        locks=_Locks(has_collaborators=True, holder_user_id="admin-1"),
    )

    assert result.data.status == "active"


def test_write_contract_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        ChannelCreate.model_validate(
            {
                "type": "dingding",
                "owner_id": "must-not-come-from-body",
                "config": {"client_id": "client", "client_secret": "secret"},
            }
        )


def test_read_contract_never_defines_client_secret():
    channel_schema = Channel.model_json_schema()
    config_ref = channel_schema["properties"]["config"]["$ref"].rsplit("/", 1)[-1]
    config_properties = channel_schema["$defs"][config_ref]["properties"]
    assert "client_secret" not in config_properties
    assert "has_client_secret" in config_properties


# ── binding_mode 契约 ──────────────────────────────────────────────

def test_create_defaults_to_plugin_mode():
    channel = ChannelCreate(
        type="dingding",
        config={"client_id": "client-1", "client_secret": "secret-1"},
    )
    assert channel.binding_mode == "plugin"


def test_create_accepts_minimal_bcn_gateway_config():
    channel = ChannelCreate(
        type="dingding",
        binding_mode="bcn_gateway",
        config={
            "client_id": "client-1",
            "client_secret": "secret-1",
            "robot_code": "robot-1",
        },
    )
    assert channel.binding_mode == "bcn_gateway"


def test_create_requires_robot_code_for_bcn_gateway_mode():
    with pytest.raises(ValidationError) as exc:
        ChannelCreate(
            type="dingding",
            binding_mode="bcn_gateway",
            config={"client_id": "client-1", "client_secret": "secret-1"},
        )
    assert "robot_code" in str(exc.value)


def test_create_rejects_plugin_fields_for_bcn_gateway_mode():
    with pytest.raises(ValidationError) as exc:
        ChannelCreate(
            type="dingding",
            binding_mode="bcn_gateway",
            config={
                "client_id": "client-1",
                "client_secret": "secret-1",
                "robot_code": "robot-1",
                "dm_policy": "open",
            },
        )
    assert "dm_policy" in str(exc.value)


def test_create_rejects_bcn_fields_for_plugin_mode():
    with pytest.raises(ValidationError) as exc:
        ChannelCreate(
            type="dingding",
            config={
                "client_id": "client-1",
                "client_secret": "secret-1",
                "group_chat_scope": "per_sender",
            },
        )
    assert "group_chat_scope" in str(exc.value)


# ── router binding_mode 投影与不可变校验 ───────────────────────────

def _bcn_body() -> ChannelCreate:
    return ChannelCreate(
        type="dingding",
        binding_mode="bcn_gateway",
        description="BCN channel",
        config={
            "client_id": "client-1",
            "client_secret": "secret-1",
            "robot_code": "robot-1",
        },
    )


@pytest.mark.asyncio
async def test_create_bcn_gateway_stores_mode_and_defaults():
    service = _Channels()

    await create_channel(
        bot_id="bot-1",
        body=_bcn_body(),
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        locks=_Locks(),
        aix_config=AixConfig(),
    )

    create_kwargs = next(c for name, c in service.calls if name == "create")
    assert create_kwargs["config"]["binding_mode"] == "bcn_gateway"
    assert create_kwargs["config"]["group_chat_scope"] == "per_sender"
    assert create_kwargs["config"]["outbound_visibility"] == "full_transcript"


@pytest.mark.asyncio
async def test_create_plugin_omits_bcn_keys():
    service = _Channels()

    await create_channel(
        bot_id="bot-1",
        body=ChannelCreate(
            type="dingding",
            config={"client_id": "client-1", "client_secret": "secret-1"},
        ),
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        locks=_Locks(),
        aix_config=AixConfig(),
    )

    create_kwargs = next(c for name, c in service.calls if name == "create")
    assert create_kwargs["config"]["binding_mode"] == "plugin"
    assert "group_chat_scope" not in create_kwargs["config"]
    assert "outbound_visibility" not in create_kwargs["config"]


@pytest.mark.asyncio
async def test_get_projects_binding_mode():
    record = _record(config={
        **_record().config,
        "binding_mode": "bcn_gateway",
        "group_chat_scope": "per_sender",
    })
    service = _Channels([record])

    response = await get_channel(
        bot_id="bot-1",
        channel_id=1,
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
    )
    payload = json.loads(response.model_dump_json())["data"]
    assert payload["binding_mode"] == "bcn_gateway"
    assert payload["config"]["group_chat_scope"] == "per_sender"


@pytest.mark.asyncio
async def test_update_rejects_binding_mode_change():
    service = _Channels([_record(config={**_record().config, "binding_mode": "plugin"})])

    response = await update_channel(
        bot_id="bot-1",
        channel_id=1,
        body=ChannelUpdate(binding_mode="bcn_gateway"),
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        locks=_Locks(),
        aix_config=AixConfig(),
    )
    assert response.status_code == 422
    assert json.loads(response.body)["message"] == "Channel mode violation"


@pytest.mark.asyncio
async def test_update_rejects_plugin_field_on_bcn_channel():
    service = _Channels([_record(config={
        **_record().config,
        "binding_mode": "bcn_gateway",
        "robot_code": "robot-1",
    })])

    response = await update_channel(
        bot_id="bot-1",
        channel_id=1,
        body=ChannelUpdate(
            config={"client_id": "client-2", "client_secret": "secret-2", "dm_policy": "open"}
        ),
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        locks=_Locks(),
        aix_config=AixConfig(),
    )
    assert response.status_code == 422
