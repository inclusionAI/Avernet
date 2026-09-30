from __future__ import annotations

import pytest

from agentclaw.community.plugin_api.tc_resource_ready import (
    TC_RESOURCE_READY_SCHEMA_VERSION,
    TcResourceReadyEvent,
)


def test_resource_ready_event_has_the_exact_stable_three_field_contract():
    event = TcResourceReadyEvent.for_resource("sr_001")

    assert event.as_payload() == {
        "schema_version": "1",
        "event_id": "tc.resource.ready:sr_001",
        "res_id": "sr_001",
    }
    assert set(event.as_payload()) == {"schema_version", "event_id", "res_id"}


@pytest.mark.parametrize(
    "forbidden",
    [
        "transfer_id",
        "oss_url",
        "share_url",
        "inline_file",
        "user_id",
        "bot_id",
        "file_name",
        "size_bytes",
        "session_id",
        "conversation_id",
        "scope_type",
        "group_id",
        "members",
    ],
)
def test_resource_ready_event_never_contains_authority_or_download_fields(forbidden):
    assert forbidden not in TcResourceReadyEvent.for_resource("sr_001").as_payload()


def test_resource_ready_event_rejects_an_empty_resource_id():
    with pytest.raises(ValueError, match="res_id_required"):
        TcResourceReadyEvent.for_resource("")


@pytest.mark.parametrize(
    ("schema_version", "event_id", "error"),
    [
        ("2", "tc.resource.ready:sr_001", "unsupported_schema_version"),
        (
            TC_RESOURCE_READY_SCHEMA_VERSION,
            "tc.resource.ready:sr_other",
            "event_id_mismatch",
        ),
    ],
)
def test_direct_construction_cannot_break_the_event_invariant(
    schema_version, event_id, error
):
    with pytest.raises(ValueError, match=error):
        TcResourceReadyEvent(
            schema_version=schema_version,
            event_id=event_id,
            res_id="sr_001",
        )
