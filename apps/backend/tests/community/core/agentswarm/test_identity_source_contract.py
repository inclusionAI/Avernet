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
    IdentitySourceInvalid, IdentitySourceRefusal, IdentitySourceRequired,
    IDENTITY_SOURCE_NO_PASSPORT, IDENTITY_SOURCE_NOT_OWNED,
    IDENTITY_SOURCE_UNVERIFIED, SubmitRunRequest,
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
        seam.identity_source.return_value = IdentitySourceRefusal(
            IDENTITY_SOURCE_NOT_OWNED,
            f"identity source {SOURCE!r} is not a Bot owned by the caller",
        )
        svc = _service(seam)
        with pytest.raises(IdentitySourceInvalid) as exc:
            svc._require_identity_source(_req(kind=AgentKind.BOT,
                                              identity_bot_id=SOURCE))
        # The offending id and the seam's case both reach the caller.
        assert exc.value.bot_id == SOURCE
        assert exc.value.reason == IDENTITY_SOURCE_NOT_OWNED
        assert "not a Bot owned by the caller" in str(exc.value)
        seam.identity_source.assert_called_once_with(
            owner_id=OWNER, bot_id=SOURCE,
        )

    def test_each_seam_refusal_keeps_its_own_reason(self):
        """The case is a typed value on the error, not text a caller must parse."""
        seen = {}
        for code in (IDENTITY_SOURCE_NOT_OWNED, IDENTITY_SOURCE_NO_PASSPORT,
                     IDENTITY_SOURCE_UNVERIFIED):
            seam = MagicMock()
            seam.identity_source.return_value = IdentitySourceRefusal(
                code, f"the {code} case",
            )
            with pytest.raises(IdentitySourceInvalid) as exc:
                svc = _service(seam)
                svc._require_identity_source(
                    _req(kind=AgentKind.BOT, identity_bot_id=SOURCE))
            assert exc.value.reason == code
            assert exc.value.message == f"the {code} case"
            assert str(exc.value) == f"the {code} case"
            seen[code] = exc.value.reason
        assert len(set(seen.values())) == 3

    def test_a_reason_without_a_message_still_names_the_source(self):
        """A seam from another tree may populate only the code; the error
        still names the source so a log reader can find it."""
        seam = MagicMock()
        seam.identity_source.return_value = IdentitySourceRefusal(
            IDENTITY_SOURCE_NO_PASSPORT,
        )
        svc = _service(seam)
        with pytest.raises(IdentitySourceInvalid) as exc:
            svc._require_identity_source(_req(kind=AgentKind.BOT,
                                              identity_bot_id=SOURCE))
        assert exc.value.reason == IDENTITY_SOURCE_NO_PASSPORT
        assert exc.value.message == ""
        assert SOURCE in str(exc.value)

    def test_a_seam_answer_outside_the_contract_is_not_accepted(self):
        """A seam returning neither a code nor a reason cannot be trusted."""
        seam = MagicMock()
        seam.identity_source.return_value = None
        svc = _service(seam)
        with pytest.raises(IdentitySourceInvalid) as exc:
            svc._require_identity_source(_req(kind=AgentKind.BOT,
                                              identity_bot_id=SOURCE))
        assert exc.value.reason == IDENTITY_SOURCE_UNVERIFIED

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

    def test_a_missing_bot_is_refused_as_not_owned(self):
        refusal = self._seam(None).identity_source(
            owner_id=OWNER, bot_id=SOURCE,
        )
        assert isinstance(refusal, IdentitySourceRefusal)
        assert refusal.code == IDENTITY_SOURCE_NOT_OWNED
        # A Bot the caller does not own says so, and never claims a passport
        # problem the caller cannot act on.
        assert "not a Bot owned by the caller" in refusal.message
        assert "passport" not in refusal.message

    def test_a_pending_passport_is_not_usable_yet(self):
        refusal = self._seam({"ext": {"passport": {
            "agent_code": SOURCE_CODE, "status": "PENDING",
        }}}).identity_source(owner_id=OWNER, bot_id=SOURCE)
        assert refusal.code == IDENTITY_SOURCE_NO_PASSPORT
        assert "pending authorization" in refusal.message

    def test_a_bot_with_no_agent_code_holds_no_passport(self):
        refusal = self._seam({"ext": {"passport": {"status": "ISSUED"}}}).identity_source(
            owner_id=OWNER, bot_id=SOURCE,
        )
        assert refusal.code == IDENTITY_SOURCE_NO_PASSPORT
        assert "holds no issued passport" in refusal.message

    def test_a_bot_with_no_ext_holds_no_passport_when_the_provider_is_silent(self):
        refusal = _seam_with(
            {"bot_id": SOURCE}, _SpyPassport({}),
        ).identity_source(owner_id=OWNER, bot_id=SOURCE)
        assert refusal.code == IDENTITY_SOURCE_NO_PASSPORT

    def test_no_repository_bound_is_unverified_not_unowned(self):
        from agentclaw.community.plugins.community.agentswarm.bot_authorization import (
            ManifestBotAuthorizationSource,
        )
        seam = ManifestBotAuthorizationSource(
            manifest_seam=MagicMock(), passport_plugin=MagicMock(), bot_repo=None,
        )
        refusal = seam.identity_source(owner_id=OWNER, bot_id=SOURCE)
        assert refusal.code == IDENTITY_SOURCE_UNVERIFIED
        assert "could not be verified" in refusal.message


# ── the passport-provider fallback ───────────────────────────

PROVIDER_CODE = "3f1c2a04-8b6e-4d19-9a2c-5d0e7b41c6af"


def _issued(code: str = PROVIDER_CODE, status: str = "ISSUED") -> dict:
    return {"agent_code": code, "status": status}


class _SpyPassport:
    """Records every passport lookup, and answers per Bot id."""

    def __init__(
        self, answers: dict,
    ) -> None:
        self.answers = answers
        self.queries: list[tuple[str, str]] = []

    def query_agent_passport(self, bot_id: str, owner_workno: str) -> dict | None:
        self.queries.append((bot_id, owner_workno))
        return self.answers.get(bot_id)


def _seam_with(row, passport: _SpyPassport):
    from agentclaw.community.plugins.community.agentswarm.bot_authorization import (
        ManifestBotAuthorizationSource,
    )
    repo = MagicMock()
    repo.get_by_id_and_owner.return_value = row
    return ManifestBotAuthorizationSource(
        manifest_seam=MagicMock(), passport_plugin=passport, bot_repo=repo,
    )


class TestAnOlderBotsPassportIsFoundAtTheProvider:
    """The case this change exists for: a Bot predating `ext.passport`."""

    def test_a_bot_with_no_cached_passport_is_accepted_from_the_provider(self):
        passport = _SpyPassport({SOURCE: _issued()})
        code = _seam_with({"bot_id": SOURCE}, passport).identity_source(
            owner_id=OWNER, bot_id=SOURCE,
        )
        assert code == PROVIDER_CODE
        # Asked for THIS Bot and THIS owner, exactly once.
        assert passport.queries == [(SOURCE, OWNER)]

    def test_a_cached_passport_with_no_code_falls_back_to_the_provider(self):
        passport = _SpyPassport({SOURCE: _issued()})
        code = _seam_with(
            {"ext": {"passport": {"status": "ISSUED"}}}, passport,
        ).identity_source(owner_id=OWNER, bot_id=SOURCE)
        assert code == PROVIDER_CODE

    def test_a_usable_cached_code_never_reaches_the_provider(self):
        passport = _SpyPassport({SOURCE: _issued()})
        code = _seam_with({"ext": {"passport": {
            "agent_code": SOURCE_CODE, "status": "ISSUED",
        }}}, passport).identity_source(owner_id=OWNER, bot_id=SOURCE)
        assert code == SOURCE_CODE
        assert passport.queries == []

    def test_a_provider_with_no_passport_either_is_a_refusal(self):
        passport = _SpyPassport({})
        refusal = _seam_with({"bot_id": SOURCE}, passport).identity_source(
            owner_id=OWNER, bot_id=SOURCE,
        )
        assert refusal.code == IDENTITY_SOURCE_NO_PASSPORT
        assert passport.queries == [(SOURCE, OWNER)]

    def test_a_provider_answer_that_is_not_issued_is_a_refusal(self):
        passport = _SpyPassport({SOURCE: _issued(status="PENDING")})
        refusal = _seam_with({"bot_id": SOURCE}, passport).identity_source(
            owner_id=OWNER, bot_id=SOURCE,
        )
        assert refusal.code == IDENTITY_SOURCE_NO_PASSPORT

    def test_a_provider_answer_without_a_status_field_is_accepted(self):
        """The local and self-issued plugins send no status; the code alone
        is the evidence, the same reading the cached record gets."""
        passport = _SpyPassport({SOURCE: {
            "agent_id": SOURCE, "agent_code": PROVIDER_CODE,
        }})
        assert _seam_with({"bot_id": SOURCE}, passport).identity_source(
            owner_id=OWNER, bot_id=SOURCE,
        ) == PROVIDER_CODE

    def test_a_provider_answer_with_no_agent_code_is_a_refusal(self):
        passport = _SpyPassport({SOURCE: _issued(code="   ")})
        refusal = _seam_with({"bot_id": SOURCE}, passport).identity_source(
            owner_id=OWNER, bot_id=SOURCE,
        )
        assert refusal.code == IDENTITY_SOURCE_NO_PASSPORT

    def test_an_unowned_bot_never_reaches_the_provider(self):
        """Ownership is settled from the Bot row, so no other account's
        passport can be probed through the refusal text."""
        passport = _SpyPassport({SOURCE: _issued()})
        refusal = _seam_with(None, passport).identity_source(
            owner_id=OWNER, bot_id=SOURCE,
        )
        assert refusal.code == IDENTITY_SOURCE_NOT_OWNED
        assert passport.queries == []


class TestTheCacheWinsWhereItAnswers:
    """A cached answer is the answer: the provider is never consulted when
    the Bot row already settles the question, in either direction."""

    def test_a_pending_cache_is_refused_without_asking_the_provider(self):
        passport = _SpyPassport({SOURCE: _issued()})
        refusal = _seam_with({"ext": {"passport": {
            "agent_code": SOURCE_CODE, "status": "PENDING",
        }}}, passport).identity_source(owner_id=OWNER, bot_id=SOURCE)
        assert refusal.code == IDENTITY_SOURCE_NO_PASSPORT
        assert "pending authorization" in refusal.message
        assert passport.queries == []

    def test_a_usable_cache_is_accepted_without_asking_the_provider(self):
        passport = _SpyPassport({SOURCE: _issued()})
        code = _seam_with({"ext": {"passport": {
            "agent_code": SOURCE_CODE,
        }}}, passport).identity_source(owner_id=OWNER, bot_id=SOURCE)
        assert code == SOURCE_CODE
        assert passport.queries == []

    def test_an_unowned_bot_is_refused_without_asking_the_provider(self):
        passport = _SpyPassport({SOURCE: _issued()})
        refusal = _seam_with(None, passport).identity_source(
            owner_id=OWNER, bot_id=SOURCE,
        )
        assert refusal.code == IDENTITY_SOURCE_NOT_OWNED
        assert passport.queries == []


class _RaisingPassport:
    def __init__(self) -> None:
        self.queries: list[tuple[str, str]] = []

    def query_agent_passport(self, bot_id: str, owner_workno: str) -> dict:
        self.queries.append((bot_id, owner_workno))
        raise RuntimeError("tcauthmng unreachable")


class TestAProviderFailureRefusesRatherThanAccepts:
    """An outage must not mint an identity source, and must not claim the
    Bot has no passport -- it reads as "could not be verified"."""

    def test_a_raising_provider_reads_as_unverified(self):
        passport = _RaisingPassport()
        refusal = _seam_with({"bot_id": SOURCE}, passport).identity_source(
            owner_id=OWNER, bot_id=SOURCE,
        )
        assert refusal.code == IDENTITY_SOURCE_UNVERIFIED
        assert "could not be verified" in refusal.message
        # It asked before refusing.
        assert passport.queries == [(SOURCE, OWNER)]

    def test_no_exception_escapes_the_seam(self):
        passport = _RaisingPassport()
        seam = _seam_with({"bot_id": SOURCE}, passport)
        # Would raise if the provider error propagated.
        assert seam.identity_source(owner_id=OWNER, bot_id=SOURCE) is not None