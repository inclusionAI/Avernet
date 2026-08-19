"""Bot-scoped public Channels handler and contract tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import Request
from pydantic import ValidationError

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


class _Collaborators:
    def __init__(self, allowed: bool = True):
        self.allowed = allowed
        self.calls: list[dict] = []

    def check_collaborator_permission(self, **kwargs):
        self.calls.append(kwargs)
        return {"has_permission": self.allowed}


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


class _MissingBotChannels(_Channels):
    async def set_channel_status(self, channel_id: int, status: str):
        raise BotNotFoundError("runtime Bot is unavailable")


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
        actor_id="collaborator-1",
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
        actor_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=_Channels([record]),
    )

    assert result.data[0].config.allowlist == []


@pytest.mark.asyncio
async def test_create_derives_scope_stores_secret_and_returns_safe_projection():
    service = _Channels()
    collaborators = _Collaborators()
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
        actor_id="collaborator-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        collaborators=collaborators,
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
        actor_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        collaborators=_Collaborators(),
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
        actor_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        collaborators=_Collaborators(),
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
        actor_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=_MissingBotChannels([_record()]),
        collaborators=_Collaborators(),
        locks=_Locks(),
    )

    assert response.status_code == 502
    assert json.loads(response.body)["message"] == "Channel synchronization failed"


@pytest.mark.asyncio
async def test_delete_deactivates_active_channel_before_deleting():
    service = _Channels([_record(status="1")])

    result = await delete_channel(
        bot_id="bot-1",
        channel_id=1,
        request=_request(),
        actor_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        collaborators=_Collaborators(),
        locks=_Locks(),
    )

    assert service.calls[-2:] == [("status", "0"), ("delete", 1)]
    assert result.code == 200000
    assert result.data.deleted is True


@pytest.mark.asyncio
async def test_get_masks_channel_from_another_bot_as_not_found():
    service = _Channels([_record(bind_bot_id="bot-other")])

    response = await get_channel(
        bot_id="bot-1",
        channel_id=1,
        request=_request(),
        actor_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
    )

    assert response.status_code == 404
    assert json.loads(response.body)["message"] == "Not found"


@pytest.mark.asyncio
async def test_write_requires_bot_admin_permission():
    service = _Channels([_record()])

    response = await update_channel_status(
        bot_id="bot-1",
        channel_id=1,
        body=ChannelStatusUpdate(status="active"),
        request=_request(),
        actor_id="member-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        collaborators=_Collaborators(allowed=False),
    )

    assert response.status_code == 404
    assert json.loads(response.body)["message"] == "Not found"
    assert not any(name == "status" for name, _ in service.calls)


@pytest.mark.asyncio
async def test_write_requires_edit_lock_when_bot_has_collaborators():
    service = _Channels([_record()])
    locks = _Locks(has_collaborators=True)

    response = await update_channel_status(
        bot_id="bot-1",
        channel_id=1,
        body=ChannelStatusUpdate(status="active"),
        request=_request(),
        actor_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        collaborators=_Collaborators(),
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
        actor_id="admin-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        collaborators=_Collaborators(),
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
