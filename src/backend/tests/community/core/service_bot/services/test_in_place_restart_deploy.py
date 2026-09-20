"""Restart mode survives both upgrade and recreate down to the BaaS payload."""

import pytest

from tests.community.core.service_bot.services.test_baas_service_start_cmd import (
    _make_service,
)
from tests.community.core.service_bot.services.test_bot_build_service_teclaw_routing import (
    _svc,
    _template_resolver,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["release_async", "upgrade_async"])
@pytest.mark.parametrize("in_place", [None, False, True])
async def test_restart_mode_reaches_real_deployment_payload(operation, in_place):
    build, _ = _svc("baas", baas_template_resolver=_template_resolver())
    baas = _make_service()
    build._baas_service = baas
    # Stop at the network boundary; build, BaaS payload and composer remain real.
    baas._post_bots_api = lambda **kwargs: kwargs["payload"]
    kwargs = {
        "bot": {
            "bot_id": "restart-bot", "entity_id": "owner", "entity_type": "staff",
            "bot_name": "Restart", "bot_type": "service", "active_engine": "openclaw",
        },
        "user_id": "owner",
        "migration_path": "/published/artifact",
        "runtime_kind": "baas",
    }
    if operation == "upgrade_async":
        kwargs["bot_uuid"] = "BOT-existing"
    if in_place is not None:
        kwargs["in_place"] = in_place
    payload = await getattr(build, operation)(**kwargs)
    command = payload["config"]["deploy_config"]["after_create_cmd_hook"]
    assert ("--in_place_restart true" in command) is (in_place is True)
    assert "--source_dir /published/artifact" in command
    assert "--useNas" in command
    assert "start_service.sh" in command
