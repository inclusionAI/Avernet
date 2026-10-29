"""Submit-boundary schema validation for the agentswarm HTTP DTOs.

Wire contract enforced at the Pydantic boundary (D18 closed-set + input
caps):

* every inbound free-text field is length-capped — caps align with the
  durable columns they land in (``name`` String(128), ``role`` MEDIUMTEXT
  (20000), ``idempotency_key`` String(190) unique keys; ``goal``/
  ``instructions`` are generous Text caps);
* request DTOs are ``extra="forbid"`` — an unknown key (a typo'd
  ``idempotency_key``) must fail loudly with 422 instead of silently
  disabling dedup;
* ``provider`` is closed-set vocabulary typed by ``ProviderKind`` — the
  boundary rejects unknown values (422) instead of the router's fail-safe
  ``ProviderKind.coerce`` default (coerce stays for DB-row decode).

Unit-level: FastAPI translates a ``pydantic.ValidationError`` on a body
model to 422 automatically, so asserting ``ValidationError`` here is the
same contract as the wire.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentclaw.community.adapters.http.agentswarm.schemas import (
    AgentSpecDTO, SubmitRunDTO,
)
from agentclaw.community.core.agentswarm.domain.models import ProviderKind

GOAL_MAX = 8000
NAME_MAX = 128
ROLE_MAX = 20000
INSTRUCTIONS_MAX = 20000
IDEMPOTENCY_KEY_MAX = 190
AGENTS_MAX = 64
METADATA_MAX_ENTRIES = 32
METADATA_KEY_MAX = 200
METADATA_VALUE_MAX = 2000
CONFIG_MANIFEST_MAX = 200_000


def _body(**overrides) -> dict:
    body = {
        "tenant": "teamclaw",
        "goal": "summarize the incident timeline",
        "agents": [{"name": "scout", "role": "research"}],
    }
    body.update(overrides)
    return body


def _agent(**overrides) -> dict:
    spec = {"name": "scout", "role": "research"}
    spec.update(overrides)
    return spec


def _error_types(exc: ValidationError) -> set[str]:
    return {err["type"] for err in exc.value.errors()}


class TestGoalCap:

    def test_over_length_goal_rejected(self):
        with pytest.raises(ValidationError) as exc:
            SubmitRunDTO(**_body(goal="x" * (GOAL_MAX + 1)))
        assert "string_too_long" in _error_types(exc)

    def test_goal_at_limit_accepted(self):
        dto = SubmitRunDTO(**_body(goal="x" * GOAL_MAX))
        assert len(dto.goal) == GOAL_MAX


class TestExtraForbidden:

    def test_unknown_top_level_field_rejected(self):
        # the motivating typo: `idempotency_keyy` used to be silently
        # ignored, disabling dedup without any signal to the caller
        body = _body()
        body["idempotency_keyy"] = "typo-disables-dedup"
        with pytest.raises(ValidationError) as exc:
            SubmitRunDTO(**body)
        assert "extra_forbidden" in _error_types(exc)
        assert ("idempotency_keyy",) in [e["loc"] for e in exc.value.errors()]

    def test_unknown_agent_spec_field_rejected(self):
        with pytest.raises(ValidationError) as exc:
            SubmitRunDTO(**_body(agents=[_agent(power=9001)]))
        assert "extra_forbidden" in _error_types(exc)
        assert ("agents", 0, "power") in [e["loc"] for e in exc.value.errors()]


class TestProviderClosedSet:

    @pytest.mark.parametrize("value", ["bogus", "openclaw"])
    def test_unknown_provider_rejected(self, value):
        with pytest.raises(ValidationError) as exc:
            SubmitRunDTO(**_body(provider=value))
        assert ("provider",) in [e["loc"] for e in exc.value.errors()]

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("default", ProviderKind.DEFAULT),
            ("stub", ProviderKind.STUB),
            ("avernet", ProviderKind.AVENET),
        ],
    )
    def test_closed_set_provider_accepted(self, value, expected):
        dto = SubmitRunDTO(**_body(provider=value))
        assert dto.provider is expected

    def test_provider_omitted_is_none(self):
        dto = SubmitRunDTO(**_body())
        assert dto.provider is None


class TestAgentsHeadcountCap:

    def test_sixty_five_agents_rejected(self):
        agents = [_agent(name=f"a{i}", role="research") for i in range(AGENTS_MAX + 1)]
        with pytest.raises(ValidationError) as exc:
            SubmitRunDTO(**_body(agents=agents))
        assert ("agents",) in [e["loc"] for e in exc.value.errors()]

    def test_at_limit_agents_accepted_and_empty_rejected(self):
        agents = [_agent(name=f"a{i}", role="research") for i in range(AGENTS_MAX)]
        dto = SubmitRunDTO(**_body(agents=agents))
        assert len(dto.agents) == AGENTS_MAX
        with pytest.raises(ValidationError):
            SubmitRunDTO(**_body(agents=[]))


class TestIdempotencyKeyCap:

    def test_over_length_key_rejected(self):
        with pytest.raises(ValidationError) as exc:
            SubmitRunDTO(**_body(idempotency_key="k" * (IDEMPOTENCY_KEY_MAX + 1)))
        assert "string_too_long" in {
            err["type"] for err in exc.value.errors()
        }

    def test_at_length_key_accepted(self):
        dto = SubmitRunDTO(**_body(idempotency_key="k" * IDEMPOTENCY_KEY_MAX))
        assert dto.idempotency_key == "k" * IDEMPOTENCY_KEY_MAX


class TestRunOptionCaps:

    def _options(self, **overrides):
        opts = {"model_name": "DeepSeek-V4.1-Flash"}
        opts.update(overrides)
        return opts

    def test_max_turns_999_accepted(self):
        dto = SubmitRunDTO(**_body(options=self._options(max_turns=999)))
        assert dto.options.max_turns == 999

    def test_max_turns_over_999_rejected(self):
        with pytest.raises(ValidationError) as exc:
            SubmitRunDTO(**_body(options=self._options(max_turns=1000)))
        assert ("options", "max_turns") in [e["loc"] for e in exc.value.errors()]

    def test_max_turns_zero_rejected(self):
        with pytest.raises(ValidationError):
            SubmitRunDTO(**_body(options=self._options(max_turns=0)))

    def test_max_total_agents_over_64_rejected(self):
        with pytest.raises(ValidationError) as exc:
            SubmitRunDTO(**_body(options=self._options(max_total_agents=65)))
        assert ("options", "max_total_agents") in [e["loc"] for e in exc.value.errors()]

    def test_per_attempt_timeout_at_1800_accepted_over_rejected(self):
        dto = SubmitRunDTO(**_body(
            options=self._options(per_attempt_timeout_seconds=1800.0)))
        assert dto.options.per_attempt_timeout_seconds == 1800.0
        with pytest.raises(ValidationError):
            SubmitRunDTO(**_body(
                options=self._options(per_attempt_timeout_seconds=1800.5)))

    def test_unknown_option_field_rejected(self):
        with pytest.raises(ValidationError) as exc:
            SubmitRunDTO(**_body(options=self._options(bogus=1)))
        assert "extra_forbidden" in _error_types(exc)


class TestAgentSpecCaps:

    def test_over_length_name_rejected(self):
        with pytest.raises(ValidationError):
            AgentSpecDTO(name="n" * (NAME_MAX + 1), role="research")

    def test_over_length_role_rejected(self):
        with pytest.raises(ValidationError):
            AgentSpecDTO(name="scout", role="r" * (ROLE_MAX + 1))

    def test_over_length_instructions_rejected(self):
        with pytest.raises(ValidationError):
            AgentSpecDTO(
                name="scout", role="research",
                instructions="i" * (INSTRUCTIONS_MAX + 1),
            )

    def test_at_length_spec_accepted(self):
        dto = AgentSpecDTO(
            name="n" * NAME_MAX, role="r" * ROLE_MAX,
            instructions="i" * INSTRUCTIONS_MAX,
        )
        assert len(dto.name) == NAME_MAX
        assert len(dto.role) == ROLE_MAX
        assert len(dto.instructions) == INSTRUCTIONS_MAX


class TestMetadataCaps:

    def test_entry_count_over_cap_rejected(self):
        metadata = {f"k{i}": "v" for i in range(METADATA_MAX_ENTRIES + 1)}
        with pytest.raises(ValidationError) as exc:
            AgentSpecDTO(name="scout", role="research", metadata=metadata)
        assert ("metadata",) in [e["loc"] for e in exc.value.errors()]

    def test_value_over_cap_rejected(self):
        with pytest.raises(ValidationError):
            AgentSpecDTO(
                name="scout", role="research",
                metadata={"k": "v" * (METADATA_VALUE_MAX + 1)},
            )

    def test_key_over_cap_rejected(self):
        with pytest.raises(ValidationError):
            AgentSpecDTO(
                name="scout", role="research",
                metadata={"k" * (METADATA_KEY_MAX + 1): "v"},
            )

    def test_at_cap_metadata_accepted(self):
        metadata = {f"k{i}": "v" for i in range(METADATA_MAX_ENTRIES - 1)}
        metadata["k" * METADATA_KEY_MAX] = "v" * METADATA_VALUE_MAX
        dto = AgentSpecDTO(name="scout", role="research", metadata=metadata)
        assert len(dto.metadata) == METADATA_MAX_ENTRIES


class TestConfigManifest:
    """The per-agent manifest document — ONE total parameter that
    expresses mcp/resources/skills/cli_tools/identity + sources + script
    per agent, instead of inventing a narrow per-agent field."""

    def test_manifest_accepted_and_default_blank(self):
        document = (
            "schema_version: 1\n"
            "manifest:\n"
            "  identity:\n"
            "    - type: SOUL.md\n"
            "      content: 'be meticulous'\n"
            "  mcp: []\n"
            "  skills: []\n"
            "script:\n"
            "  body: 'echo provisioned'\n"
        )
        assert AgentSpecDTO(name="scout", role="research").config_manifest == ""
        dto = AgentSpecDTO(
            name="scout", role="research", config_manifest=document)
        assert dto.config_manifest == document

    def test_over_length_manifest_rejected(self):
        with pytest.raises(ValidationError) as exc:
            AgentSpecDTO(
                name="scout", role="research",
                config_manifest="x" * (CONFIG_MANIFEST_MAX + 1),
            )
        assert "string_too_long" in _error_types(exc)

    def test_at_length_manifest_accepted(self):
        dto = AgentSpecDTO(
            name="scout", role="research",
            config_manifest="x" * CONFIG_MANIFEST_MAX,
        )
        assert len(dto.config_manifest) == CONFIG_MANIFEST_MAX

    def test_unknown_agent_field_still_refused_beside_manifest(self):
        # extra="forbid" must hold after the field addition: a typo'd
        # neighbor is 422, not silently ignored.
        with pytest.raises(ValidationError) as exc:
            SubmitRunDTO(**_body(
                agents=[_agent(config_manifest="schema_version: 1",
                               power=9001)]))
        assert "extra_forbidden" in _error_types(exc)
        assert ("agents", 0, "power") in [e["loc"] for e in exc.value.errors()]

    def test_manifest_flows_into_domain_spec(self):
        # The router's DTO→domain mapping (router._to_domain) carries the
        # document through: it is the wire value the host adapter later
        # submits verbatim.
        from agentclaw.community.adapters.http.agentswarm.router import (
            _to_domain,
        )
        document = "schema_version: 1\nscript:\n  body: 'echo x'\n"
        req = _to_domain(SubmitRunDTO(
            tenant="t",
            goal="summarize the incident timeline",
            agents=[_agent(config_manifest=document)],
        ))
        assert req.agents[0].config_manifest == document
