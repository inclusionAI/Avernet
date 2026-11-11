"""Call-site tests for identity inheritance.

The helpers are covered directly in ``test_identity_source``; this file pins
that each *call site* actually reaches them -- a helper that is correct but
never called is exactly the silent-defect shape this feature must avoid.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.create_flow import (
    complete_manifest_creation,
)
from agentclaw.community.core.bot_management.errors import IdentitySourceInvalidError


def _job_payload(identity_bot_id: str | None) -> dict:
    """A payload shaped exactly as ``creation_spec_to_payload`` freezes it."""
    from agentclaw.community.core.bot_management.create_context import (
        BotCreateContext,
        BotCreateDeploymentMode,
        BotCreateSpec,
    )
    from agentclaw.community.core.bot_management.create_flow import (
        creation_spec_to_payload,
    )

    spec = BotCreateSpec(
        entity_id="owner-1",
        entity_type="staff",
        engine_type="openclaw",
        bot_type="personal",
        bot_name="bot",
        bot_desc="desc",
        share_policy={},
        identity_bot_id=identity_bot_id,
    )
    context = BotCreateContext(
        deployment_mode=BotCreateDeploymentMode.CLOUD,
        space_kind="personal",
        space_quota=False,
    )
    return {
        "user_id": "owner-1",
        "bot_id": "child",
        "spec": creation_spec_to_payload(spec, context),
    }


class TestCompleteManifestCreationSkipsOnlyThePoll:
    def _run(self, identity_bot_id):
        from agentclaw.community.core.bot_management.create_flow import (
            complete_manifest_creation,
        )

        captured = {}

        def fake_complete(**kwargs):
            captured.update(kwargs)
            return MagicMock(status="ISSUED")

        orig = complete_manifest_creation.__globals__["complete_bot_authorization"]
        complete_manifest_creation.__globals__["complete_bot_authorization"] = fake_complete
        try:
            complete_manifest_creation(
                _job_payload(identity_bot_id),
                bot_service=MagicMock(),
                passport_plugin=MagicMock(),
                auth_rel_plugin=MagicMock(),
            )
        finally:
            complete_manifest_creation.__globals__["complete_bot_authorization"] = orig
        return captured

    def test_an_inheriting_bot_declares_itself_authorized(self):
        assert self._run("src")["authorized"] is True

    def test_an_ordinary_bot_still_polls_passport(self):
        assert self._run(None)["authorized"] is False

    def test_the_same_completion_path_is_used_for_both(self):
        """Inheritance must not fork the completion, only skip the poll."""
        inheriting = self._run("src")
        ordinary = self._run(None)
        assert set(inheriting) == set(ordinary)


class TestScopeIsCheckedAgainstTheSourcePassport:
    def test_a_declared_entry_the_source_lacks_is_refused(self):
        from agentclaw.community.core.bot_management.identity_source import (
            identity_scope_violations,
        )

        violations = identity_scope_violations(
            parsed_manifest={"mcp": [{"server_code": "jira"}]},
            source_passport={"mcps": [{"server_code": "github"}]},
        )
        assert violations == {"mcp:jira"}

    def test_the_refusal_names_every_offending_entry(self):
        """A single rejection lists all violations, so one round trip suffices."""
        from agentclaw.community.core.bot_management.identity_source import (
            identity_scope_violations,
        )

        violations = identity_scope_violations(
            parsed_manifest={
                "mcp": [{"server_code": "jira"}, {"server_code": "notion"}],
                "cli_tools": [{"name": "kubectl"}],
            },
            source_passport={"mcps": [], "clis": []},
        )
        assert ", ".join(sorted(violations)) == "cli:kubectl, mcp:jira, mcp:notion"


class TestJobStillRunsThePreContainerPhase:
    """Skipping the authorization wait must not skip provisioning input.

    Creation composes the start command from the startup-script row that the
    pre-container phase writes, so an inheriting Bot must still wait for that
    phase -- only the passport authorization is skipped.
    """

    def _handler(self, *, authorized: bool):
        from agentclaw.community.core.bot_config_manifest.create_job import (
            BotCreateWithManifestHandler,
        )

        h = BotCreateWithManifestHandler.__new__(BotCreateWithManifestHandler)
        h._bots_provider = lambda: MagicMock(
            get_by_id_and_entity=MagicMock(return_value=None)
        )
        h._seam = MagicMock(verify_binding=MagicMock())
        h._seam_provider = lambda: h._seam
        h._authorization_status = MagicMock(side_effect=AssertionError(
            "must not poll authorization"))
        h._pre_container_record = MagicMock(return_value=None)
        h._create_the_bot = MagicMock()
        h._creation_sequence = lambda spec: "CREATE_BETWEEN_PHASES"
        payload = {
            "bot_id": "child",
            "entity_id": "owner-1",
            "user_id": "owner-1",
            "spec": {"identity_bot_id": "src"} if authorized else {},
        }
        return h, payload

    def test_an_inheriting_bot_applies_the_pre_container_phase_first(self):
        h, payload = self._handler(authorized=True)
        h._before_the_bot_exists(payload, authorized=True)
        h._create_the_bot.assert_not_called()
        assert h._seam.apply_pre_container.call_args.kwargs["bot_id"] == "child"

    def test_an_inheriting_bot_creates_once_the_phase_is_terminal(self):
        h, payload = self._handler(authorized=True)
        h._pre_container_record = MagicMock(return_value=MagicMock(status="SUCCEEDED"))
        h._before_the_bot_exists(payload, authorized=True)
        h._create_the_bot.assert_called_once_with(payload)

    def test_an_inheriting_bot_never_reads_authorization(self):
        h, payload = self._handler(authorized=True)
        h._before_the_bot_exists(payload, authorized=True)
        h._authorization_status.assert_not_called()


def test_identity_source_error_is_a_bot_create_error():
    """The surface maps it to 422, so it must stay in that hierarchy."""
    from agentclaw.community.core.bot_management.errors import BotCreateError

    assert issubclass(IdentitySourceInvalidError, BotCreateError)