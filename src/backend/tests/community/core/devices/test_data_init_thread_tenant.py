"""The device data-init thread inherits the request's tenant.

`_trigger_data_init_on_device_ready` (the device-SUCCEEDED callback) spawns a
bare `threading.Thread` that runs `DataInitService.trigger_init`, which
reads/writes `BotModel` (data_init_status lives in `bot.ext`). Bare threads don't
copy `ContextVar`s, so it is wrapped with `bind_current_avernet_tenant`; this
proves the wrapped thread runs under the spawning request's tenant.

Flagged by a review note on PR #478.
"""
import threading
from unittest.mock import Mock

import pytest

from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
    get_current_avernet_tenant,
)

pytestmark = pytest.mark.unit


class _FakeDataInit:
    def __init__(self, sink):
        self._sink = sink

    async def trigger_init(self, **kwargs):
        self._sink["tenant"] = get_current_avernet_tenant()
        self._sink["kwargs"] = kwargs


def test_data_init_thread_inherits_request_tenant(monkeypatch):
    sink: dict = {}

    # Construct only the slice of DeviceService the callback touches.
    svc = DeviceService.__new__(DeviceService)
    svc._bot_query = Mock()
    svc._bot_query.get_by_binding_id.return_value = {
        "bot_id": "b1",
        "owner_id": "o1",
        "entity_id": "e1",
        "entity_type": "staff",
        "ext": {"data_init_status": "pending_init"},
        "status": "ACTIVE",
    }
    svc._data_init_service_provider = lambda: _FakeDataInit(sink)

    # Capture the spawned thread so we can join it deterministically.
    created: dict = {}
    real_thread_cls = threading.Thread

    def _capturing_thread(*args, **kwargs):
        t = real_thread_cls(*args, **kwargs)
        created["t"] = t
        return t

    monkeypatch.setattr(threading, "Thread", _capturing_thread)

    record = Mock()
    record.id = 42
    record.entity_id = "e1"
    record.entity_type = "staff"

    with avernet_tenant_scope("tenant-x"):
        svc._trigger_data_init_on_device_ready(device_id="dev-1", record=record)

    assert "t" in created, "data-init thread was not spawned"
    created["t"].join(timeout=5)

    # The thread ran under the spawning request's tenant, not the default.
    assert sink["tenant"] == "tenant-x"
    assert sink["kwargs"]["bot_id"] == "b1"
