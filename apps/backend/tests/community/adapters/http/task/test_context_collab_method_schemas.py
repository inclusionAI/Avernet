"""Open publication parses wire strings into the shared domain enum."""

import pytest
from pydantic import ValidationError

from agentclaw.community.adapters.http.task.schemas_context_collab_methods import (
    OpenOpportunityRequest,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ParticipationType,
)


def _body(**overrides):
    return {
        "scope_ref": "collet_enum",
        "participation_type": "COLLET_PARTICIPANT",
        "opens_at": "2026-01-01T00:00:00+00:00",
        **overrides,
    }


@pytest.mark.parametrize("participation_type", list(ParticipationType))
def test_open_request_converts_wire_value_to_domain_enum(participation_type):
    body = _body(participation_type=participation_type.value)
    request = OpenOpportunityRequest.model_validate(body)
    assert request.participation_type is participation_type
    context = ActorContext(PrincipalRef("human", "owner"), "api")
    command = request.to_domain("collet_enum", context)
    assert command.participation_type is participation_type
    assert command.collab_context_id == "collet_enum"
    assert command.context is context
    wire = request.model_dump(mode="json", exclude_unset=True)
    assert type(wire["participation_type"]) is str
    assert wire == body


@pytest.mark.parametrize("value", ["unknown", "group_member", "", None, 1, []])
def test_open_request_rejects_invalid_participation_types(value):
    with pytest.raises(ValidationError) as exc_info:
        OpenOpportunityRequest.model_validate(_body(participation_type=value))
    assert exc_info.value.errors()[0]["loc"] == ("participation_type",)


def test_open_request_requires_participation_type():
    body = _body()
    del body["participation_type"]
    with pytest.raises(ValidationError) as exc_info:
        OpenOpportunityRequest.model_validate(body)
    assert exc_info.value.errors()[0]["type"] == "missing"
    assert exc_info.value.errors()[0]["loc"] == ("participation_type",)


def test_open_request_schema_declares_the_existing_enum():
    schema = OpenOpportunityRequest.model_json_schema()
    assert "participation_type" in schema["required"]
    assert schema["properties"]["participation_type"]["$ref"] == (
        "#/$defs/ParticipationType"
    )
    assert schema["$defs"]["ParticipationType"]["type"] == "string"
    assert schema["$defs"]["ParticipationType"]["enum"] == [
        participation_type.value for participation_type in ParticipationType
    ]
