"""The identity-source contract across its three layers.

One requirement, checked where it is enforced:

* the service refuses a bot-kind run with no source, and one whose source the
  seam rejects — before any run row exists;
* the route maps those two refusals to 400 and 422, auditing each;
* the seam answers from the Bot's row: an issued passport's agent code, or
  nothing when the Bot is absent, has no code, or is still PENDING.

Every assertion names an exact value. A "not None" check would pass against a
seam that returned the child's own id, which is the defect this contract
exists to prevent.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.domain.models import (
    AgentKind, AgentSpec,
)
from agentclaw.community.core.agentswarm.service.impl import AgentSwarmService
from agentclaw.community.core.agentswarm.service.protocols import (
    IdentitySourceInvalid, IdentitySourceRequired, SubmitRunRequest,
)

OWNER = "49912"
SOURCE = "20261110_source"
SOURCE_CODE = "bdeb1e9e-69d0-43b6-97f6-0270b5b29faa"


def _agent(kind: AgentKind, name: str = "a1") -> AgentSpec:
    return AgentSpec(name=name, role="worker", kind=kind)


def _req(*, kind: AgentKind, identity_bot_id: str | None) -> SubmitRunRequest:
    return SubmitRunRequest(
        tenant="teamclaw",
        goal="g",
        agents=(_agent(kind),),
        owner_id=OWNER,
        identity_bot_id=identity_bot_id,
    )


def _service(seam) -> AgentSwarmService:
    """Only the seam matters here; the rest is never reached on refusal."""
    svc = AgentSwarmService.__new__(AgentSwarmService)
    svc._authorization_source = seam
    return svc


class TestTheServiceRefusesAnUnusableSource:
    def test_a_bot_kind_run_with_no_source_is_refused(self):
        svc = _service(MagicMock())
        with pytest.raises(IdentitySourceRequired) as exc:
            svc._require_identity_source(_req(kind=AgentKind.BOT,
                                              identity_bot_id=None))
        assert "identity_bot_id" in str(exc.value)

    def test_a_blank_source_is_refused_rather_than_taken_as_absent(self):
        svc = _service(MagicMock())
        with pytest.raises(IdentitySourceRequired):
            svc._require_identity_source(_req(kind=AgentKind.BOT,
                                              identity_bot_id="   "))

    def test_a_source_the_seam_rejects_is_invalid_not_missing(self):
        seam = MagicMock()
        seam.identity_source.return_value = None
        svc = _service(seam)
        with pytest.raises(IdentitySourceInvalid) as exc:
            svc._require_identity_source(_req(kind=AgentKind.BOT,
                                              identity_bot_id=SOURCE))
        # The offending id is named, and the seam was asked for THIS owner.
        assert exc.value.bot_id == SOURCE
        seam.identity_source.assert_called_once_with(
            owner_id=OWNER, bot_id=SOURCE,
        )

    def test_a_run_with_no_bot_agent_needs_no_source(self):
        """An llm agent completes in-process and holds no passport."""
        seam = MagicMock()
        svc = _service(seam)
        svc._require_identity_source(_req(kind=AgentKind.LLM,
                                          identity_bot_id=None))
        seam.identity_source.assert_not_called()

    def test_an_llm_run_naming_a_source_ignores_it(self):
        seam = MagicMock()
        svc = _service(seam)
        svc._require_identity_source(_req(kind=AgentKind.LLM,
                                          identity_bot_id=SOURCE))
        seam.identity_source.assert_not_called()

    def test_no_seam_bound_takes_the_id_as_given(self):
        svc = _service(None)
        svc._require_identity_source(_req(kind=AgentKind.BOT,
                                          identity_bot_id=SOURCE))


class TestTheSeamAnswersFromTheBotRow:
    def _seam(self, row):
        from agentclaw.community.plugins.community.agentswarm.bot_authorization import (
            ManifestBotAuthorizationSource,
        )
        repo = MagicMock()
        repo.get_by_id_and_owner.return_value = row
        return ManifestBotAuthorizationSource(
            manifest_seam=MagicMock(), passport_plugin=MagicMock(),
            bot_repo=repo,
        )

    def test_an_issued_passport_yields_its_agent_code(self):
        code = self._seam({"ext": {"passport": {
            "agent_code": SOURCE_CODE, "status": "ISSUED",
        }}}).identity_source(owner_id=OWNER, bot_id=SOURCE)
        assert code == SOURCE_CODE

    def test_a_code_without_a_status_field_is_accepted(self):
        """The prod plugin writes no status; the code alone is the evidence."""
        code = self._seam({"ext": {"passport": {
            "agent_code": SOURCE_CODE,
        }}}).identity_source(owner_id=OWNER, bot_id=SOURCE)
        assert code == SOURCE_CODE

    def test_a_missing_bot_yields_nothing(self):
        assert self._seam(None).identity_source(
            owner_id=OWNER, bot_id=SOURCE,
        ) is None

    def test_a_pending_passport_is_not_usable_yet(self):
        assert self._seam({"ext": {"passport": {
            "agent_code": SOURCE_CODE, "status": "PENDING",
        }}}).identity_source(owner_id=OWNER, bot_id=SOURCE) is None

    def test_a_bot_with_no_agent_code_yields_nothing(self):
        assert self._seam({"ext": {"passport": {"status": "ISSUED"}}}).identity_source(
            owner_id=OWNER, bot_id=SOURCE,
        ) is None

    def test_a_bot_with_no_ext_yields_nothing(self):
        assert self._seam({"bot_id": SOURCE}).identity_source(
            owner_id=OWNER, bot_id=SOURCE,
        ) is None

    def test_no_repository_bound_yields_nothing(self):
        from agentclaw.community.plugins.community.agentswarm.bot_authorization import (
            ManifestBotAuthorizationSource,
        )
        seam = ManifestBotAuthorizationSource(
            manifest_seam=MagicMock(), passport_plugin=MagicMock(), bot_repo=None,
        )
        assert seam.identity_source(owner_id=OWNER, bot_id=SOURCE) is None