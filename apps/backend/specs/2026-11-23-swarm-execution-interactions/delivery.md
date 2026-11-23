# Local implementation and verification

## Record

- Baseline: `2d4c15db9dce7ac8d7282e870a301d77c87ef442`.
- Implementation is in the working tree; no commit/push, merge or deployment is
  claimed for this change.
- Durable contract: [Swarm execution](../../src/agentclaw/community/core/task/context_collab/l5_methods/SWARM-EXECUTION.md).
- Scope/decisions: [spec](spec.md); ordered work: [plan](plan.md).

## Implemented

- `POST .../swarm/step`, `GET .../swarm/stages`, `POST .../swarm/interactions`.
- One new Stage maximum per step; async observation without redispatch; explicit
  uncertain-dispatch reporting instead of an automatic external retry.
- Full configured graph previews, actual inputs/outputs, responsibilities,
  executor/Task state, relationships, logical sessions, feedback and history.
- Planner-adjudicated ignore/full/from-Stage execution revisions. Pinned DAGs
  remain immutable; successful unaffected work may be reused. Atomic Method
  revision CAS revokes previous live allocations and fences late reports.
- Exact-worker clarification, recipient-addressed persistent notifications,
  waiting projection, authorized human reply and cooperative worker continuation.
- Reserved coordination Event schemas, request/decision/result journaling and
  same-command/actor replay. Generic append cannot forge internal events.
- New API re-export, DI composition/alias, conformance registry and owning docs.
- No new SQL table or migration; existing Event storage is reused.

## Executed verification

All commands run from `apps/backend` with `DEPLOY_PROFILE=test uv run --no-sync`.

### Feature and affected compatibility suites

```bash
pytest -q \
  tests/community/core/task/context_collab/test_swarm_execution_api.py \
  tests/community/endpoints/test_swarm_execution_endpoints.py \
  tests/community/core/task/context_collab/test_event_schema.py \
  tests/community/core/task/context_collab/test_swarm_workflow*.py \
  tests/community/core/task/context_collab/products/test_swarm_workflow_end_to_end.py \
  tests/community/di/modules/test_swarm_runtime.py
```

123 passed at that checkpoint. Five additional pure revision/CAS/guard tests
passed. After the final DI/planner isolation and uncertainty-projection changes,
execution API + revision + HTTP + Event schema suites passed **64 tests**,
including two final race tests for exact initial-request CAS fencing.
Coverage includes synchronous/asynchronous steps, exact replay, concurrent
same-request calls, lost result/adoption/dispatch acknowledgements, full/partial
replanning, output reuse, late reports, invalid planner/session/reply, scoped
worker reads, exact actor type checks, synchronous worker questions, generic
Event forgery rejection and immutable projection/provider boundaries.

### Broad regression and architecture

```bash
pytest -q \
  tests/community/core/task/context_collab \
  tests/community/di/modules/test_swarm_runtime.py \
  tests/community/di/test_l4_collab_service.py \
  tests/community/repository/task/context_collab \
  tests/community/endpoints/test_swarm_execution_endpoints.py \
  tests/community/endpoints/test_context_collab_methods_endpoints.py \
  tests/community/architecture
```

**2519 passed, 186 failed, 43 warnings**. Architecture separately passed
**447 tests**, including the new Service API pair and declared dependency gate.
A temporary clean detached worktree at the exact baseline ran the same broad
non-architecture scope (without the new tests): **2032 passed, 186 failed**.
The 186 failing node IDs match exactly: **zero new failures**. The temporary
worktree was removed; no other worktree was changed.

Existing failures include legacy `space`/`to_space` and record shape assumptions,
removed convenience APIs, and two old Method HTTP assertions expecting 422 where
the baseline returns 200. They were not skipped/xfail'ed or "fixed" by restoring
obsolete contracts. Initial new reserved-schema failures were resolved by evolving
the schema conformance tests with the new registered/internal Event semantics;
they are not classified as baseline failures.

### Static/document checks

- Changed Python files: severe Ruff rules (`E9,F63,F7,F82`) pass.
- New execution/revision/DI/HTTP/API/test modules: default Ruff checks pass.
- All 29 added/modified Python sources are at most 1000 lines (largest: 952).
- `git diff --check` passes; 305 changed-Markdown local-file links were checked.

## Remaining gaps

The complete Backend CI/coverage pipeline has not passed or been claimed; broad
regression still has the baseline failures above. No live external Agent planner,
Engine chat session, machine HTTP authentication ingress, external IM/push,
remote cancellation, lease renewal, distributed execution or durable MethodStore
recovery is integrated here. The public default planner explicitly ignores
replanning; a host must bind its planner for other modes. Workers request/read
clarification through the trusted Service API and must cooperatively consume
replies. Persistent notifications are exposed through polling/Event reads.

## Commit preparation follow-up (2026-11-23)

- Implementation commit: `7b9687c5f1` (`feat(backend): add Swarm stage execution and interaction APIs`).
  This supersedes the initial working-tree-only status above; merge and deployment
  are not claimed.
- The HTTP fast gate initially found 41 uncovered routes, including the three new
  Swarm execution endpoints. Added real full-app registered happy/error cases for
  each new endpoint rather than expanding the frozen coverage baseline.
- Registered runner: `pytest -q tests/community/endpoints/test_endpoint_runner.py -k swarm_execution`
  passed **6 tests**. Cases retain real business services and SQL persistence;
  only the worker/evidence and publication-authorization capability seams are
  supplied through test DI. Assertions check dispatch, DAG preview, persisted
  feedback and that unknown-binding requests cannot dispatch additional work.
- Combined execution/revision/HTTP/schema, HTTP fast checks, coverage-framework
  helpers and endpoint no-mock guards: **137 passed, 1 failed**. The sole failure
  is `test_endpoint_coverage_against_baseline`, reporting 38 existing Method routes.
  A clean detached worktree at the exact parent reproduced that failure and the
  same 38 rendered gaps; set comparison found **zero new gaps**. The frozen
  baseline and gate remain unchanged.
- The earlier combined architecture + feature + HTTP fast run passed **561 tests**
  with the same coverage-gate test failing before the new endpoint registrations
  were added. Architecture source has not changed in this follow-up.
- The new registered-case module is 171 lines and passes default Ruff checks;
  the feature commit passes the repository's local Python SAST gate.
- Full Backend CI/coverage and remote runtime integration remain unverified.
