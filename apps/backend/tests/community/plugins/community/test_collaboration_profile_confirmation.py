"""An onboard acknowledgement cannot confirm an empty summary replacement."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.kernel.bot_manager_sync import DeliveryFailure
from agentclaw.community.kernel.collaboration_repair import (
    MetadataDeliveryError,
    ProfileSnapshot,
)
from agentclaw.community.plugins.community.bcs_collaboration_metadata import (
    BcnCollaborationMetadataAdapter,
)
from agentclaw.community.plugins.local.bcs_collaboration_metadata import (
    RecordingBcsCollaborationMetadataPlugin,
)
from tests.community.core.bot_management.collaboration_repair.factories import (
    default_repair_facts,
    make_repair_case,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("summary", ["", " \t\n"])
async def test_empty_summary_onboard_acknowledgement_is_not_confirmation(summary):
    bcn = Mock(spec=BcnService)
    # The actual admin onboard response does not echo the saved summary.
    bcn.onboard_bot.return_value = {"bot_uuid": "b:e", "onboarded": True, "name": "N"}
    plugin = BcnCollaborationMetadataAdapter(bcn, {})
    snapshot = ProfileSnapshot("b", "b:e", "N", summary, "")

    with pytest.raises(MetadataDeliveryError) as caught:
        await plugin.update_profile(snapshot, timeout_seconds=1)

    assert caught.value.kind is DeliveryFailure.UNKNOWN
    assert caught.value.code == "collaboration_summary_unconfirmed"
    bcn.onboard_bot.assert_called_once_with(
        bot_id="b:e", name="N", summary=summary, avatar_url="", timeout_seconds=1,
    )


@pytest.mark.asyncio
async def test_empty_summary_keeps_other_confirmed_repair_outcomes():
    facts = default_repair_facts()
    facts = replace(facts, profile=replace(facts.profile, summary=""))
    case = make_repair_case(facts=facts)
    metadata = RecordingBcsCollaborationMetadataPlugin(existing={"b:e"})
    metadata.profiles["b:e"] = replace(facts.profile, summary="old summary", avatar_url="old")
    case.service._metadata = metadata

    report = await case.service.repair(case.command)

    assert report.status == "partial_failed"
    assert [item.status for item in report.items] == [
        "succeeded", "unknown", "succeeded", "succeeded",
    ]
    assert report.items[1].error_code == "collaboration_summary_unconfirmed"
    assert metadata.profiles["b:e"].summary == "old summary"
    assert metadata.profiles["b:e"].avatar_url == ""
    assert [operation for operation, _ in metadata.calls] == ["exists", "profile", "caller"]
