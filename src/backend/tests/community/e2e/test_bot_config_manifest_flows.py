"""Walks the bot-config-manifest user manual against the assembled app.

``tests/community/_flows/bot_config_manifest/api_lifecycle.py`` holds the
journey as data — one FlowCase per manual section, each step carrying the claim
it asserts. This module is the executor: it seeds the bot the journey addresses,
runs the cases **in order through one shared FlowContext** (so an ``apply_id``
minted in one case is polled in the next), drains the apply queue between them,
and adds the assertions ``FlowStep.expect`` structurally cannot make.

Why the drain lives here: applying is a ``config_manifest.apply`` task, and this
app never runs lifecycle ``bootstrap()``, so no handler is registered and its
worker retires anything enqueued. Running the enqueued payload is exactly what a
worker with the handler would do, and doing it inline keeps the walk
deterministic — no lease, no poll interval, no thread racing the per-test
engine disposal. Same reasoning as ``_await_the_background_apply`` in
``tests/community/endpoints/test_openapi_config_manifest_apply.py``.

Why the extra assertions live here: ``expect`` is a subset match, so an expected
``[]`` matches *any* list and an expected ``""`` cannot say "non-empty". Every
manual claim of that shape — "``warnings`` is always an empty array on a read",
"``notes`` is empty on ARCA", "the capability table lists exactly these
constructs" — is asserted below against values the flow extracted.
"""
from __future__ import annotations

import pytest

from agentclaw.community.api.bot_config_manifest_apply_service import (
    BotConfigManifestApplyServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.apply.apply_task import (
    APPLY_TASK_TYPE,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.platform import (
    TaskQueueRepositoryProtocol,
)
from agentclaw.community.core.task_queue.types import DEFAULT_APP, TaskStatus
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community._flows.bot_config_manifest.api_lifecycle import (
    BOT_ID,
    MANIFEST_MANUAL_FLOWS,
    OWNER,
    PRINCIPAL_SIGNING_KEY,
)
from tests.community.framework.flow import FlowContext
from tests.community.framework.flow_runner import run_flow


class _Secret:
    secret_user = "test"
    secret_value = PRINCIPAL_SIGNING_KEY


class _Resolver:
    """Hands the verifier the key the flows signed their principal with."""

    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _seed(world) -> None:
    """One ACTIVE personal bot on an ARCA-family engine, owned by the caller.

    ``openclaw`` rather than ``teclaw`` because the journey declares ``script``:
    §2.1's capability table refuses it on teclaw and desktop outright, and the
    manual's create → converge arc is the ARCA one.
    """
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    world.get(BotRepository).insert(
        {
            "bot_id": BOT_ID,
            "bot_name": "Manifest Manual Bot",
            "owner_id": OWNER,
            "owner_name": OWNER,
            "entity_id": OWNER,
            "entity_type": "staff",
            "creator_id": OWNER,
            "status": "ACTIVE",
            "active_engine": "openclaw",
            "bot_type": "personal",
        }
    )


def _drain_applies(world, already_run: set[int]) -> None:
    """Run every apply task enqueued since the last drain, once each.

    Tracking ids matters here in a way it does not for a single-case test: the
    walk starts several applies against one bot, and re-running a spent payload
    would materialise it a second time and hand ``last-apply`` the wrong record.
    """
    repo = world.get(TaskQueueRepositoryProtocol)
    apply_service = world.get(BotConfigManifestApplyServiceProtocol)
    for status in TaskStatus:
        for task in repo.list_by_status(
            status=status, env=get_current_env(), app=DEFAULT_APP
        ):
            if task.task_type != APPLY_TASK_TYPE or task.id in already_run:
                continue
            already_run.add(task.id)
            apply_service.run_apply_task(task.payload)


# ── The assertions ``expect``'s subset match cannot make ───────────────────


def _absent_reads_as_empty(ctx: FlowContext) -> None:
    # §B.1.1: on a read, ``warnings`` is *always* an empty array — it is the
    # write's field. An expected [] in ``expect`` would have matched anything.
    assert ctx["absent_warnings"] == [], (
        "§B.1.1: warnings must be empty on a read, "
        f"got {ctx['absent_warnings']!r}"
    )
    # §B.2.5: an empty report's four arrays are all empty.
    for key in ("empty_sources", "empty_categories", "empty_entries", "empty_notes"):
        assert ctx[key] == [], f"§B.2.5: empty report's {key} must be [], got {ctx[key]!r}"

    # §B.7 + §B.1.4: the capability table lists every construct, supported and
    # unsupported alike, so a client can decide what to write before writing it.
    by_kind: dict[str, set[str]] = {}
    for construct in ctx["constructs"]:
        assert set(construct) >= {"kind", "name", "supported", "reason"}, (
            f"§B.1.4: a construct row is missing fields: {construct!r}"
        )
        if construct["supported"]:
            assert construct["reason"] == "", (
                "§B.1.4: reason is the empty string when supported, "
                f"got {construct!r}"
            )
        by_kind.setdefault(construct["kind"], set()).add(construct["name"])

    assert by_kind == {
        "category": {
            "mcp",
            "resources",
            "skills",
            "engine_config",
            "identity",
            "cli_tools",
        },
        "section": {"script"},
        "source": {"url", "git", "named", "content"},
    }, f"§B.7's construct table and the capability endpoint disagree: {by_kind!r}"


def _refused_put_names_every_reason(ctx: FlowContext) -> None:
    # §B.1.2: one pass reports every rule it could run — not one per submission.
    assert len(ctx["violations"]) == 2, (
        "§B.1.2: both reasons must arrive together, "
        f"got {ctx['violations']!r}"
    )
    for violation in ctx["violations"]:
        assert set(violation) == {"location", "code", "message"}, (
            f"§B.1.2: violation shape is public contract, got {violation!r}"
        )
        assert violation["message"], "§B.1.2: message must explain the rule"


def _put_starts_an_apply(ctx: FlowContext) -> None:
    # §B.1.2: RUNNING carries a real handle; only NOT_STARTED has an empty id.
    assert ctx["apply_id"], "§B.1.2: a RUNNING apply must carry an apply_id"
    # §B.1.2 warning class 2: a declared ``script`` is written now and executed
    # at next start, and the response says so rather than leaving it inferred.
    assert ctx["put_warnings"], (
        "§B.1.2: declaring a script must warn that it takes effect at next start"
    )
    # §B.1.1 again, now that a document exists: reads still never carry warnings.
    assert ctx["read_warnings"] == [], (
        f"§B.1.1: a read carries no warnings, got {ctx['read_warnings']!r}"
    )


def _report_is_complete(ctx: FlowContext) -> None:
    # §B.2.1 + §B.2.4: the handle names the record, and ``last-apply`` — the
    # authoritative "did it land" answer — names the same one.
    assert ctx["report_apply_id"] == ctx["apply_id"], (
        "§B.2.3: polling the handle must return the apply it was minted for"
    )
    assert ctx["last_apply_id"] == ctx["apply_id"], (
        "§B.2.4: last-apply must be the apply the PUT started"
    )
    # §B.2.5: one row per *declared* entry, and one per *declared* category —
    # a category the document never mentions is never touched, so never listed.
    assert len(ctx["report_entries"]) == 1, (
        f"§B.2.5: only declared entries are reported, got {ctx['report_entries']!r}"
    )
    assert len(ctx["report_categories"]) == 1, (
        f"§B.2.5: only declared categories are reported, "
        f"got {ctx['report_categories']!r}"
    )
    category = ctx["report_categories"][0]
    assert category["category"] == "script"
    # §B.2.5: a converged category aborted nothing and half-wrote nothing.
    assert category["aborted"] is False and category["partially_written"] is False, (
        f"§B.2.5: a SUCCEEDED category is neither aborted nor half-written: {category!r}"
    )
    # §3.3: overwrite removes what the declared area no longer names. A first
    # apply into an empty area removes nothing.
    assert category["removed"] == [], (
        f"§B.2.5: nothing was displaced, got removed={category['removed']!r}"
    )
    # §B.2.5: ``notes`` carries teclaw's whole-artifact redelivery failure and
    # nothing else — always empty on ARCA.
    assert ctx["report_notes"] == [], (
        f"§B.2.5: notes is empty on ARCA, got {ctx['report_notes']!r}"
    )
    # §B.2.5: finished_at is null only while RUNNING or on an empty report.
    assert ctx["report_finished_at"] is not None, (
        "§B.2.5: a terminal report carries finished_at"
    )


def _second_apply_changed_nothing(ctx: FlowContext) -> None:
    # §3.2 / §4.8 / §B.7: "already the declared shape" is zero action. If this
    # reports ``updated``, convergence is rewriting on every apply.
    assert [e["action"] for e in ctx["converged_entries"]] == ["unchanged"], (
        "§B.7: re-applying an unchanged declaration must be `unchanged`, "
        f"got {ctx['converged_entries']!r}"
    )


def _dry_run_planned_the_same_thing(ctx: FlowContext) -> None:
    # §4.4: the preview reports what *would* happen against current state —
    # which, on a converged bot, is nothing.
    assert [e["action"] for e in ctx["dry_run_entries"]] == ["unchanged"], (
        f"§4.4: the plan must match reality, got {ctx['dry_run_entries']!r}"
    )
    # §B.2.2: it writes no apply record, so the explicit apply still stands as
    # the last one. A dry run that displaced it would make "did my manifest
    # land" unanswerable.
    assert ctx["last_apply_id_after_dry_run"] == ctx["apply_id_3"], (
        "§B.2.2: a dry run must not enter last-apply"
    )


def _delete_cleared_only_the_declaration(ctx: FlowContext) -> None:
    # §B.1.3 + §3.3: DELETE removes the declaration, not the entities it
    # materialised — and starts no apply, so the standing report is untouched.
    assert ctx["last_apply_id_after_delete"] == ctx["apply_id_3"], (
        "§B.1.3: DELETE must not apply anything"
    )


#: Post-conditions per flow, keyed by the FlowCase name they follow.
_POSTCONDITIONS = {
    "manifest-absent-reads-as-empty": _absent_reads_as_empty,
    "manifest-refused-put-stores-nothing": _refused_put_names_every_reason,
    "manifest-put-stores-and-starts-an-apply": _put_starts_an_apply,
    "manifest-report-after-put": _report_is_complete,
    "manifest-convergence-report": _second_apply_changed_nothing,
    "manifest-dry-run-plans-without-recording": _dry_run_planned_the_same_thing,
    "manifest-delete-clears-the-declaration": _delete_cleared_only_the_declaration,
}


@pytest.mark.usefixtures("app_with_testing_modules")
def test_user_manual_walkthrough(app_with_testing_modules, world):
    """The manual's §4 workflow, start to finish, on one bot.

    Declare nothing → read empty · refuse a bad document → store nothing ·
    ``PUT`` → apply → read the report · ``PUT`` again → converge ·
    explicit apply · dry run → plan without recording · ``DELETE`` → clear the
    declaration and nothing else.

    One test rather than one per flow because it *is* one journey: each case
    reads state the previous case wrote. ``run_flow`` names the failing case and
    step, so a break still points at the manual section that broke.
    """
    _seed(world)

    ctx = FlowContext()
    # ``run_flow`` interpolates the path before substituting path_params, so the
    # addressed bot travels in the context like any other chained value.
    ctx["bot_id"] = BOT_ID

    already_run: set[int] = set()
    for case in MANIFEST_MANUAL_FLOWS:
        ctx = run_flow(case, app_with_testing_modules, world, initial_context=ctx)
        postcondition = _POSTCONDITIONS.get(case.name)
        if postcondition is not None:
            postcondition(ctx)
        # Whatever this case enqueued, a worker would have run before the next
        # case polled for its report.
        _drain_applies(world, already_run)
