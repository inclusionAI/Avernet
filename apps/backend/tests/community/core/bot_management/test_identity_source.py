"""Unit tests for identity inheritance on a manifest creation.

Every assertion names an exact value: the self-issued passport derives an
agent code from *any* id, so a loose "not empty" check would pass against the
very defect these tests exist to catch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.errors import IdentitySourceInvalidError
from agentclaw.community.core.bot_management.identity_source import (
    identity_scope_violations,
    inherited_agent_code,
    resolve_identity_source,
)


_MOD = "agentclaw.community.core.bot_management.identity_source"


@dataclass
class _Spec:
    identity_bot_id: str | None = None
    entity_id: str = "owner-1"
    bot_name: str | None = "bot"
    bot_desc: str | None = "desc"
    entity_type: str = "staff"
    share_policy: dict[str, Any] = field(default_factory=dict)
    engine_type: str = "openclaw"
    bot_type: str = "personal"
    template_type: str | None = None
    template_config: dict[str, Any] | None = None
    space_id: int | None = None
    avatar_url: str | None = None
    expert_origin: Any = None


def _issued_source(agent_code: str = "source-agent") -> dict[str, Any]:
    return {"bot_id": "src", "entity_id": "owner-1",
            "ext": {"passport": {"status": "ISSUED", "agent_code": agent_code}}}


def _service(source: dict[str, Any] | None) -> Mock:
    svc = Mock()
    svc.get_identity_source_row.return_value = source
    return svc


class _Provider:
    """A passport provider stand-in that records what it was asked.

    ``answers`` maps bot id → the facade's answer; a missing bot id means the
    provider has no passport for it, which is the "nothing anywhere" case the
    refusal wording is about.
    """

    def __init__(self, answers: dict[str, dict[str, Any]] | None = None) -> None:
        self.answers = answers or {}
        self.queries: list[tuple[str, str]] = []

    def query_agent_passport(self, bot_id: str, owner_workno: str) -> dict[str, Any] | None:
        self.queries.append((bot_id, owner_workno))
        return self.answers.get(bot_id)


class _RaisingProvider:
    def query_agent_passport(self, bot_id: str, owner_workno: str):
        raise RuntimeError("tcauthmng unreachable")


# ── resolve_identity_source ──────────────────────────────────


class TestResolveIdentitySource:
    def test_ordinary_creation_returns_none_without_looking_anything_up(self):
        svc = _service(_issued_source())
        assert resolve_identity_source(spec=_Spec(), bot_service=svc) is None
        svc.get_identity_source_row.assert_not_called()

    def test_blank_source_is_refused_rather_than_treated_as_ordinary(self):
        svc = _service(_issued_source())
        with pytest.raises(IdentitySourceInvalidError) as exc:
            resolve_identity_source(spec=_Spec(identity_bot_id="   "), bot_service=svc)
        assert str(exc.value) == "identity_bot_id must not be blank"
        svc.get_identity_source_row.assert_not_called()

    def test_source_must_belong_to_the_same_entity(self):
        svc = _service(None)
        with pytest.raises(IdentitySourceInvalidError) as exc:
            resolve_identity_source(spec=_Spec(identity_bot_id="src"), bot_service=svc)
        assert str(exc.value) == (
            "identity source 'src' is not a Bot owned by 'owner-1'"
        )
        svc.get_identity_source_row.assert_called_once_with("src", entity_id="owner-1")

    @pytest.mark.parametrize(
        "passport",
        [
            {},
            {"status": "PENDING", "agent_code": "x"},
            {"status": "ISSUED", "agent_code": ""},
            {"status": "ISSUED", "agent_code": "   "},
        ],
    )
    def test_source_must_hold_an_issued_non_empty_agent_code(self, passport):
        """Neither the cache nor the provider has a code for it."""
        svc = _service({"bot_id": "src", "ext": {"passport": passport}})
        with pytest.raises(IdentitySourceInvalidError) as exc:
            resolve_identity_source(
                spec=_Spec(identity_bot_id="src"), bot_service=svc,
                passport_plugin=_Provider(),
            )
        assert str(exc.value) == (
            "identity source 'src' holds no issued passport; "
            "it cannot supply an agent code"
        )

    def test_a_code_without_a_status_field_is_accepted(self):
        """The prod plugin writes no status; a code alone is an issued passport."""
        svc = _service({"bot_id": "src",
                        "ext": {"passport": {"agent_code": "fcfab821-6751"}}})
        assert resolve_identity_source(
            spec=_Spec(identity_bot_id="src"), bot_service=svc
        ) is not None

    def test_missing_ext_is_not_an_issued_passport(self):
        svc = _service({"bot_id": "src"})
        with pytest.raises(IdentitySourceInvalidError):
            resolve_identity_source(
                spec=_Spec(identity_bot_id="src"), bot_service=svc,
                passport_plugin=_Provider(),
            )

    def test_valid_source_is_returned_verbatim(self):
        source = _issued_source("abc")
        svc = _service(source)
        assert resolve_identity_source(
            spec=_Spec(identity_bot_id="src"), bot_service=svc
        ) is source

    def test_surrounding_whitespace_is_trimmed_before_lookup(self):
        svc = _service(_issued_source())
        resolve_identity_source(spec=_Spec(identity_bot_id="  src  "), bot_service=svc)
        svc.get_identity_source_row.assert_called_once_with("src", entity_id="owner-1")


# ── identity_scope_violations ────────────────────────────────


class TestIdentityScopeViolations:
    def test_a_subset_reports_nothing(self):
        assert identity_scope_violations(
            parsed_manifest={
                "mcp": [{"server_code": "github"}],
                "cli_tools": [{"name": "git"}],
            },
            source_passport={
                "mcps": [{"server_code": "github"}, {"server_code": "slack"}],
                "clis": [{"cli_code": "git"}, {"cli_code": "npm"}],
            },
        ) == set()

    def test_mcp_and_cli_beyond_the_source_are_both_named(self):
        assert identity_scope_violations(
            parsed_manifest={
                "mcp": [{"server_code": "github"}, {"server_code": "jira"}],
                "cli_tools": [{"name": "git"}, {"name": "kubectl"}],
            },
            source_passport={
                "mcps": [{"server_code": "github"}],
                "clis": [{"cli_code": "git"}],
            },
        ) == {"mcp:jira", "cli:kubectl"}

    def test_an_empty_source_passport_rejects_every_declared_entry(self):
        assert identity_scope_violations(
            parsed_manifest={
                "mcp": [{"server_code": "github"}],
                "cli_tools": [{"name": "git"}],
            },
            source_passport=None,
        ) == {"mcp:github", "cli:git"}

    def test_entries_without_an_identifier_are_ignored(self):
        assert identity_scope_violations(
            parsed_manifest={"mcp": [{}, "nonsense"], "cli_tools": [{"name": None}]},
            source_passport=None,
        ) == set()

    def test_mcp_items_alias_is_read_when_mcps_is_absent(self):
        assert identity_scope_violations(
            parsed_manifest={"mcp": [{"server_code": "github"}]},
            source_passport={"mcp_items": [{"server_code": "github"}]},
        ) == set()

    def test_a_manifest_with_no_declared_scope_reports_nothing(self):
        assert identity_scope_violations(
            parsed_manifest={}, source_passport={"mcps": [{"server_code": "x"}]}
        ) == set()


# ── inherited_agent_code ─────────────────────────────────────


class TestInheritedAgentCode:
    def test_returns_the_source_agent_code_exactly(self):
        svc = _service(_issued_source("source-agent"))
        assert inherited_agent_code(
            spec=_Spec(identity_bot_id="src"), bot_service=svc
        ) == "source-agent"

    def test_never_returns_the_inheriting_bots_own_id(self):
        svc = _service(_issued_source("source-agent"))
        code = inherited_agent_code(spec=_Spec(identity_bot_id="src"), bot_service=svc)
        assert code == "source-agent"
        assert code != "src"

    def test_a_vanished_source_raises(self):
        svc = _service(None)
        with pytest.raises(IdentitySourceInvalidError) as exc:
            inherited_agent_code(spec=_Spec(identity_bot_id="src"), bot_service=svc)
        assert str(exc.value) == "identity source 'src' is no longer available"

    def test_a_source_without_an_agent_code_raises(self):
        svc = _service({"bot_id": "src", "ext": {"passport": {"status": "ISSUED"}}})
        with pytest.raises(IdentitySourceInvalidError) as exc:
            inherited_agent_code(
                spec=_Spec(identity_bot_id="src"), bot_service=svc,
                passport_plugin=_Provider(),
            )
        assert str(exc.value) == "identity source 'src' has no agent code"


# ── the provider fallback at the creation boundaries ─────────

PROVIDER_CODE = "bdeb1e9e-69d0-43b6-97f6-0270b5b29faa"


class TestCreationBoundariesReachTheProvider:
    """A Bot accepted by the agentswarm submit must also be creatable.

    Before this, both creation checks read only ``ext.passport``, so a run for
    a Bot with no cached copy passed submit and then failed as it spawned —
    ``identity source ... holds no issued passport`` burned all three attempts.
    """

    def test_the_provider_is_asked_about_the_requesting_entity_at_both_boundaries(self):
        """One spelling for the owner everywhere: the requester's entity.

        The seam asks the provider about the run's owner; these boundaries must
        not reach it with a different id for the same Bot.
        """
        svc = _service({"bot_id": "src", "entity_id": "owner-1", "ext": None})
        provider = _Provider({"src": {"agent_code": PROVIDER_CODE}})

        resolve_identity_source(
            spec=_Spec(identity_bot_id="src"), bot_service=svc,
            passport_plugin=provider,
        )
        inherited_agent_code(
            spec=_Spec(identity_bot_id="src"), bot_service=svc,
            passport_plugin=provider,
        )

        assert provider.queries == [("src", "owner-1"), ("src", "owner-1")]

    def test_resolve_accepts_a_bot_whose_passport_the_provider_holds(self):
        svc = _service({"bot_id": "src", "entity_id": "owner-1", "ext": None})
        provider = _Provider({"src": {
            "agent_code": PROVIDER_CODE, "status": "ISSUED",
        }})

        source = resolve_identity_source(
            spec=_Spec(identity_bot_id="src"), bot_service=svc,
            passport_plugin=provider,
        )

        assert source is not None
        assert provider.queries == [("src", "owner-1")]

    def test_resolve_accepts_a_provider_answer_without_a_status_field(self):
        """The prod facade sends no status; the code alone is the evidence."""
        svc = _service({"bot_id": "src", "entity_id": "owner-1"})
        provider = _Provider({"src": {"agent_code": PROVIDER_CODE}})

        assert resolve_identity_source(
            spec=_Spec(identity_bot_id="src"), bot_service=svc,
            passport_plugin=provider,
        ) is not None

    def test_inherited_code_comes_from_the_provider_when_the_cache_is_silent(self):
        svc = _service({"bot_id": "src", "entity_id": "owner-1", "ext": None})
        provider = _Provider({"src": {"agent_code": PROVIDER_CODE}})

        assert inherited_agent_code(
            spec=_Spec(identity_bot_id="src"), bot_service=svc,
            passport_plugin=provider,
        ) == PROVIDER_CODE

    def test_the_cache_answers_without_the_provider_at_both_boundaries(self):
        source = _issued_source("cached-code")
        svc = _service(source)
        provider = _Provider({"src": {"agent_code": PROVIDER_CODE}})

        assert resolve_identity_source(
            spec=_Spec(identity_bot_id="src"), bot_service=svc,
            passport_plugin=provider,
        ) is source
        assert inherited_agent_code(
            spec=_Spec(identity_bot_id="src"), bot_service=svc,
            passport_plugin=provider,
        ) == "cached-code"
        assert provider.queries == []

    def test_a_cached_pending_passport_refuses_without_the_provider(self):
        source = {"bot_id": "src", "entity_id": "owner-1",
                  "ext": {"passport": {"status": "PENDING",
                                       "agent_code": "cached"}}}
        svc = _service(source)
        provider = _Provider({"src": {"agent_code": PROVIDER_CODE}})

        with pytest.raises(IdentitySourceInvalidError):
            resolve_identity_source(
                spec=_Spec(identity_bot_id="src"), bot_service=svc,
                passport_plugin=provider,
            )
        with pytest.raises(IdentitySourceInvalidError):
            inherited_agent_code(
                spec=_Spec(identity_bot_id="src"), bot_service=svc,
                passport_plugin=provider,
            )
        assert provider.queries == []

    def test_an_unowned_bot_never_reaches_the_provider(self):
        svc = _service(None)
        provider = _Provider({"src": {"agent_code": PROVIDER_CODE}})

        with pytest.raises(IdentitySourceInvalidError):
            resolve_identity_source(
                spec=_Spec(identity_bot_id="src"), bot_service=svc,
                passport_plugin=provider,
            )
        assert provider.queries == []

    def test_a_provider_failure_refuses_rather_than_inventing_an_identity(self):
        svc = _service({"bot_id": "src", "entity_id": "owner-1", "ext": None})
        provider = _RaisingProvider()

        with pytest.raises(IdentitySourceInvalidError) as create_exc:
            resolve_identity_source(
                spec=_Spec(identity_bot_id="src"), bot_service=svc,
                passport_plugin=provider,
            )
        assert "could not be verified" in str(create_exc.value)

        with pytest.raises(IdentitySourceInvalidError) as code_exc:
            inherited_agent_code(
                spec=_Spec(identity_bot_id="src"), bot_service=svc,
                passport_plugin=provider,
            )
        assert "could not be verified" in str(code_exc.value)

    def test_no_provider_bound_is_unverified_not_no_passport(self):
        """Without a provider the answer is unknown, not a verdict."""
        svc = _service({"bot_id": "src", "entity_id": "owner-1", "ext": None})

        with pytest.raises(IdentitySourceInvalidError) as exc:
            resolve_identity_source(
                spec=_Spec(identity_bot_id="src"), bot_service=svc,
            )
        assert "could not be verified" in str(exc.value)
        assert "holds no issued passport" not in str(exc.value)
