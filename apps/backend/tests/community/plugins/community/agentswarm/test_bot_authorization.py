"""ManifestBotAuthorizationSource — the run surfaces' read into the pending gate.

Pins the agentswarm service's authorization seam: run detail and the SSE
gate snapshots ask the source for bot-kind agents, and presence keys off
the passport facade's live application status — PENDING inside the window
serves {state, iframe_url, redirect_url, deadline, application_id} frozen
out of the durable creation-job payload, while issued, rejected,
unanswerable and past-window answers surface none. Every unreadable fact
reads as the wire module's empty string instead of raising.

Hand fakes per house style (no MagicMock): the seam stand-in serves
TaskRecord-like jobs whose payloads carry the same keys
``build_create_job_payload`` freezes; the passport stand-in serves the
facade's ``query_auth_status`` answers.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from agentclaw.community.core.agentswarm.domain.models import BotState
from agentclaw.community.plugins.community.agentswarm.bot_authorization import (
    ManifestBotAuthorizationSource,
)


class FakeSeam:
    def __init__(self, jobs: dict[str, object]) -> None:
        self.jobs = jobs
        self.calls: list[tuple[str, str]] = []

    def find_job(self, *, entity_id: str, bot_id: str) -> object | None:
        self.calls.append((entity_id, bot_id))
        return self.jobs.get(bot_id)


class FakePassport:
    """The facade stand-in: per-(bot, owner) query_auth_status answers."""

    def __init__(
        self, answers: dict[str, dict | None], fail_for: set[str] | None = None,
    ) -> None:
        self.answers = answers
        self.fail_for = fail_for or set()
        self.calls: list[tuple[str, str]] = []

    def query_auth_status(
        self, bot_id: str, owner_workno: str, **_: object,
    ) -> dict | None:
        self.calls.append((bot_id, owner_workno))
        if bot_id in self.fail_for:
            raise RuntimeError("facade unavailable")
        return self.answers.get(bot_id)


def _job(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(payload=payload)


def _pending(application_id: str = "sim-passport-0001") -> dict:
    return {"status": "PENDING", "application_id": application_id}


def _source(
        jobs: dict[str, object],
        answers: dict[str, dict | None] | None = None,
        fail_for: set[str] | None = None,
) -> ManifestBotAuthorizationSource:
    bot_ids = {bot_id for bot_id in jobs if bot_id}
    answers = answers or {bot_id: _pending() for bot_id in bot_ids}
    return ManifestBotAuthorizationSource(
        manifest_seam=FakeSeam(jobs),
        passport_plugin=FakePassport(answers, fail_for),
    )


class TestPendingAuthorization:

    def test_serves_the_payload_facts_in_the_shared_descriptor_shape(self):
        source = _source({
            "agent_swarm_a1": _job({
                "iframe_url": "https://tcauthmng.example/consent?app=p1",
                "redirect_url": "https://tcauthmng.example/done-p1",
                "submitted_at": "2026-11-20T09:10:20",
                "window_seconds": 600,
            }),
        }, answers={"agent_swarm_a1": _pending("sim-passport-aa11")})

        descriptor = source.authorization(
            owner_id="staff-1", bot_id="agent_swarm_a1",
        )

        assert descriptor is not None
        assert descriptor.state == \
            BotState.AWAITING_AUTHORIZATION.value
        assert descriptor.iframe_url == \
            "https://tcauthmng.example/consent?app=p1"
        assert descriptor.redirect_url == \
            "https://tcauthmng.example/done-p1"
        # The horizon is the payload's own spelling: submitted_at plus
        # the frozen window, not a re-derived clock.
        assert descriptor.deadline == (
            datetime.fromisoformat("2026-11-20T09:10:20")
            + timedelta(seconds=600)
        ).isoformat()
        assert descriptor.application_id == "sim-passport-aa11"

    def test_no_durable_payload_answers_none_without_touching_the_facade(self):
        source = _source({}, answers={})

        assert source.authorization(
            owner_id="staff-1", bot_id="agent_swarm_none",
        ) is None
        assert source._passport.calls == []

    def test_issued_application_answers_none_despite_consent_urls(self):
        # Fast-path creation: the payload froze the URLs at apply time,
        # but the application is already decided — no gate is pending.
        source = _source({
            "agent_swarm_a1": _job({
                "iframe_url": "https://tcauthmng.example/consent?app=p1",
                "redirect_url": "https://tcauthmng.example/done-p1",
                "submitted_at": "2026-11-20T09:10:20",
                "window_seconds": 600,
            }),
        }, answers={
            "agent_swarm_a1": {"status": "ISSUED", "application_id": "x"},
        })

        assert source.authorization(
            owner_id="staff-1", bot_id="agent_swarm_a1",
        ) is None

    def test_rejected_application_answers_none(self):
        source = _source({
            "agent_swarm_a1": _job({"iframe_url": "https://x.example/c"}),
        }, answers={
            "agent_swarm_a1": {"status": "REJECTED", "application_id": "x"},
        })

        assert source.authorization(
            owner_id="staff-1", bot_id="agent_swarm_a1",
        ) is None

    def test_facade_failure_reads_as_not_pending_never_raises(self):
        source = _source({
            "agent_swarm_a1": _job({"iframe_url": "https://x.example/c"}),
        }, fail_for={"agent_swarm_a1"})

        assert source.authorization(
            owner_id="staff-1", bot_id="agent_swarm_a1",
        ) is None

    def test_facade_none_answer_reads_as_not_pending(self):
        source = _source({
            "agent_swarm_a1": _job({"iframe_url": "https://x.example/c"}),
        }, answers={"agent_swarm_a1": None})

        assert source.authorization(
            owner_id="staff-1", bot_id="agent_swarm_a1",
        ) is None

    def test_pending_past_the_window_answers_none(self):
        # PENDING past its horizon: the creation job owns the expiry
        # flip, and a parked row's degraded spelling is the service's to
        # compose — never a dead window surfaced as actionable.
        source = _source({
            "agent_swarm_old": _job({
                "iframe_url": "https://tcauthmng.example/consent?app=old",
                "submitted_at": "2020-11-20T09:10:20",
                "window_seconds": 180,
            }),
        })

        assert source.authorization(
            owner_id="staff-1", bot_id="agent_swarm_old",
        ) is None

    def test_missing_facts_read_as_empty_strings_never_raise(self):
        source = _source({
            "agent_swarm_empty": _job({
                # submitted_at absent: the window is unanswerable, the
                # payload's fact-only rule degrades instead of raising.
            }),
        }, answers={"agent_swarm_empty": {"status": "PENDING"}})

        descriptor = source.authorization(
            owner_id="staff-1", bot_id="agent_swarm_empty",
        )

        assert descriptor is not None
        assert descriptor.state == \
            BotState.AWAITING_AUTHORIZATION.value
        assert descriptor.iframe_url == ""
        assert descriptor.redirect_url == ""
        assert descriptor.deadline == ""
        assert descriptor.application_id == ""

    def test_unreadable_submitted_at_reads_as_empty_deadline(self):
        source = _source({
            "agent_swarm_bad": _job({
                "iframe_url": "https://tcauthmng.example/consent?app=b",
                "redirect_url": "https://tcauthmng.example/done-b",
                "submitted_at": "not-an-iso-timestamp",
                "window_seconds": 600,
            }),
        })

        descriptor = source.authorization(
            owner_id="staff-1", bot_id="agent_swarm_bad",
        )

        assert descriptor.iframe_url == \
            "https://tcauthmng.example/consent?app=b"
        assert descriptor.deadline == ""

    def test_lookups_are_scoped_to_the_owner_and_bot(self):
        source = _source({"agent_swarm_x": _job({"iframe_url": "https://x.example/c"})})

        source.authorization(owner_id="  staff-1  ", bot_id=" agent_swarm_x ")

        assert source._manifest_seam.calls == [("staff-1", "agent_swarm_x")]
        assert source._passport.calls == [
            ("agent_swarm_x", "staff-1"),
        ]

    def test_blank_inputs_are_passed_through_never_substituted(self):
        source = _source({
            "": _job({"iframe_url": "https://x.example/c"}),
        }, answers={"": {"status": "PENDING", "application_id": "blank"}})

        descriptor = source.authorization(owner_id="", bot_id="")

        # A blank owner never reaches another owner's payload: the seam
        # and the facade each see the blank scope exactly as given (no
        # ambient identity is fabricated in between) and answer for
        # precisely that scope.
        assert source._manifest_seam.calls == [("", "")]
        assert source._passport.calls == [("", "")]
        assert descriptor is not None
        assert descriptor.iframe_url == "https://x.example/c"
        assert descriptor.application_id == "blank"