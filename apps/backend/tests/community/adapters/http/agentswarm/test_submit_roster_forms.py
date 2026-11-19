"""The submit contract's exactly-one-roster rule.

The per-agent contract is unchanged by this change (identity_file stays), so
these tests also pin that an inline payload is accepted untouched.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentclaw.community.adapters.http.agentswarm.schemas import (
    AgentSpecDTO, SubmitRunDTO,
)

# The per-agent field set as it stood before agent definitions existed.
PRE_CHANGE_AGENT_FIELDS = {
    "name", "role", "instructions", "kind", "model", "engine", "bot_type",
    "identity_file", "metadata", "config_manifest",
}


def _base(**over):
    body = {"tenant": "teamclaw", "goal": "g"}
    body.update(over)
    return body


def test_inline_only_is_accepted():
    dto = SubmitRunDTO(**_base(agents=[{"name": "a", "role": "r"}]))
    assert len(dto.agents) == 1 and dto.agent_refs == []


def test_refs_only_is_accepted():
    dto = SubmitRunDTO(**_base(agent_refs=[{"definition_id": "d1"}]))
    assert len(dto.agent_refs) == 1 and dto.agents == []


def test_both_forms_is_refused():
    with pytest.raises(ValidationError):
        SubmitRunDTO(**_base(
            agents=[{"name": "a", "role": "r"}],
            agent_refs=[{"definition_id": "d1"}],
        ))


def test_neither_form_is_refused():
    with pytest.raises(ValidationError):
        SubmitRunDTO(**_base())


def test_alias_longer_than_128_is_refused():
    with pytest.raises(ValidationError):
        SubmitRunDTO(**_base(agent_refs=[{
            "definition_id": "d1", "alias": "x" * 129,
        }]))


def test_alias_is_optional():
    dto = SubmitRunDTO(**_base(agent_refs=[{"definition_id": "d1"}]))
    assert dto.agent_refs[0].alias is None


def test_inline_agent_contract_is_unchanged():
    """identity_file and the rest are still accepted: no wire break here."""
    fields = set(AgentSpecDTO.model_fields)
    assert fields == PRE_CHANGE_AGENT_FIELDS, (
        "the per-agent contract changed; identity_file removal is a separate "
        "change and must not ride along with agent definitions"
    )
    dto = SubmitRunDTO(**_base(agents=[{
        "name": "a", "role": "r", "identity_file": "SOUL.md",
    }]))
    assert dto.agents[0].identity_file == "SOUL.md"
