"""Bot common-config HTTP router unit tests."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.bot_common_config import router
from agentclaw.community.adapters.http.bot_common_config.schemas import (
    BatchUpsertBotCommonConfigRequest,
    CreateBotCommonConfigRequest,
    DeleteBotCommonConfigRequest,
    GetBotCommonConfigRequest,
    UpdateBotCommonConfigRequest,
)
from agentclaw.community.core.common_config.models import BotCommonConfigRecord

_USER = AuthenticatedUser(id="op-1", staffId="op-1", operatorName="op-1")
KEY = dict(bot_id="b1", entity_id="staff_10001", config_key="storage_policy")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fixed_env(monkeypatch):
    monkeypatch.setattr(router.env_utils, "get_current_env", lambda: "pre")


def _record(**overrides) -> BotCommonConfigRecord:
    values = dict(
        id=7,
        bot_id="b1",
        entity_id="staff_10001",
        env="pre",
        config_key="storage_policy",
        config_value='{"storage_type":"upfs"}',
        is_delete=0,
        gmt_create=datetime(2026, 9, 17, 1, 2, 3),
        gmt_modified=None,
    )
    values.update(overrides)
    return BotCommonConfigRecord(**values)


def test_list_uses_server_env_and_serializes_records():
    service = MagicMock()
    service.list_records.return_value = (1, [_record()])

    resp = _run(
        router.list_bot_common_configs(
            bot_id="b1",
            entity_id=None,
            config_key="storage_policy",
            page_num=2,
            page_size=20,
            _=_USER,
            service=service,
        )
    )

    assert resp["data"]["total"] == 1
    item = resp["data"]["items"][0]
    assert item["id"] == 7
    assert item["config_value"] == {"storage_type": "upfs"}
    assert item["gmt_create"] == "2026-09-17T01:02:03"
    assert item["gmt_modified"] is None
    service.list_records.assert_called_once_with(
        bot_id="b1",
        entity_id=None,
        env="pre",
        config_key="storage_policy",
        page_num=2,
        page_size=20,
    )


def test_list_falls_back_to_raw_value_for_legacy_non_json():
    service = MagicMock()
    service.list_records.return_value = (1, [_record(config_value="manual")])

    resp = _run(router.list_bot_common_configs(_=_USER, service=service))

    assert resp["data"]["items"][0]["config_value"] == "manual"


def test_get_returns_decoded_value_from_service():
    service = MagicMock()
    service.get_config.return_value = {"storage_type": "upfs"}

    resp = _run(
        router.get_bot_common_config(GetBotCommonConfigRequest(**KEY), _=_USER, service=service)
    )

    assert resp["data"] == {**KEY, "config_value": {"storage_type": "upfs"}}
    service.get_config.assert_called_once_with(**KEY, env="pre")


def test_get_missing_raises_404():
    service = MagicMock()
    service.get_config.return_value = None
    with pytest.raises(HTTPException) as exc:
        _run(router.get_bot_common_config(GetBotCommonConfigRequest(**KEY), _=_USER, service=service))
    assert exc.value.status_code == 404


def test_create_passes_raw_json_value_and_server_env():
    service = MagicMock()
    service.create_record.return_value = 9

    resp = _run(
        router.create_bot_common_config(
            CreateBotCommonConfigRequest(**KEY, config_value={"storage_type": "upfs"}),
            _=_USER,
            service=service,
        )
    )

    assert resp["data"] == {"config_id": 9}
    service.create_record.assert_called_once_with(
        **KEY, env="pre", value={"storage_type": "upfs"}
    )


def test_update_missing_raises_404():
    service = MagicMock()
    service.update_record.return_value = False
    with pytest.raises(HTTPException) as exc:
        _run(
            router.update_bot_common_config(
                UpdateBotCommonConfigRequest(id=1, config_value={"a": 1}),
                _=_USER,
                service=service,
            )
        )
    assert exc.value.status_code == 404
    service.update_record.assert_called_once_with(config_id=1, value={"a": 1})


def test_upsert_delegates_with_server_env():
    service = MagicMock()
    service.upsert_record.return_value = 3

    resp = _run(
        router.upsert_bot_common_config(
            CreateBotCommonConfigRequest(**KEY, config_value="manual"),
            _=_USER,
            service=service,
        )
    )

    assert resp["data"] == {"config_id": 3}
    service.upsert_record.assert_called_once_with(**KEY, env="pre", value="manual")


def test_batch_upsert_builds_entries_with_server_env():
    service = MagicMock()
    service.batch_upsert_records.return_value = [1, 2]

    resp = _run(
        router.batch_upsert_bot_common_configs(
            BatchUpsertBotCommonConfigRequest(
                items=[
                    CreateBotCommonConfigRequest(**KEY, config_value={"v": 1}),
                    CreateBotCommonConfigRequest(
                        **{**KEY, "bot_id": "b2"}, config_value={"v": 2}
                    ),
                ]
            ),
            _=_USER,
            service=service,
        )
    )

    assert resp["data"] == {"config_ids": [1, 2]}
    kwargs = service.batch_upsert_records.call_args.kwargs
    assert kwargs["env"] == "pre"
    entries = kwargs["records"]
    assert [e.bot_id for e in entries] == ["b1", "b2"]
    assert [e.value for e in entries] == [{"v": 1}, {"v": 2}]


def test_delete_missing_raises_404():
    service = MagicMock()
    service.delete_record.return_value = False
    with pytest.raises(HTTPException) as exc:
        _run(
            router.delete_bot_common_config(
                DeleteBotCommonConfigRequest(id=1), _=_USER, service=service
            )
        )
    assert exc.value.status_code == 404
