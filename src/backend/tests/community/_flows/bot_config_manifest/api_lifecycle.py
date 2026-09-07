"""The bot-config-manifest user manual, walked as a business flow.

``docs/bot-config-manifest/user-manual.zh-CN.md`` is what a business caller
reads before writing their first manifest. It makes claims a caller will build
against — "an absent manifest reads as an empty document, not a 404", "``PUT``
starts an apply and the report's trigger is ``put``", "applying the same
document twice is ``unchanged``", "a dry run mints no id and never enters
``last-apply``". Each flow below walks one of those journeys end to end and
**cites the manual section it is asserting**, so a claim that stops being true
fails here by name rather than drifting into prose nobody re-reads.

Deliberately *not* a second copy of the per-endpoint suites in
``tests/community/endpoints/test_openapi_config_manifest*.py``: those pin each
route's own shape in isolation. What only a flow can catch is the seam between
them — an ``apply_id`` handed out by one route and polled through another, a
second ``PUT`` converging against what the first one materialised, a dry run
that must leave the previous report standing.

**Two things the runner owns, not these cases.**

1. *Draining.* ``POST …/apply`` and the apply that follows a ``PUT`` enqueue a
   ``config_manifest.apply`` task; the endpoint-test app registers no handler,
   so a worker never runs it (see ``_await_the_background_apply`` in
   ``tests/community/endpoints/test_openapi_config_manifest_apply.py``). A flow
   that starts an apply therefore ends where the apply is enqueued, and the
   flow that reads its report is a separate case — the seam is named in the
   case names rather than hidden inside one. ``tests/community/e2e/`` drains
   between them and threads one ``FlowContext`` through, so ``{apply_id}``
   still chains across the boundary.

2. *Emptiness.* ``FlowStep.expect`` is a **subset** match, so an expected ``[]``
   matches any list at all. Every "this array is empty" claim in the manual is
   therefore ``extract``-ed here and asserted exactly by the runner.

**Route A only.** Every route in this group is ``/openapi/v1`` and needs a
gateway-signed principal, which singlebox cannot mint — the reason
``bot_config_manifest`` sits in ``SINGLEBOX_E2E_EXEMPT``. These cases mint one
themselves, which the in-process app accepts and a live backend would not, so
``covers`` is left empty: this suite does not drain that exemption and must not
claim to.
"""
from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from tests.community.framework.flow import FlowCase, FlowStep


#: The owner every flow acts as, and the bot they all address. The runner seeds
#: both before the first step.
OWNER = "manifest-manual-owner"
BOT_ID = "manifest-manual-bot"

#: Signing key for the minted principal. The runner installs the matching
#: verifier config; nothing outside the test process ever sees it.
PRINCIPAL_SIGNING_KEY = "manifest-manual-flow-signing-key-at-least-32-bytes"

#: An ``apply_id`` that was never minted. §B.2.3: polling it answers an *empty
#: report*, not a 404 — the id is a polling handle, not an access credential.
UNKNOWN_APPLY_ID = "ffffffffffffffffffffffffffffffff"


def _principal(user_id: str = OWNER) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 24 * 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {"id": user_id, "username": f"{user_id}@example.test"},
                }
            ],
        },
        PRINCIPAL_SIGNING_KEY,
        algorithm="HS256",
    )


HEADERS = {PRINCIPAL_HEADER: _principal()}

#: §B.0.1: ``user_id`` is a **required** query parameter on every bot-scoped
#: route. Omitting it is a 422, not a default identity.
QUERY = {"user_id": OWNER}


#: The document the journey declares. ``script`` is the one category whose
#: materialiser needs neither a container nor a network fetch, so the whole
#: create → converge → report arc runs offline and the assertions are about the
#: manual's semantics rather than about a stubbed device.
SCRIPT_DOCUMENT = (
    "schema_version: 1\n"
    "script:\n"
    "  body: |\n"
    "    #!/bin/bash\n"
    "    echo 'manual walkthrough'\n"
)

#: Two violations from two different places at once. §B.1.2 promises one
#: validation pass reports **every** rule it can run, so a caller fixes the
#: document in one round rather than learning one rule per submission.
#: ``engine_config`` is Appendix C's headline refusal: expressible in the schema,
#: no materialiser, so refused at ``PUT`` rather than silently ignored.
INVALID_DOCUMENT = (
    "schema_version: 1\n"
    "manifest:\n"
    "  engine_config:\n"
    "    config:\n"
    "      model: m\n"
    "  identity:\n"
    "    - type: MEMORY.md\n"
    '      content: "hi"\n'
)


def _step(method: str, path: str, **kwargs) -> FlowStep:
    """A FlowStep carrying the principal and ``user_id`` every route demands."""
    query = {**QUERY, **kwargs.pop("query", {})}
    return FlowStep(method=method, path=path, query=query, headers=HEADERS, **kwargs)


_MANIFEST = "/openapi/v1/bots/{bot_id}/config-manifest"


MANIFEST_MANUAL_FLOWS: list[FlowCase] = [
    # ── §2.1 / §B.1.1 / §B.1.4 / §B.2.4: what a bot answers before anyone has
    # written anything. Every one of these is "absent is not an error".
    FlowCase(
        name="manifest-absent-reads-as-empty",
        covers=[],
        steps=[
            # §B.1.1: an unwritten manifest is an empty document. A 404 here
            # would make "has none" indistinguishable from "no such bot".
            _step(
                "GET",
                _MANIFEST,
                expect={
                    "code": 200000,
                    "data": {
                        "bot_id": BOT_ID,
                        "document": "",
                        "size_bytes": 0,
                        "schema_version": None,
                        "updated_by": "",
                        "updated_at": None,
                        "apply": None,
                    },
                },
                extract={"absent_warnings": "data.warnings"},
            ),
            # §B.2.4 + §B.2.5: never-applied reads an *empty report* — result
            # and trigger are "", not any enum value. Judge emptiness by
            # ``result == ""``.
            _step(
                "GET",
                _MANIFEST + "/last-apply",
                expect={
                    "data": {
                        "apply_id": "",
                        "result": "",
                        "trigger": "",
                        "started_at": None,
                        "finished_at": None,
                    }
                },
                extract={
                    "empty_sources": "data.sources",
                    "empty_categories": "data.categories",
                    "empty_entries": "data.entries",
                    "empty_notes": "data.notes",
                },
            ),
            # §B.2.3: an id that is not this bot's answers the same empty
            # report — "no such record", never "there is one, but not for you".
            _step(
                "GET",
                _MANIFEST + f"/applies/{UNKNOWN_APPLY_ID}",
                expect={"data": {"apply_id": "", "result": "", "trigger": ""}},
            ),
            # §2.1 + §B.1.4: capability is decided by engine × bot type alone —
            # no container is consulted, so this answers before a bot has one.
            _step(
                "GET",
                _MANIFEST + "/capabilities",
                expect={
                    "data": {
                        "bot_id": BOT_ID,
                        "engine_type": "openclaw",
                        "bot_type": "personal",
                        "schema_versions": [1],
                    }
                },
                extract={"constructs": "data.constructs"},
            ),
        ],
    ),
    # ── §B.1.2 + 附录 C: a refused document names every reason and stores
    # nothing. The manifest is still absent afterwards.
    FlowCase(
        name="manifest-refused-put-stores-nothing",
        covers=[],
        steps=[
            _step(
                "PUT",
                _MANIFEST,
                body={"document": INVALID_DOCUMENT},
                expect_status=422,
                expect={
                    # §B.0: 422109 is the manifest-validation subcode. Branch on
                    # ``code``, never on ``message``.
                    "code": 422109,
                    "data": {
                        "violations": [
                            {
                                "location": "manifest.engine_config",
                                "code": "unsupported_category",
                            },
                            {
                                "location": "manifest.identity[0].type",
                                "code": "reserved_identity_type",
                            },
                        ]
                    },
                },
                extract={"violations": "data.violations"},
            ),
            # A rejected write is not a partial write.
            _step("GET", _MANIFEST, expect={"data": {"document": "", "size_bytes": 0}}),
        ],
    ),
    # ── §B.0.1's most-stepped-on rule, as its own case: a missing ``user_id``
    # is a 422, not "handled as some default identity".
    FlowCase(
        name="manifest-requires-a-user-id",
        covers=[],
        steps=[
            FlowStep(
                method="GET",
                path=_MANIFEST,
                headers=HEADERS,
                expect_status=422,
            ),
        ],
    ),
    # ── §4.6 step 2 + §B.1.2: an accepted document is stored byte-for-byte and
    # an apply follows it immediately, handed back as a pollable id.
    FlowCase(
        name="manifest-put-stores-and-starts-an-apply",
        covers=[],
        steps=[
            _step(
                "PUT",
                _MANIFEST,
                body={"document": SCRIPT_DOCUMENT},
                expect={
                    "code": 200000,
                    "data": {
                        "document": SCRIPT_DOCUMENT,
                        "size_bytes": len(SCRIPT_DOCUMENT.encode("utf-8")),
                        "schema_version": 1,
                        # §B.1.2: stamped from the principal, never from the body.
                        "updated_by": OWNER,
                        # §B.1.2: the *start result*, not a report. ``reason`` is
                        # populated only when nothing started.
                        "apply": {"result": "RUNNING", "reason": None},
                    },
                },
                extract={
                    "apply_id": "data.apply.apply_id",
                    "put_warnings": "data.warnings",
                },
            ),
            # §B.1.1: a read never carries a start result — that field belongs
            # to the write that started one.
            _step(
                "GET",
                _MANIFEST,
                expect={"data": {"document": SCRIPT_DOCUMENT, "apply": None}},
                extract={"read_warnings": "data.warnings"},
            ),
        ],
    ),
    # ── §4.7 + §B.2.5: the report the caller reads to answer "did my manifest
    # take effect". Runs after the runner drains the enqueued task.
    FlowCase(
        name="manifest-report-after-put",
        covers=[],
        steps=[
            _step(
                "GET",
                _MANIFEST + "/applies/{apply_id}",
                expect={
                    "data": {
                        "bot_id": BOT_ID,
                        # §B.7: the auto-apply that follows a PUT is ``put``.
                        "trigger": "put",
                        "result": "SUCCEEDED",
                        "entries": [
                            {
                                "category": "script",
                                "name": "script",
                                # First time the area is written.
                                "action": "created",
                                "error": None,
                            }
                        ],
                    }
                },
                extract={
                    # ``expect`` is matched verbatim — only paths and request
                    # bodies are interpolated — so ids that must equal an
                    # earlier step's are chained through the runner instead.
                    "report_apply_id": "data.apply_id",
                    "report_entries": "data.entries",
                    "report_categories": "data.categories",
                    # §B.2.5: ARCA-family applies leave ``notes`` empty; it
                    # carries teclaw's whole-artifact redelivery failure only.
                    "report_notes": "data.notes",
                    "report_finished_at": "data.finished_at",
                },
            ),
            # §B.2.4: ``last-apply`` is the authoritative "did it land" answer,
            # and it is the same record the handle names.
            _step(
                "GET",
                _MANIFEST + "/last-apply",
                expect={"data": {"trigger": "put", "result": "SUCCEEDED"}},
                extract={"last_apply_id": "data.apply_id"},
            ),
        ],
    ),
    # ── §3.2 + §4.8: applying the same declaration again converges. This is the
    # feature's central promise — "already correct" must be zero action, not a
    # rewrite that reports success.
    FlowCase(
        name="manifest-second-put-converges",
        covers=[],
        steps=[
            _step(
                "PUT",
                _MANIFEST,
                body={"document": SCRIPT_DOCUMENT},
                expect={"data": {"apply": {"result": "RUNNING", "reason": None}}},
                extract={"apply_id_2": "data.apply.apply_id"},
            ),
        ],
    ),
    FlowCase(
        name="manifest-convergence-report",
        covers=[],
        steps=[
            _step(
                "GET",
                _MANIFEST + "/applies/{apply_id_2}",
                expect={
                    "data": {
                        "trigger": "put",
                        "result": "SUCCEEDED",
                        # §B.7: "already the declared shape, zero action —
                        # this is normal."
                        "entries": [{"category": "script", "action": "unchanged"}],
                    }
                },
                extract={"converged_entries": "data.entries"},
            ),
        ],
    ),
    # ── §B.2.1: an explicit apply takes no body — the document to apply is the
    # one already stored — and answers 202 with a handle.
    FlowCase(
        name="manifest-explicit-apply",
        covers=[],
        steps=[
            _step(
                "POST",
                _MANIFEST + "/apply",
                expect_status=202,
                expect={"code": 200000, "data": {"result": "RUNNING"}},
                extract={"apply_id_3": "data.apply_id"},
            ),
        ],
    ),
    FlowCase(
        name="manifest-explicit-apply-report",
        covers=[],
        steps=[
            _step(
                "GET",
                _MANIFEST + "/applies/{apply_id_3}",
                # §B.7: ``POST …/apply`` records itself as ``explicit``.
                expect={"data": {"trigger": "explicit", "result": "SUCCEEDED"}},
            ),
        ],
    ),
    # ── §4.4 + §B.2.2: a dry run is synchronous (200, not 202), mints no id,
    # and must not disturb the standing report.
    FlowCase(
        name="manifest-dry-run-plans-without-recording",
        covers=[],
        steps=[
            _step(
                "POST",
                _MANIFEST + "/apply",
                query={"dry_run": "true"},
                expect_status=200,
                expect={
                    "data": {
                        # A preview mints no handle.
                        "apply_id": "",
                        # §B.7: the preview report's own trigger value.
                        "trigger": "dry_run",
                        "entries": [{"category": "script", "action": "unchanged"}],
                    }
                },
                extract={"dry_run_entries": "data.entries"},
            ),
            # §B.2.2: it writes no apply record, so the last real apply still
            # stands.
            _step(
                "GET",
                _MANIFEST + "/last-apply",
                expect={"data": {"trigger": "explicit"}},
                extract={"last_apply_id_after_dry_run": "data.apply_id"},
            ),
        ],
    ),
    # ── §B.1.3 + §3.3: DELETE clears the *declaration* and nothing else. It is
    # idempotent, and it applies nothing.
    FlowCase(
        name="manifest-delete-clears-the-declaration",
        covers=[],
        steps=[
            _step("DELETE", _MANIFEST, expect={"data": {"deleted": True}}),
            _step(
                "GET",
                _MANIFEST,
                expect={
                    "data": {
                        "document": "",
                        "size_bytes": 0,
                        "schema_version": None,
                    }
                },
            ),
            # Idempotent: deleting an absent declaration still answers deleted.
            _step("DELETE", _MANIFEST, expect={"data": {"deleted": True}}),
            # §B.1.3: it removed no entity and started no apply, so the standing
            # report is untouched.
            _step(
                "GET",
                _MANIFEST + "/last-apply",
                expect={"data": {"trigger": "explicit"}},
                extract={"last_apply_id_after_delete": "data.apply_id"},
            ),
        ],
    ),
]
