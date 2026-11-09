"""Rule 25 conformance — ServiceBotEvalPlugin.

The service-Bot admission flow calls a transport-independent evaluation plugin.
The test/local profile must remain no-op so development releases are not
blocked by an unavailable SecurityAC integration, while still preserving the
shape consumers use to render evaluation actions and history.
"""
from __future__ import annotations

import pytest

from agentclaw.community.plugin_api.service_bot_eval import (
    ServiceBotEvalContext,
    ServiceBotEvalPlugin,
)


@pytest.mark.asyncio
async def test_local_eval_gate_fails_open_and_hides_actions(world) -> None:
    plugin = world.get(ServiceBotEvalPlugin)
    context = ServiceBotEvalContext(
        bot_id="bot-eval",
        bot_version="1.2.3",
        release_id="release-1",
        publish_id="publish-1",
        operator_id="owner-1",
        latest_bot_modified_at="2026-11-08T10:00:00Z",
    )

    precheck = await plugin.precheck(context)
    assert precheck["gate_status"] == "SKIPPED"
    assert precheck["allowed"] is True

    detail = await plugin.detail(context)
    tabs = detail["tabs"]
    assert tabs["simple_cert_submit"]["visible"] is False
    assert tabs["restart_eval"]["visible"] is False
    assert tabs["eval_history"]["visible"] is True

    submission = await plugin.submit(
        context,
        files=[],
        trigger_source="manual",
    )
    assert submission["task_status"] == "SKIPPED"
    assert (await plugin.get_submission(context, "submission-1"))["submission_id"] == "submission-1"
    assert (await plugin.sync(context))["allowed"] is True
    assert (await plugin.history(context))["records"] == []


@pytest.mark.asyncio
async def test_community_eval_gate_preserves_the_same_no_op_contract(
    community_world,
) -> None:
    plugin = community_world.get(ServiceBotEvalPlugin)
    context = ServiceBotEvalContext(
        bot_id="bot-eval-community",
        bot_version="1.2.3",
        release_id="release-1",
        publish_id="publish-1",
        operator_id="owner-1",
        latest_bot_modified_at="2026-11-08T10:00:00Z",
    )

    precheck = await plugin.precheck(context)
    assert precheck["gate_status"] == "SKIPPED"
    assert precheck["allowed"] is True

    detail = await plugin.detail(context)
    assert detail["bot_id"] == context.bot_id
    assert detail["tabs"]["simple_cert_submit"]["visible"] is False
    assert detail["tabs"]["restart_eval"]["visible"] is False
    assert detail["tabs"]["eval_history"]["visible"] is True

    submission = await plugin.submit(context, files=[], trigger_source="manual")
    assert submission["task_status"] == "SKIPPED"
    assert (await plugin.get_submission(context, "submission-1"))["submission_id"] == "submission-1"
    assert (await plugin.history(context))["records"] == []
    assert (await plugin.sync(context))["allowed"] is True
