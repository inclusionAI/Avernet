"""ManifestBotAuthorizationSource — the run surfaces' read into the job payload.

Pins the agentswarm service's authorization seam (openspec
add-bot-create-human-confirm tasks 3.1/3.2, D1): run detail and the SSE
gate snapshots derive {state, iframe_url, redirect_url, deadline} from the
durable creation-job payload — the same descriptor the host adapter
reports, never a second stored copy — with the window deadline served in
the payload's own ISO-8601 spelling and every unreadable fact reading as
the wire module's empty string instead of raising.

Hand fakes per house style (no MagicMock): the seam stand-in serves
TaskRecord-like jobs whose payloads carry the same keys
``build_create_job_payload`` freezes.
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


def _job(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(payload=payload)


def _source(jobs: dict[str, object]) -> ManifestBotAuthorizationSource:
    return ManifestBotAuthorizationSource(manifest_seam=FakeSeam(jobs))


class TestAuthorization:

    def test_serves_the_payload_facts_in_the_shared_descriptor_shape(self):
        source = _source({
            "agent_swarm_a1": _job({
                "iframe_url": "https://tcauthmng.example/consent?app=p1",
                "redirect_url": "https://tcauthmng.example/done-p1",
                "submitted_at": "2026-11-20T09:10:20",
                "window_seconds": 600,
            }),
        })

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

    def test_no_durable_payload_answers_none(self):
        source = _source({})

        assert source.authorization(
            owner_id="staff-1", bot_id="agent_swarm_none",
        ) is None

    def test_missing_facts_read_as_empty_strings_never_raise(self):
        source = _source({
            "agent_swarm_empty": _job({
                # submitted_at absent: the window is unanswerable, the
                # payload's fact-only rule degrades instead of raising.
            }),
        })

        descriptor = source.authorization(
            owner_id="staff-1", bot_id="agent_swarm_empty",
        )

        assert descriptor is not None
        assert descriptor.state == \
            BotState.AWAITING_AUTHORIZATION.value
        assert descriptor.iframe_url == ""
        assert descriptor.redirect_url == ""
        assert descriptor.deadline == ""

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
        seam = FakeSeam({})
        source = ManifestBotAuthorizationSource(manifest_seam=seam)

        source.authorization(owner_id="  staff-1  ", bot_id=" agent_swarm_x ")

        assert seam.calls == [("staff-1", "agent_swarm_x")]

    def test_blank_inputs_are_passed_through_never_substituted(self):
        seam = FakeSeam({"": _job({"iframe_url": "https://x.example/c"})})
        source = ManifestBotAuthorizationSource(manifest_seam=seam)

        descriptor = source.authorization(owner_id="", bot_id="")

        # A blank owner never reaches another owner's payload: the seam
        # sees the blank scope exactly as given (no ambient identity is
        # fabricated in between) and answers for precisely that scope.
        assert seam.calls == [("", "")]
        assert descriptor is not None
        assert descriptor.iframe_url == "https://x.example/c"