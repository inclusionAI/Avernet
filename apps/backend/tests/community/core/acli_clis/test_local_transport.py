"""``ArcaAcliPort`` over the local in-memory transport, for each acli prefix."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.core.acli_clis.arca_port import ArcaAcliPort
from agentclaw.community.core.acli_clis.operations import (
    AcliAction,
    AcliRuntime,
    EngineState,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.plugins.local.device_adapter_transport import (
    InMemoryDeviceAdapterTransport,
)

pytestmark = pytest.mark.asyncio


class _Contexts:
    def resolve(self, **_: Any) -> Any:
        return SimpleNamespace(conn_info={"bot_uuid": "bot-1"})


@pytest.mark.parametrize("engine", ["openclaw", "teclaw"])
async def test_the_local_simulator_installs_lists_and_removes(engine: str) -> None:
    port = ArcaAcliPort(contexts=_Contexts(), transport=InMemoryDeviceAdapterTransport())
    runtime = AcliRuntime(
        bot_id="bot-1", owner_id="owner-1", stage=PublishStage.DRAFT, engine=engine
    )

    op = await port.submit(runtime, action=AcliAction.INSTALL, name="yuque-cli")
    assert op.state == EngineState.SUCCEEDED
    assert (await port.get_operation(runtime, op.id)).id == op.id
    assert [t.name for t in await port.list_tools(runtime)] == ["yuque-cli"]

    await port.submit(runtime, action=AcliAction.UNINSTALL, name="yuque-cli")
    assert await port.list_tools(runtime) == []
