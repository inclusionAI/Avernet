"""Typed Collet artifacts are model-only and preserve empty legacy representations."""

import json
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.artifact import (
    Artifact,
    ResourceBinding,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet_metadata import (
    ColletMetadata,
)


@pytest.fixture(params=[False, True], ids=["legacy", "with_metadata"])
def collet(request):
    owner = PrincipalRef("human", "owner")
    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    return Collet(
        collet_id="collet-1",
        initiator_ref=owner,
        state=ColletState.ACTIVE,
        scope=None,
        version=1,
        metadata=ColletMetadata(now, owner, now, owner) if request.param else None,
    )


@pytest.fixture
def artifacts():
    return (
        Artifact(
            "requirements",
            ResourceBinding(
                ResourceRef("document", "requirements.md", "v1"), "grant-1"
            ),
        ),
        Artifact(
            "pr-draft",
            ResourceBinding(ResourceRef("pull_request", "pr-draft.json", None), None),
        ),
    )


def test_default_artifacts_preserve_existing_wire_shape_and_etag(collet):
    assert collet.artifacts == ()
    data = collet.to_dict()
    assert "artifacts" not in data
    representation = {key: value for key, value in data.items() if key != "etag"}
    canonical = json.dumps(
        representation, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    expected_etag = '"' + sha256(canonical.encode("utf-8")).hexdigest() + '"'
    assert collet.etag == expected_etag
    assert Collet.from_dict(data) == collet
    # Explicit empty input normalizes to the same representation and tag.
    assert Collet.from_dict({**data, "artifacts": []}) == collet


def test_typed_artifacts_roundtrip_using_l4_artifact_contract(collet, artifacts):
    original = replace(collet, artifacts=artifacts)
    data = original.to_dict()
    assert data["artifacts"] == [
        {
            "artifact_id": "requirements",
            "resource_binding": {
                "resource_ref": {
                    "resource_type": "document",
                    "resource_id": "requirements.md",
                    "revision": "v1",
                },
                "grant_ref": "grant-1",
            },
        },
        {
            "artifact_id": "pr-draft",
            "resource_binding": {
                "resource_ref": {
                    "resource_type": "pull_request",
                    "resource_id": "pr-draft.json",
                    "revision": None,
                },
                "grant_ref": None,
            },
        },
    ]
    restored = Collet.from_dict(json.loads(json.dumps(data)))
    assert restored == original
    assert all(isinstance(artifact, Artifact) for artifact in restored.artifacts)
    assert isinstance(restored.artifacts[0].resource_binding, ResourceBinding)
    assert isinstance(restored.artifacts[0].resource_binding.resource_ref, ResourceRef)
    assert original.version == collet.version
    assert original.state == collet.state
    assert original.metadata == collet.metadata
    assert collet.artifacts == ()
    data["artifacts"][0]["resource_binding"]["resource_ref"]["resource_id"] = "changed"
    assert original.artifacts == artifacts


def test_artifacts_are_included_in_etag(collet, artifacts):
    changed = replace(collet, artifacts=artifacts)
    assert changed.etag != collet.etag
    assert replace(changed, artifacts=artifacts[:1]).etag != changed.etag
    assert replace(changed, artifacts=tuple(reversed(artifacts))).etag != changed.etag
    if collet.metadata is not None:
        data = changed.to_dict()
        data["etag"] = collet.etag
        with pytest.raises(ContextCollabValidationError, match="etag"):
            Collet.from_dict(data)


@pytest.mark.parametrize(
    "value", [None, "artifact://one", [], ("artifact://one",), ({},), (1,)]
)
def test_constructor_rejects_untyped_artifacts(collet, value):
    with pytest.raises(ContextCollabValidationError, match="artifacts"):
        replace(collet, artifacts=value)


@pytest.mark.parametrize("value", [None, "artifact://one", (), [""], [1]])
def test_wire_rejects_invalid_artifact_containers(collet, value):
    with pytest.raises(ContextCollabValidationError, match="artifacts"):
        Collet.from_dict({**collet.to_dict(), "artifacts": value})


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"artifact_id": "", "resource_binding": {}},
        {"artifact_id": "output", "resource_binding": "resource://output"},
        {"artifact_id": "output", "resource_binding": {"resource_ref": {}}},
    ],
)
def test_wire_reuses_l4_artifact_validation(collet, value):
    with pytest.raises(ContextCollabValidationError):
        Collet.from_dict({**collet.to_dict(), "artifacts": [value]})
