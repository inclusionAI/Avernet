"""Submitting a create-from-manifest request: what runs, in what order (W13).

Two properties carry the whole item and both are asserted here rather than
reasoned about: nothing external is spent on a document that cannot succeed, and
**no bot is ever created by submission**.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_config_manifest.schema import (
    ManifestValidationError,
    Violation,
)
from agentclaw.community.plugin_api.passport import PassportError
from agentclaw.community.core.bot_management.create_flow import (
    BotCreateContext,
    BotCreateDeploymentMode,
    BotCreateSpec,
    creation_spec_from_payload,
    creation_spec_to_payload,
    submit_bot_creation_with_manifest,
)

_CONTEXT = BotCreateContext(
    deployment_mode=BotCreateDeploymentMode.CLOUD, space_kind="personal"
)
_SPEC = BotCreateSpec(
    entity_id="u1", engine_type="openclaw", bot_type="personal", bot_name="Bot"
)
_DOCUMENT = 'schema_version: 1\nscript:\n  body: "echo hi"\n'


class _Seam:
    def __init__(self, *, refuse: bool = False, job_fails: bool = False) -> None:
        self.refuse = refuse
        self.job_fails = job_fails
        self.preflighted: list[dict] = []
        self.persisted: list[dict] = []
        self.started: list[dict] = []
        self.discarded: list[dict] = []

    def preflight(self, **kwargs):
        self.preflighted.append(kwargs)
        if self.refuse:
            raise ManifestValidationError(
                [Violation(location="manifest.identity", code="nope", message="no")]
            )
        return {}

    def persist(self, **kwargs):
        self.persisted.append(kwargs)
        return kwargs["spec_entity_id"] or f"staff_{kwargs['user_id']}"

    def start_job(self, **kwargs):
        self.started.append(kwargs)
        if self.job_fails:
            raise RuntimeError("the queue is unreachable")

    def discard(self, **kwargs):
        self.discarded.append(kwargs)
        return True


def _submit(seam, apply_result=None, passport=None):
    passport = passport or MagicMock()
    # ``apply_agent_passport``, not ``applyFirst``: this endpoint never asks
    # Passport to skip approval, not even for a user's first bot, so the
    # approval-flow call is the only one it makes on the default tenant.
    passport.apply_agent_passport.return_value = (
        apply_result
        if apply_result is not None
        else {"iframe_url": "https://auth.example/consent"}
    )
    bot_service = MagicMock()
    # True on purpose: even for a caller whose very first bot this is, the
    # consent step is not skipped here.
    bot_service.is_first_bot.return_value = True
    bot_service.is_first_personal_bot.return_value = True
    skill_set_factory = MagicMock()
    skill_set_factory.create.return_value.get_bot_mcp_codes.return_value = []
    outcome = submit_bot_creation_with_manifest(
        user_id="u1",
        bot_id="b_1",
        document=_DOCUMENT,
        modifier="u1",
        spec=_SPEC,
        context=_CONTEXT,
        bot_service=bot_service,
        passport_plugin=passport,
        skill_set_factory=skill_set_factory,
        manifest_seam=seam,
    )
    return outcome, passport, bot_service


def test_a_first_bot_still_goes_through_consent_on_this_surface():
    """No "first bot skips approval" shortcut here, deliberately.

    ``create_bot_with_authorization`` asks Passport to skip approval for a
    user's very first bot. This endpoint does not: an OpenAPI client routes its
    users through consent every time, so what a client integrates against is one
    flow rather than two — and the branch that returns a token with nowhere to
    send anyone never arises on the default tenant.

    Asserted on the *call*, not on the outcome, because the outcome of the two
    Passport methods looks the same from here. The stand-in reports this caller
    as first-bot in both senses, so a re-introduced shortcut fails this.
    """
    seam = _Seam()
    _outcome, passport, bot_service = _submit(seam)

    assert bot_service.is_first_bot.return_value is True, "the premise"
    passport.apply_agent_passport.assert_called_once()
    passport.apply_first_agent_passport.assert_not_called()


def test_submission_stores_the_manifest_and_returns_the_authorization_handles():
    seam = _Seam()
    outcome, _passport, bot_service = _submit(seam)

    assert outcome.bot_id == "b_1"
    assert outcome.iframe_url == "https://auth.example/consent"
    assert seam.persisted, "the validated manifest must be stored, not re-sent"
    bot_service.create_bot.assert_not_called()


def test_submission_never_creates_the_bot_even_when_passport_answers_at_once():
    """The inline-create branch is the one this path must not take.

    If it did, the pre-container phase would be skipped whenever Passport
    happened to answer immediately, and that bot's first boot would carry no
    script — the exact failure this item exists to prevent.
    """
    seam = _Seam()
    _outcome, _passport, bot_service = _submit(
        seam, apply_result={"token": "t", "agent_code": "ac"}
    )
    bot_service.create_bot.assert_not_called()


def test_an_invalid_manifest_is_refused_before_passport_is_applied_for():
    """Asserted on the plugin, not inferred: a user must never complete an
    authorization only to be told their document was wrong."""
    seam = _Seam(refuse=True)
    passport = MagicMock()
    with pytest.raises(ManifestValidationError):
        _submit(seam, passport=passport)

    passport.apply_first_agent_passport.assert_not_called()
    passport.apply_agent_passport.assert_not_called()
    assert seam.preflighted, "the manifest was never checked"
    assert not seam.persisted, "a refused document must not be stored"


def test_the_manifest_is_preflighted_against_the_prepared_spec():
    """``_prepare_create`` can rewrite the engine, and capabilities depend on it."""
    seam = _Seam()
    _submit(seam)
    (call,) = seam.preflighted
    assert call["engine_type"] == "openclaw"
    assert call["bot_type"] == "personal"


def test_a_stored_manifest_is_discarded_when_the_handoff_fails():
    """Submission is one unit from the persist onwards, and it has to be.

    Once the document is stored it sits under a ``bot_id`` the caller has not
    been told about. If the durable handoff fails, the request ends in an error
    and that row is unreachable: no bot record for ordinary deletion to find,
    and no job to expire and clean it up. "The rows this endpoint creates are
    bounded by their own jobs" is only true once a job exists — before that,
    this is the only thing that can bound them.
    """
    seam = _Seam(job_fails=True)

    with pytest.raises(RuntimeError):
        _submit(seam)

    assert seam.persisted, "nothing was stored, so this proves nothing"
    assert seam.discarded == [{"entity_id": "u1", "bot_id": "b_1"}], (
        "the discard has to use the same key the persist did, or it deletes "
        "nothing and the row survives anyway"
    )


def test_a_passport_that_answers_with_nowhere_to_go_also_discards():
    """The other way the same window opens: the application itself is useless.

    Neither a token nor a URL means nobody can ever authorize this creation, so
    the caller gets an error — and the manifest must not outlive it.
    """
    seam = _Seam()

    with pytest.raises(PassportError):
        _submit(seam, apply_result={})

    assert seam.persisted
    assert seam.discarded, "the stored manifest outlived a submission that failed"


def test_a_successful_submission_starts_the_job_and_discards_nothing():
    seam = _Seam()

    _submit(seam)

    assert len(seam.started) == 1
    assert not seam.discarded
    started = seam.started[0]
    # The job is handed the *prepared* spec, frozen, so nothing about the
    # creation has to be supplied again by the poll.
    assert started["bot_id"] == seam.persisted[0]["bot_id"]
    assert started["spec"]["engine_type"]


def test_the_background_job_preserves_space_quota_enforcement():
    context = BotCreateContext(
        deployment_mode=BotCreateDeploymentMode.CLOUD,
        space_kind="team",
        space_quota=True,
    )

    payload = creation_spec_to_payload(_SPEC, context)
    restored_spec, restored_context = creation_spec_from_payload(payload)

    assert restored_spec == _SPEC
    assert restored_context == context
