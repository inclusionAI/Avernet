"""The create-with-manifest pair: its addresses, its bars, its state table.

The state table (`plan.md` §K-8) is the part worth testing case by case. It is
the whole meaning of the poll, it has eight answers and two "there is nothing
here", and every one of them is a claim a caller acts on — "you have a bot" and
"you have no bot" are different instructions.

The rest of this file pins the two structural properties the pair depends on and
which nothing else would catch: the submit address is a literal that the bots
group's ``{bot_id}`` wildcard must never capture, and the poll reaches no
external service — which is true because it has nowhere to reach *from*, and
that is what is asserted rather than a call count.
"""

from __future__ import annotations

import inspect

from fastapi import FastAPI
from fastapi.routing import APIRoute

try:  # fastapi >= 0.138 wraps an included router in a lazy proxy
    from fastapi.routing import _IncludedRouter
except ImportError:  # older fastapi flattens onto app.routes
    _IncludedRouter = ()

from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.adapters.http.openapi_v1.admission import ADMISSION
from agentclaw.community.adapters.http.openapi_v1.admission_modes import AdmissionMode
from agentclaw.community.adapters.http.openapi_v1.bots import create_with_manifest
from agentclaw.community.adapters.http.openapi_v1.principal import (
    refuse_app_only_caller,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.create_job import (
    AUTHORIZATION_WINDOW_ELAPSED,
    BOT_COULD_NOT_BE_PROVISIONED,
    _CONTAINER_FAILED_STATUSES,
    _CONTAINER_READY_STATUSES,
)
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER,
    CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.task_queue.types import TaskStatus
from agentclaw.community.plugin_api.passport import PassportPlugin

from agentclaw.community.adapters.http.openapi_v1.bots.schemas_create_with_manifest import (
    CreationState,
)

_SUBMIT = ("POST", "/openapi/v1/bots/with-manifest")
_POLL = ("GET", "/openapi/v1/bots/{bot_id}/with-manifest/status")


# ── the state table ────────────────────────────────────────────────────────


class _Job:
    """A task row, as much of one as the table reads."""

    def __init__(self, status, last_error=None, payload=None) -> None:
        self.status = status
        self.last_error = last_error
        self.payload = payload or {}


class _Report:
    def __init__(self, trigger, status) -> None:
        self.trigger = trigger
        self.status = status


def _state(*, bot=None, report=None, job=None):
    """The table under test, with the job lookup counted."""
    calls: list[int] = []

    def _lookup():
        calls.append(1)
        return job

    answer = create_with_manifest._creation_state(
        bot=bot, report=report, job=_lookup
    )
    return answer, len(calls)


def test_a_live_job_and_no_bot_is_awaiting_authorization():
    (state, record), _ = _state(job=_Job(TaskStatus.PENDING))
    assert state is CreationState.AWAITING_AUTHORIZATION
    assert record is not None


def test_a_running_job_still_reads_as_awaiting():
    """RUNNING is the job polling Passport, not the bot being created."""
    (state, _), _ = _state(job=_Job(TaskStatus.RUNNING))
    assert state is CreationState.AWAITING_AUTHORIZATION


def test_a_failed_job_with_no_bot_is_a_rejection():
    (state, _), _ = _state(job=_Job(TaskStatus.FAILED, "authorization did not complete: REJECTED"))
    assert state is CreationState.AUTHORIZATION_REJECTED


def test_the_queues_own_timeout_is_an_expiry_not_a_rejection():
    (state, _), _ = _state(job=_Job(TaskStatus.TIMED_OUT))
    assert state is CreationState.AUTHORIZATION_EXPIRED


def test_the_handlers_own_expiry_is_an_expiry_too():
    """The half the queue status cannot tell you.

    Expiry normally reaches the handler first, which fails the task so it can
    delete what submission wrote — a task retired DB-side never runs again. The
    row is then ``FAILED`` like a decline, and only the reason separates them.
    Reporting `AUTHORIZATION_REJECTED` here would attribute to the user a
    decision they never made.
    """
    (state, _), _ = _state(job=_Job(TaskStatus.FAILED, AUTHORIZATION_WINDOW_ELAPSED))
    assert state is CreationState.AUTHORIZATION_EXPIRED


def test_no_job_and_no_bot_is_nothing_here():
    answer, _ = _state()
    assert answer is None


def test_a_bot_with_no_job_is_not_this_endpoints_business():
    """A bot created the ordinary way, given a manifest by PUT afterwards.

    It has a bot record and no post-container apply, which is the shape of
    `CREATING` — so without the job lookup it would be reported as a creation
    that is still going. Answering `404` is the difference between "we have no
    idea what you are asking about" and inventing a state for it.
    """
    answer, _ = _state(bot={"status": "ACTIVE"})
    assert answer is None


def test_a_bot_and_a_live_job_is_creating():
    (state, _), _ = _state(bot={"status": "STARTING"}, job=_Job(TaskStatus.PENDING))
    assert state is CreationState.CREATING


def test_phase_as_record_does_not_look_like_progress():
    """The pre-container phase is written *before* the bot, so it says nothing
    about how far the creation got. It has to read the same as no record."""
    (state, _), _ = _state(
        bot={"status": "STARTING"},
        report=_Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED),
        job=_Job(TaskStatus.PENDING),
    )
    assert state is CreationState.CREATING


def test_a_bot_that_will_never_have_a_container_is_create_failed():
    (state, _), _ = _state(bot={"status": "FAILED"}, job=_Job(TaskStatus.PENDING))
    assert state is CreationState.CREATE_FAILED


def test_a_job_that_gave_up_before_a_container_is_create_failed():
    (state, _), _ = _state(
        bot={"status": "STARTING"},
        job=_Job(TaskStatus.FAILED, "the bot could not be provisioned: FAILED"),
    )
    assert state is CreationState.CREATE_FAILED


def test_a_job_that_gave_up_with_the_bot_running_is_an_apply_failure():
    """The bot exists and works, so `CREATE_FAILED` would be a lie with a cost.

    A job can go terminal after the bot is up — starting the post-container
    phase keeps failing, say, until the queue's deadline retires it. Telling the
    caller their creation failed makes them create a second bot while the first
    is already running and billable. Under this API a usable bot means the
    failure was on the configuration side, whatever stopped the job.
    """
    (state, _), _ = _state(
        bot={"status": "ACTIVE"},
        job=_Job(TaskStatus.TIMED_OUT),
    )
    assert state is CreationState.APPLY_FAILED
    assert create_with_manifest._bot_is_shown(state), (
        "the response must carry the bot, or the caller cannot see it exists"
    )


def test_a_ready_status_is_read_positively_not_as_not_failed():
    """A status nobody anticipated is not reported as a working bot."""
    (state, _), _ = _state(
        bot={"status": "SOMETHING_NEW"},
        job=_Job(TaskStatus.TIMED_OUT),
    )
    assert state is CreationState.CREATE_FAILED


def test_the_poll_and_the_job_agree_on_what_is_running():
    """The mirror of the failed-status pinning below, and for the same reason.

    A status added to the job's ready set and not the poll's would leave a bot
    the job is happily configuring reported as a creation that failed.
    """
    assert create_with_manifest._CONTAINER_READY == _CONTAINER_READY_STATUSES


def test_a_running_post_container_apply_is_applying():
    (state, _), _ = _state(
        bot={"status": "ACTIVE"},
        report=_Report(CREATE_ON_CONTAINER_TRIGGER, ApplyStatus.RUNNING),
    )
    assert state is CreationState.APPLYING


def test_a_succeeded_post_container_apply_is_ready():
    (state, _), _ = _state(
        bot={"status": "ACTIVE"},
        report=_Report(CREATE_ON_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED),
    )
    assert state is CreationState.READY


def test_a_partial_apply_is_apply_failed_not_ready():
    """`PARTIAL` is a state to act on, not a success with a footnote.

    Under the manifest's category overwrite a partial category can have removed
    entries it then failed to replace, so the bot is running with less
    configuration than it had.
    """
    (state, _), _ = _state(
        bot={"status": "ACTIVE"},
        report=_Report(CREATE_ON_CONTAINER_TRIGGER, ApplyStatus.PARTIAL),
    )
    assert state is CreationState.APPLY_FAILED


def test_a_failed_apply_is_apply_failed_and_not_create_failed():
    """The distinction the two names exist for: the bot is up either way here."""
    (state, _), _ = _state(
        bot={"status": "ACTIVE"},
        report=_Report(CREATE_ON_CONTAINER_TRIGGER, ApplyStatus.FAILED),
    )
    assert state is CreationState.APPLY_FAILED
    assert state is not CreationState.CREATE_FAILED


def test_a_bot_with_a_later_explicit_apply_is_not_reported_as_still_creating():
    """An apply after the creation supersedes the record the poll reads.

    `last_apply` gives the newest, and once a caller has run
    `POST .../config-manifest/apply` that is an `explicit` record, not the
    creation's. Without the job's own verdict this would fall through to
    `CREATING` — a finished creation reported as still in flight, forever.

    Reading the creation's own record back instead would need a
    trigger-filtered query the design chose not to add; the job's status
    answers it for free.
    """
    (state, _), _ = _state(
        bot={"status": "ACTIVE"},
        report=_Report("explicit", ApplyStatus.SUCCEEDED),
        job=_Job(TaskStatus.SUCCEEDED),
    )
    assert state is CreationState.READY


def test_a_bot_created_the_ordinary_way_is_still_nothing_here():
    """Even with a manifest applied to it afterwards.

    A bot created through `POST /openapi/v1/bots` and given a manifest by `PUT`
    has a bot record and an apply record, and no creation job — which is what
    separates it from a creation. This endpoint answers about creations.
    """
    answer, _ = _state(
        bot={"status": "ACTIVE"},
        report=_Report("explicit", ApplyStatus.SUCCEEDED),
    )
    assert answer is None


def test_the_report_shown_is_the_creations_own():
    """A superseding `explicit` report is not returned under a creation state.

    It would answer a question the caller did not ask, and would look like the
    creation's outcome had changed after the fact.
    """
    assert create_with_manifest._report_is_shown(
        CreationState.READY, _Report(CREATE_ON_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED)
    )
    assert not create_with_manifest._report_is_shown(
        CreationState.READY, _Report("explicit", ApplyStatus.SUCCEEDED)
    )
    assert not create_with_manifest._report_is_shown(CreationState.READY, None)
    assert not create_with_manifest._report_is_shown(
        CreationState.CREATING,
        _Report(CREATE_ON_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED),
    )


def test_the_states_a_caller_polls_hardest_never_look_the_job_up():
    """The read's cost model, asserted rather than described.

    Finding a task by its idempotency key is index-served only while the task is
    live; once it is over the lookup is a scan. `APPLYING` and `READY` are
    exactly the states a caller sits in — and they are answerable from the bot
    record and the apply record alone, so they must not pay for it.
    """
    for status in (ApplyStatus.RUNNING, ApplyStatus.SUCCEEDED, ApplyStatus.PARTIAL):
        _, lookups = _state(
            bot={"status": "ACTIVE"},
            report=_Report(CREATE_ON_CONTAINER_TRIGGER, status),
        )
        assert lookups == 0, status


# ── the addresses and the bars ─────────────────────────────────────────────


def _live_paths() -> list[tuple[str, str]]:
    """Every ``(method, path)`` the assembled app answers, in resolution order.

    Walked rather than read off ``app.routes``: fastapi no longer flattens an
    ``include_router`` eagerly, and the order is the whole point of the wildcard
    test below — a set would answer "is it published?" and say nothing about
    which route wins the segment.
    """
    app = FastAPI()
    app.include_router(build_public_router())
    live: list[tuple[str, str]] = []

    def walk(routes, prefix: str = "") -> None:
        for route in routes:
            if _IncludedRouter and isinstance(route, _IncludedRouter):
                inner = prefix + getattr(route.include_context, "prefix", "")
                original = route.original_router
                walk(getattr(original, "routes", []), inner)
                walk(getattr(original, "_low_priority_routes", []), inner)
            elif isinstance(route, APIRoute):
                for method in sorted(route.methods or ()):
                    live.append((method, prefix + route.path))

    walk(app.routes)
    return live


def test_both_routes_are_mounted_on_the_public_surface():
    published = _live_paths()
    assert _SUBMIT in published
    assert _POLL in published


def test_the_submit_literal_is_not_captured_by_the_bots_wildcard():
    """`with-manifest` must resolve as itself, not as a bot id.

    The bots group publishes `/openapi/v1/bots/{bot_id}`, which matches any
    single segment, so whichever is registered first owns the address. Asserted
    on the app's own resolution order rather than on the mount list, because the
    mount list is what a future edit would reorder.
    """
    order = [path for _, path in _live_paths()]
    assert "/openapi/v1/bots/with-manifest" in order

    # Only a *single-segment* wildcard can swallow this address. The deeper
    # ones (`/openapi/v1/bots/{bot_id}/sessions` …) have a different shape and
    # could never match it, so counting them would make this test fail for a
    # reason that is not the one it exists for.
    wildcards = [
        index
        for index, path in enumerate(order)
        if path.startswith("/openapi/v1/bots/{")
        and "/" not in path[len("/openapi/v1/bots/") :]
    ]
    literal = order.index("/openapi/v1/bots/with-manifest")
    assert not wildcards or literal < min(wildcards), (
        "a /openapi/v1/bots/{...} wildcard is registered ahead of the "
        "with-manifest literal, so a submission would be read as addressing a bot"
    )


def test_the_poll_and_the_job_agree_on_what_never_comes_up():
    """Two copies of "no container is coming", pinned to each other.

    They are separate on purpose — the job's set decides when to stop
    rescheduling, the poll's decides what to report — but a status added to one
    and not the other would mean a creation the job has given up on still
    reporting `CREATING` forever, or the reverse. The duplication is cheap; the
    divergence is not, and nothing else would catch it.
    """
    assert create_with_manifest._PROVISIONING_FAILED == _CONTAINER_FAILED_STATUSES


def test_both_routes_refuse_an_app_only_caller():
    """Not a preference — without it an application can create a bot as anyone.

    Both routes take their owner from ``UserIdDep``. For a caller that names an
    application and no user, ``require_user_id`` returns the ``user_id`` **query
    parameter verbatim**, explicitly deferring "may this app act for that user?"
    to whichever grant dependency the route declares. These routes can declare
    none: a grant covers a bot, and at submission there is no bot yet.

    So the refusal is the only check standing between an app credential and
    ``POST …/with-manifest?user_id=<someone else>`` — which would spend that
    user's quota and run a caller-supplied startup script under their identity.
    The poll is the same mechanism and leaks the authorization handles.

    Pinned in both places because either alone is insufficient: the table entry
    is what ``require_principal`` enforces centrally, and the route dependency
    is what holds if the table entry were ever mislabelled to an admitting mode.
    """
    assert ADMISSION[_SUBMIT] is AdmissionMode.REFUSED
    assert ADMISSION[_POLL] is AdmissionMode.REFUSED

    declared = {
        route.endpoint.__name__: route
        for route in create_with_manifest.router.routes
        if isinstance(route, APIRoute)
    }
    for name in ("create_bot_with_manifest", "get_bot_create_with_manifest_status"):
        route = declared[name]
        assert any(
            dep.call is refuse_app_only_caller for dep in route.dependant.dependencies
        ), f"{name} must declare the refusal, not only be listed in the table"


def test_the_poll_takes_nothing_but_its_path():
    """No body and no query parameter — so there is nowhere to re-send the
    manifest, and the document that was validated is the one that is applied."""
    signature = inspect.signature(
        create_with_manifest.get_bot_create_with_manifest_status
    )
    assert "body" not in signature.parameters
    assert "config_manifest" not in signature.parameters


def test_the_poll_cannot_reach_passport():
    """A pure read, asserted structurally rather than by counting calls.

    The poll takes no Passport plugin, so there is nothing for it to query even
    by mistake. The job is what polls AgentPass; duplicating that here would put
    business work on a read path and make polling faster change an outcome.
    """
    # Annotations are strings — the module is under
    # ``from __future__ import annotations`` — so the name is what is compared.
    assert PassportPlugin.__name__ not in _annotations(
        create_with_manifest.get_bot_create_with_manifest_status
    )
    # The submission *does* take one; that asymmetry is what is being pinned.
    assert PassportPlugin.__name__ in _annotations(
        create_with_manifest.create_bot_with_manifest
    )


def _annotations(fn) -> set[str]:
    return {
        str(parameter.annotation)
        for parameter in inspect.signature(fn).parameters.values()
    }


# ── RECORD_APPLY_PROVISION: the pre-container record is the terminal one (W8) ──

from agentclaw.community.core.bot_config_manifest.apply.delivery import (  # noqa: E402
    CreationSequence,
)

_RECORD_FIRST = CreationSequence.RECORD_APPLY_PROVISION


def _record_first_state(*, bot, report=None, job=None):
    return create_with_manifest._creation_state(
        bot=bot, report=report, job=lambda: job, sequence=_RECORD_FIRST
    )


def test_record_first_the_phase_running_against_the_record_is_applying():
    state, _ = _record_first_state(
        bot={"status": "PENDING"}, report=_Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.RUNNING)
    )
    assert state is CreationState.APPLYING


def test_record_first_a_finished_phase_with_the_container_still_coming_is_creating():
    state, _ = _record_first_state(
        bot={"status": "PENDING"},
        report=_Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED),
        job=_Job(TaskStatus.PENDING),
    )
    assert state is CreationState.CREATING


def test_record_first_a_running_bot_reads_the_phase_as_the_outcome():
    ready, _ = _record_first_state(
        bot={"status": "ACTIVE"}, report=_Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED)
    )
    assert ready is CreationState.READY
    failed, _ = _record_first_state(
        bot={"status": "ACTIVE"}, report=_Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.PARTIAL)
    )
    assert failed is CreationState.APPLY_FAILED


def test_record_first_a_bot_that_never_came_up_is_create_failed():
    state, _ = _record_first_state(
        bot={"status": "FAILED"},
        report=_Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED),
        job=_Job(TaskStatus.FAILED, "the bot could not be provisioned: FAILED"),
    )
    assert state is CreationState.CREATE_FAILED


def test_record_first_shows_the_pre_container_report_and_create_between_phases_does_not():
    report = _Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED)
    assert create_with_manifest._report_is_shown(CreationState.READY, report, _RECORD_FIRST)
    assert not create_with_manifest._report_is_shown(CreationState.READY, report)
    assert not create_with_manifest._report_is_shown(
        CreationState.READY, _Report(CREATE_ON_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED), _RECORD_FIRST
    )


def test_create_between_phases_still_ignores_the_pre_container_record():
    """Under today's sequence phase A's record is written before the bot and
    never the outcome — a bot with only that record and a live job is CREATING."""
    (state, _), _ = _state(
        bot={"status": "ACTIVE"},
        report=_Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED),
        job=_Job(TaskStatus.PENDING),
    )
    assert state is CreationState.CREATING


def test_record_first_a_retired_job_with_the_container_never_up_is_create_failed():
    """The phase finished, the bot never reached ACTIVE, the queue retired the
    job: not CREATING forever — the job's row says it is over."""
    for status in (TaskStatus.TIMED_OUT, TaskStatus.FAILED):
        state, _ = _record_first_state(
            bot={"status": "PENDING"},
            report=_Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED),
            job=_Job(status, "the bot could not be provisioned: PENDING"),
        )
        assert state is CreationState.CREATE_FAILED, status


def test_a_provisioning_failure_that_soft_deleted_the_record_is_create_failed():
    """W8: under RECORD_APPLY_PROVISION the service soft-deletes the record on an
    allocation failure, so the poll sees no bot beside a terminal job — the
    shape of a decline, which it is not."""
    (state, _), _ = _state(job=_Job(TaskStatus.FAILED, f"{BOT_COULD_NOT_BE_PROVISIONED}: no capacity"))
    assert state is CreationState.CREATE_FAILED
    (state, _), _ = _state(job=_Job(TaskStatus.FAILED, "authorization did not complete: REJECTED"))
    assert state is CreationState.AUTHORIZATION_REJECTED
