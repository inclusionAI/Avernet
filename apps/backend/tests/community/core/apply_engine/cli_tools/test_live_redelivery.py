"""Management CLI mutations use the real shared Teclaw redeliver."""
from dataclasses import replace

import pytest

from agentclaw.community.core.apply_engine.cli_tools import CliToolStatus
from agentclaw.community.core.apply_engine.cli_tools.teclaw_port import TeclawCliToolPort
from agentclaw.community.core.apply_engine.redeliver import TeclawRedeliver

from .test_service import _CTX, _decl, _service


@pytest.mark.asyncio
async def test_management_install_and_remove_redeliver_with_shared_delivery():
    deliveries = []
    resolved = []
    device = object()

    class Sync:
        def deliver_manifest_apply(self):
            deliveries.append(True)
            return {"success": True}

    def resolve(bot_id, owner_id):
        resolved.append((bot_id, owner_id))
        return device

    def dispatch(target):
        assert target is device
        return Sync()

    redeliver = TeclawRedeliver(
        resolve=resolve, dispatch=dispatch, not_bound=LookupError,
    )
    service, repo, *_ = _service(delivery=TeclawCliToolPort(redeliver=redeliver))
    ctx = replace(_CTX, engine_type="teclaw")

    installed = await service.install(ctx, _decl(), installed_by="u2")
    assert installed.status is CliToolStatus.INSTALLED
    assert repo.get(env="dev", entity_id="u1", bot_id="bot7", name="mycli")
    await service.remove(ctx, "mycli")
    assert repo.get(env="dev", entity_id="u1", bot_id="bot7", name="mycli") is None
    assert resolved == [("bot7", "u1"), ("bot7", "u1")]
    assert deliveries == [True, True]
