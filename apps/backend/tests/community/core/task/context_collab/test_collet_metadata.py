"""Collet description, version/audit metadata and canonical representation tags."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet_metadata import (
    COLLET_AUDIT_KEYS,
    COLLET_METADATA_KEYS,
    COLLET_SCHEMA_VERSION,
    ColletMetadata,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletCreateCommand,
)

ACTOR = PrincipalRef("human", "owner")
NOW = datetime(2026, 11, 17, 4, 5, 6, 123456, tzinfo=timezone.utc)


def collet():
    return Collet(
        "collet_1",
        ACTOR,
        ColletState.ACTIVE,
        SpaceRef("space_1", "test"),
        "evt_1",
        7,
        ColletMetadata(NOW, ACTOR, NOW, ACTOR, title="协作上下文", description="描述"),
    )


def wire(**changes):
    data = collet().to_dict()
    del data["etag"]
    data.update(changes)
    return data


def test_current_representation_roundtrip_and_revision_are_distinct_from_schema():
    original = collet()
    data = original.to_dict()
    assert data["title"] == original.title == "协作上下文"
    assert data["description"] == original.description == "描述"
    assert data["schema_version"] == original.schema_version == COLLET_SCHEMA_VERSION
    assert data["version"] == original.version == original.revision == 7
    assert data["created_at"] == data["updated_at"] == NOW.isoformat()
    assert data["created_by"] == data["updated_by"] == ACTOR.to_dict()
    assert original.created_at == original.updated_at == NOW
    assert original.created_by == original.updated_by == ACTOR
    assert data["etag"] == original.etag
    assert len(original.etag) == 66 and original.etag.startswith('"')
    assert not {"metadata", "revision", "objective", "executor_refs", "tasks"} & set(
        data
    )
    restored = Collet.from_dict(data)
    assert restored == original
    assert restored.to_dict() == data
    # JSON key ordering is irrelevant to a canonical tag.
    assert Collet.from_dict(dict(reversed(list(data.items())))).etag == original.etag
    data["title"] = "a caller changed a copy"
    assert original.title == "协作上下文"


def test_timezone_normalization_preserves_microseconds_and_etag():
    local_time = NOW.astimezone(timezone(timedelta(hours=8)))
    decoded = Collet.from_dict(
        wire(created_at=local_time.isoformat(), updated_at=local_time.isoformat())
    )
    assert decoded.created_at == NOW
    assert decoded.created_at.tzinfo == timezone.utc
    assert decoded.to_dict()["created_at"] == NOW.isoformat()
    assert decoded.etag == collet().etag


def test_optional_description_fields_can_be_absent_or_explicit_null():
    data = wire()
    del data["title"], data["description"]
    decoded = Collet.from_dict(data)
    assert decoded.title is None and decoded.description is None
    assert decoded.to_dict()["title"] is None
    assert decoded.to_dict()["description"] is None
    assert Collet.from_dict(decoded.to_dict()) == decoded
    command = ColletCreateCommand(SpaceRef("s", "test"), ACTOR, "Task objective")
    assert command.title is None and command.description is None


@pytest.mark.parametrize("field", ["title", "description"])
@pytest.mark.parametrize("value", ["", " \t", 1, False, [], {}])
def test_description_validation_in_command_metadata_and_wire(field, value):
    with pytest.raises(ContextCollabValidationError, match=field):
        ColletCreateCommand(
            SpaceRef("s", "test"), ACTOR, "Task objective", **{field: value}
        )
    with pytest.raises(ContextCollabValidationError, match=field):
        replace(collet().metadata, **{field: value})
    with pytest.raises(ContextCollabValidationError, match=field):
        Collet.from_dict(wire(**{field: value}))


@pytest.mark.parametrize("field", sorted(COLLET_AUDIT_KEYS))
def test_partial_audit_is_rejected_not_backfilled(field):
    data = wire()
    del data[field]
    with pytest.raises(ContextCollabValidationError, match="incomplete"):
        Collet.from_dict(data)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": None},
        {"schema_version": 1.1},
        {"schema_version": "unknown"},
        {"created_at": None},
        {"created_at": "not a timestamp"},
        {"created_at": "2026-11-17T04:05:06"},
        {"updated_at": "2026-11-16T00:00:00+00:00"},
        {"updated_at": 0},
        {"created_by": None},
        {"created_by": "owner"},
        {"updated_by": {}},
        {"updated_by": {"principal_type": "human", "principal_id": ""}},
    ],
)
def test_invalid_metadata_wire_fails_closed(changes):
    with pytest.raises(ContextCollabValidationError):
        Collet.from_dict(wire(**changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"created_at": NOW.replace(tzinfo=None)},
        {"updated_at": "2026-11-17T00:00:00Z"},
        {"created_by": ACTOR.to_dict()},
        {"updated_by": PrincipalRef("human", "")},
    ],
)
def test_typed_metadata_constructor_validates_audit(changes):
    with pytest.raises(ContextCollabValidationError):
        replace(collet().metadata, **changes)


@pytest.mark.parametrize("data", [None, [], {}, {"unknown": "field"}])
def test_invalid_metadata_container_is_rejected(data):
    with pytest.raises(ContextCollabValidationError):
        ColletMetadata.from_dict(data)


def test_metadata_unknown_keys_and_non_authority_fields_rejected():
    data = collet().metadata.to_dict()
    with pytest.raises(ContextCollabValidationError):
        ColletMetadata.from_dict({**data, "objective": "not context authority"})
    for field in ("objective", "executor_refs", "participants", "product_profile_ref"):
        with pytest.raises(ContextCollabValidationError, match="unknown"):
            Collet.from_dict(wire(**{field: "not Collet authority"}))


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 8},
        {"current_event_id": "evt_2"},
        {"collet_id": "collet_2"},
        {"state": "SUSPENDED"},
        {"scope": {"space_id": "space_2", "environment": "test"}},
        {"initiator_ref": PrincipalRef("human", "other").to_dict()},
        {"title": "new title"},
        {"description": "new description"},
        {"created_at": (NOW - timedelta(seconds=1)).isoformat()},
        {"updated_at": (NOW + timedelta(seconds=1)).isoformat()},
        {"created_by": PrincipalRef("human", "other").to_dict()},
        {"updated_by": PrincipalRef("bot", "worker").to_dict()},
    ],
)
def test_etag_covers_representation_and_rejects_stale_tags(changes):
    changed = wire(**changes)
    assert Collet.from_dict(changed).etag != collet().etag
    with pytest.raises(ContextCollabValidationError, match="etag"):
        Collet.from_dict({**changed, "etag": collet().etag})


def test_legacy_representation_has_no_fabricated_history_or_schema():
    data = {k: v for k, v in wire().items() if k not in COLLET_METADATA_KEYS}
    decoded = Collet.from_dict(data)
    assert decoded.metadata is None and decoded.schema_version is None
    assert decoded.title is None and decoded.description is None
    assert decoded.created_at is None and decoded.updated_at is None
    assert decoded.created_by is None and decoded.updated_by is None
    assert decoded.to_dict() == data
    with pytest.raises(ContextCollabValidationError, match="complete metadata"):
        Collet.from_dict({**data, "etag": decoded.etag})
