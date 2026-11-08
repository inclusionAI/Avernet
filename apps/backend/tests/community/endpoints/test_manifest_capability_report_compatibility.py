"""Current and historic capability reports survive the authorized HTTP reader."""

import json

import pytest
from fastapi.testclient import TestClient

from agentclaw.community.core.repository.protocols.bot import (
    BotConfigManifestApplyRepositoryProtocol,
)
from agentclaw.community.utils.env_utils import get_current_env
from .test_openapi_config_manifest_apply import (
    _APPLY_ID,
    _BOT_ID,
    _OWNER,
    _HEADERS,
    _QUERY,
    _seed_bot,
)


@pytest.mark.parametrize("suffix", ["last-apply", "applies/" + _APPLY_ID])
@pytest.mark.parametrize(
    "category,name,removed",
    [
        ("capabilities", "skill_sets/reviewers", "skill_sets/default"),
        ("skills", "review", "old-review"),
        ("mcp", "mcp.docs", "mcp.old"),
    ],
)
def test_report_poll_keeps_current_prefixes_and_historical_values(
    app_with_testing_modules,
    world,
    suffix,
    category,
    name,
    removed,
):
    _seed_bot(world)
    payload = dict(
        apply_id=_APPLY_ID,
        bot_id=_BOT_ID,
        trigger="explicit",
        result="SUCCEEDED",
        started_at=None,
        finished_at=None,
        sources=[],
        categories=[
            dict(
                category=category,
                aborted=False,
                partially_written=False,
                removed=[removed],
            )
        ],
        entries=[
            dict(category=category, name=name, action="created", error=None, note=None)
        ],
    )
    repository = world.get(BotConfigManifestApplyRepositoryProtocol)
    address = dict(
        env=get_current_env(), entity_id=_OWNER, bot_id=_BOT_ID, apply_id=_APPLY_ID
    )
    repository.start(**address, trigger="explicit", actor=_OWNER, report="{}")
    repository.finish(**address, status="SUCCEEDED", report=json.dumps(payload))
    response = TestClient(app_with_testing_modules).get(
        f"/openapi/v1/bots/{_BOT_ID}/config-manifest/{suffix}",
        params=_QUERY,
        headers=_HEADERS,
    )
    assert response.status_code == 200, response.json()
    data = response.json()["data"]
    assert data["categories"] == payload["categories"]
    assert data["entries"] == payload["entries"]
