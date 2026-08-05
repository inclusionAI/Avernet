"""bot_chat API-lifecycle business flows — data, not tests.

Single source of truth for both executors:
  - 路 A: tests/e2e/test_bot_chat_flows.py via flow_runner.run_flow (TestClient)
  - 路 B: tests/acceptance/bot_chat/ via flow_runner_live.run_flow_live (real backend)
and for the E3 coverage guard (tests/architecture/test_e2e_module_coverage.py).

These shared query flows are read-only. The in-process runner seeds
aw_langfuse_traces + ac_bots rows directly into SQLite before the flow runs
(see tests/e2e/test_bot_chat_flows.py), then asserts the API surfaces them.
Live OTLP ingestion and relation writes are covered separately by
tests/acceptance/bot_chat/test_otel_roundtrip_lifecycle.py.

LOCAL+SQLITE, mock-free for the db source. The langfuse source (log_source=langfuse)
and /health endpoint hit an external Langfuse HTTP API (https://example.com)
and are documented as known findings — NOT covered by these flows; see
docs/singlebox-eval/findings/bot_chat-langfuse-no-plugin-protocol.md.

Each FlowCase covers=["bot_chat"]. Auth via x-user-id:
  - route A default "e2e_user" (flow_runner.py)
  - route B default "e2e_lifecycle_user" (flow_runner_live.py)
Expects use literal values matching the route-A default; route-B passes
default_headers={"x-user-id": "e2e_user"} to override.

Gotcha: FlowRunner.run_flow does NOT interpolate `expect` (only path/body).
expects use literal values; the extract→interpolate chain is proven via path
interpolation in Flow 3 (broken extract would 422/500 the next step, not
silently pass).
"""
from __future__ import annotations

from tests.community.framework.flow import FlowCase, FlowStep

BOT_CHAT_LIFECYCLE_FLOWS: list[FlowCase] = [
    # Flow 1: list with no seed — empty result. Pins LOCAL "no data" contract:
    # success=True, sessions=[], total=0 (NOT 500/no such table — that bug was
    # fixed in commit 366343614; pinning the post-fix state prevents regression).
    FlowCase(
        name="bot_chat-list-empty",
        covers=["bot_chat"],
        steps=[
            FlowStep(method="GET", path="/api/health", expect_status=200,
                     expect={"status": "ok"}),
            # owner_id defaults to caller's staffId (router line 49). With no seed,
            # default 72-hour window query returns empty.
            FlowStep(
                method="GET",
                path="/api/v1/bot-chats",
                expect_status=200,
                expect={"success": True, "message": "ok",
                        "data": {"sessions": [], "total": 0, "page": 1, "limit": 20}},
            ),
        ],
    ),
    # Flow 2: list with one seeded trace — query returns it. The seed happens
    # in the test runner (out-of-band, not in the flow); the flow asserts the API
    # surfaces the seeded row. Filter by owner_id="e2e_user" to scope: the route-A
    # runner injects x-user-id=e2e_user, and the seed (see test_bot_chat_flows.py)
    # writes aw_langfuse_traces.user_id="e2e_user" so the query window matches.
    FlowCase(
        name="bot_chat-list-with-seed",
        covers=["bot_chat"],
        steps=[
            FlowStep(
                method="GET",
                path="/api/v1/bot-chats?owner_id=e2e_user",
                expect_status=200,
                # subset assert: cannot pin total without coupling to the test
                # runner's seed count; instead extract the first session's id
                # field (ConversationSession.id is the trace_id by schema —
                # see core/bot_chat/schemas.py:18) and let Flow 3 chain it as
                # {trace_id_for_detail}. If sessions is empty, extract throws
                # KeyError (FlowContext.interpolate is non-silent), so the test
                # fails loudly rather than passing on an empty result.
                expect={"success": True, "message": "ok"},
                extract={"first_trace_id": "data.sessions.0.id"},
            ),
        ],
    ),
    # Flow 3: detail by trace_id — chains from Flow 2's extracted id, proves
    # extract→interpolate works AND get_session returns the seeded trace.
    # Owner_id is taken from caller's x-user-id (no query param here);
    # repository.get_trace + ownership check via ac_bots gate apply.
    FlowCase(
        name="bot_chat-detail-by-trace-id",
        covers=["bot_chat"],
        steps=[
            FlowStep(
                method="GET",
                path="/api/v1/bot-chats?owner_id=e2e_user",
                expect_status=200,
                expect={"success": True},
                extract={"trace_id_for_detail": "data.sessions.0.id"},
            ),
            FlowStep(
                method="GET",
                path="/api/v1/bot-chats/{trace_id_for_detail}",
                expect_status=200,
                expect={"success": True, "message": "ok"},
            ),
        ],
    ),
]
