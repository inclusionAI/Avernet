"""Typed Collet resources are model-only and preserve empty representations."""

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
    ResourceBinding as ArtifactResourceBinding,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet_metadata import (
    ColletMetadata,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.resource_binding import (
    ResourceBinding,
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
def resources():
    return (
        ResourceBinding(ResourceRef("document", "requirements.md", "v1")),
        ResourceBinding(ResourceRef("repository", "calculator", None)),
    )


def test_default_resources_preserve_existing_wire_shape_and_etag(collet):
    assert collet.resources == ()
    data = collet.to_dict()
    assert "resources" not in data
    representation = {key: value for key, value in data.items() if key != "etag"}
    canonical = json.dumps(
        representation, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    expected_etag = '"' + sha256(canonical.encode("utf-8")).hexdigest() + '"'
    assert collet.etag == expected_etag
    assert Collet.from_dict(data) == collet
    assert Collet.from_dict({**data, "resources": []}) == collet


def test_resources_roundtrip_without_changing_artifacts_or_other_facts(
    collet, resources
):
    artifact = Artifact(
        "pr-draft",
        ArtifactResourceBinding(ResourceRef("pull_request", "pr-1", None), None),
    )
    original = replace(collet, resources=resources, artifacts=(artifact,))
    data = original.to_dict()
    assert data["resources"] == [
        {
            "resource_ref": {
                "resource_type": "document",
                "resource_id": "requirements.md",
                "revision": "v1",
            }
        },
        {
            "resource_ref": {
                "resource_type": "repository",
                "resource_id": "calculator",
                "revision": None,
            }
        },
    ]
    restored = Collet.from_dict(json.loads(json.dumps(data)))
    assert restored == original
    assert all(isinstance(resource, ResourceBinding) for resource in restored.resources)
    assert all(
        isinstance(resource.resource_ref, ResourceRef)
        for resource in restored.resources
    )
    assert original.artifacts == (artifact,)
    assert original.version == collet.version
    assert original.state == collet.state
    assert original.metadata == collet.metadata
    assert collet.resources == ()
    data["resources"][0]["resource_ref"]["resource_id"] = "changed"
    assert original.resources == resources


def test_resources_are_included_in_etag(collet, resources):
    changed = replace(collet, resources=resources)
    assert changed.etag != collet.etag
    assert replace(changed, resources=resources[:1]).etag != changed.etag
    assert replace(changed, resources=tuple(reversed(resources))).etag != changed.etag
    if collet.metadata is not None:
        with pytest.raises(ContextCollabValidationError, match="etag"):
            Collet.from_dict({**changed.to_dict(), "etag": collet.etag})


@pytest.mark.parametrize(
    "value",
    [
        None,
        "resource://one",
        [],
        ("resource://one",),
        ({},),
        (ResourceRef("document", "one", None),),
        (ArtifactResourceBinding(ResourceRef("document", "one", None), None),),
    ],
)
def test_constructor_rejects_untyped_or_artifact_resource_bindings(collet, value):
    with pytest.raises(ContextCollabValidationError, match="resources"):
        replace(collet, resources=value)


@pytest.mark.parametrize("value", [None, "resource://one", (), [""], [1]])
def test_wire_rejects_invalid_resource_containers(collet, value):
    with pytest.raises(ContextCollabValidationError, match="resources"):
        Collet.from_dict({**collet.to_dict(), "resources": value})


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"resource_ref": "resource://one"},
        {"resource_ref": {}},
        {"resource_ref": {"resource_type": "document", "resource_id": ""}},
        {
            "resource_ref": {
                "resource_type": "document",
                "resource_id": "one",
                "revision": 1,
            }
        },
        {
            "resource_ref": {"resource_type": "document", "resource_id": "one"},
            "grant_ref": "grant-1",
        },
    ],
)
def test_wire_reuses_l4_resource_binding_validation(collet, value):
    with pytest.raises(ContextCollabValidationError):
        Collet.from_dict({**collet.to_dict(), "resources": [value]})
