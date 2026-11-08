"""Conformance projections pinned to DWS v1.0.62 output contracts."""

import json

import pytest

from engine.community.kernel.dws_connection import (
    ConnectionIdentity,
    DwsError,
    IntakePolicy,
)
from engine.community.plugins.dws_hosting import wire

IDENTITY = ConnectionIdentity(
    organization_id="org", user_id="self", self_open_id="open-self"
)
START = 1_800_000_000_000


def row(**changes):
    return (
        dict(
            messageId="m1",
            conversationId="chat",
            senderId="peer",
            messageType="text",
            text="hello",
            createTime=START + 1000,
        )
        | changes
    )


def history_payload(messages=(), **changes):
    return (
        dict(
            complete=True,
            hasMore=False,
            paginationKnown=True,
            failedCount=0,
            truncated=False,
            messages=list(messages),
        )
        | changes
    )


def parse_history(data):
    return wire.history(
        json.dumps(data), "chat", START, START + 60_000, IDENTITY, "Asia/Shanghai"
    )


@pytest.mark.parametrize("raw", ["not json", "[]", "null", "1"])
def test_invalid_json(raw):
    with pytest.raises(DwsError, match="invalid_output"):
        wire.object_json(raw)


def test_envelope_rejects_partial_and_accepts_success():
    assert wire.payload(
        json.dumps(dict(ok=True, outcome="success", data={"x": 1}))
    ) == {"x": 1}
    for value in (
        dict(ok=True, outcome="partial_failure", data={}),
        dict(ok=False, outcome="failure"),
        dict(ok=True, outcome="success", data=[]),
    ):
        with pytest.raises(DwsError):
            wire.payload(json.dumps(value))


def test_authentication_requires_expected_identity():
    assert not wire.auth_status(
        '{"success":true,"authenticated":false}', IDENTITY
    ).authenticated
    data = dict(success=True, authenticated=True, corp_id="org", user_id="self")
    assert wire.auth_status(json.dumps(data), IDENTITY).user_id == "self"
    for change in (
        dict(corp_id="other"),
        dict(user_id="other"),
        dict(authenticated="true"),
        dict(success=False),
    ):
        with pytest.raises(DwsError):
            wire.auth_status(json.dumps(data | change), IDENTITY)


def test_health_tracks_actual_consumer_and_drop_reconnect_generations():
    live = dict(
        bus={"pid": 42},
        source_state={"state": "connected", "reconnect_count": 0},
        consumers=[{"pid": 123, "dropped": 0}],
    )

    def raw():
        return json.dumps(dict(bus={"live": live}))

    first = wire.health(raw(), 123)
    assert first.connected
    assert not wire.health(raw(), 456).connected
    live["source_state"]["reconnect_count"] += 1
    second = wire.health(raw(), 123)
    assert second.generation != first.generation
    live["consumers"][0]["dropped"] += 1
    assert wire.health(raw(), 123).generation != second.generation
    live["source_state"]["state"] = "reconnecting"
    assert not wire.health(raw(), 123).connected
    assert not wire.health('{"bus":{}}', 123).connected
    for data in (dict(bus=[]), dict(bus={"live": {}})):
        with pytest.raises(DwsError, match="invalid_health"):
            wire.health(json.dumps(data), 123)


def test_discovery_is_complete_and_direct_only():
    data = dict(
        complete=True,
        unknownTypeCount=0,
        conversations=[
            {"conversationId": "dm", "type": "direct"},
            {"conversationId": "group", "type": "group"},
            {"conversationId": "dm", "type": "direct"},
        ],
    )
    assert wire.discovery(json.dumps(data)) == ("dm",)
    for change in (
        dict(complete=False),
        dict(unknownTypeCount=1),
        dict(conversations=None),
        dict(conversations=[{"type": "unknown"}]),
        dict(conversations=[{"type": "direct"}]),
        dict(conversations=[{}] * 10_001),
    ):
        with pytest.raises(DwsError):
            wire.discovery(json.dumps(data | change))


def test_history_text_self_alias_and_unsupported_are_distinct():
    result = parse_history(
        history_payload(
            [
                row(),
                row(messageId="own", senderId="open-self"),
                row(messageId="image", messageType="image"),
                row(messageId="bot", messageAiSendFlag=True),
            ]
        )
    )
    assert [m.sender_id for m in result.messages] == ["peer", "self"]
    assert result.unsupported_count == 2
    assert result.observed_ids == ("m1", "own", "image", "bot")
    assert result.activity_complete
    assert [m.sender_id for m in result.activities] == ["peer", "self", "peer", "peer"]
    assert [m.ai_generated for m in result.activities] == [False, False, False, True]


def test_unsupported_body_without_sender_has_incomplete_activity():
    result = parse_history(history_payload([row(messageType="image", senderId=None)]))
    assert result.unsupported_count == 1
    assert not result.activity_complete and not result.activities


def untyped_row(**changes):
    message = row(**changes)
    del message["messageType"]
    return message


def test_v1_history_matches_live_cli_optional_type_projection():
    result = parse_history(
        history_payload(
            [
                untyped_row(sender="Peer"),
                untyped_row(messageId="own", senderId="open-self"),
            ],
            contractVersion="im.message-list.v1",
        )
    )
    assert [m.text for m in result.messages] == ["hello", "hello"]
    assert [m.sender_id for m in result.messages] == ["peer", "self"]
    assert result.observed_ids == ("m1", "own")
    assert result.unsupported_count == 0


@pytest.mark.parametrize(
    "changes",
    [
        dict(messageType="image"),
        dict(messageType=None),
        dict(resourceRefs=[{"type": "mediaId", "resourceId": "image"}]),
        dict(forwarded=[{"text": "forwarded text"}]),
        dict(messageAiSendFlag="DWS"),
        dict(messageAiSendFlag=True),
    ],
)
def test_v1_history_does_not_treat_media_or_ai_text_as_user_message(changes):
    result = parse_history(
        history_payload([untyped_row() | changes], contractVersion="im.message-list.v1")
    )
    assert result.messages == ()
    assert result.observed_ids == ("m1",)
    assert result.unsupported_count == 1


def test_untyped_history_requires_known_projection_contract():
    assert parse_history(history_payload([untyped_row()])).messages == ()
    with pytest.raises(DwsError, match="unsupported_history_contract"):
        parse_history(history_payload([row()], contractVersion="im.message-list.v2"))


@pytest.mark.parametrize("flag", [None, False, ""])
def test_v1_history_allows_absent_ai_marker(flag):
    result = parse_history(
        history_payload(
            [untyped_row(messageAiSendFlag=flag)],
            contractVersion="im.message-list.v1",
        )
    )
    assert len(result.messages) == 1


@pytest.mark.parametrize(
    "changes",
    [
        dict(complete=False),
        dict(hasMore=True),
        dict(paginationKnown=False),
        dict(failedCount=1),
        dict(truncated=True),
        dict(messages=None),
        dict(messages=[{}] * 5001),
    ],
)
def test_incomplete_history_never_becomes_success(changes):
    with pytest.raises(DwsError):
        parse_history(history_payload() | changes)


@pytest.mark.parametrize(
    "changes",
    [
        dict(conversationId="other"),
        dict(messageId=""),
        dict(senderId=None),
        dict(createTime=START - 1),
        dict(createTime=START + 60_000),
        dict(text=None),
        dict(text="x" * 16001),
    ],
)
def test_malformed_history_never_becomes_a_message(changes):
    with pytest.raises(DwsError):
        parse_history(history_payload([row(**changes)]))


def test_history_nonobject_row():
    with pytest.raises(DwsError):
        parse_history(history_payload([None]))


def test_timestamp_uses_explicit_timezone_and_milliseconds():
    assert wire.timestamp(START, "UTC") == START
    assert wire.timestamp(START // 1000, "UTC") == START
    assert wire.timestamp(wire.rfc3339(START), "UTC") == START
    assert wire.timestamp("2026-01-01 08:00:00", "Asia/Shanghai") == wire.timestamp(
        "2026-01-01T00:00:00Z", "UTC"
    )
    for value in (None, True, 1.5, "bad"):
        with pytest.raises(DwsError):
            wire.timestamp(value, "UTC")


@pytest.mark.parametrize("value", [None, "", "a" * 513])
def test_identifier_bounds(value):
    with pytest.raises(DwsError):
        wire.identifier(value)


def test_intake_bounds():
    with pytest.raises(ValueError):
        IntakePolicy(overlap_ms=300_000)
    with pytest.raises(ValueError):
        IntakePolicy(retry_seconds=100.0)
