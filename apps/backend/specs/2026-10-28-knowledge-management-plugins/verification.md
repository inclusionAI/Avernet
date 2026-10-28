# Verification — 2026-10-28

Historical migration runtime-source candidate: `e635bb6bdb4459af7d18db5c2c2e6b7196ef0ce2`, based on authoritative development baseline `7e9a0d97817c504fbf4aad7681a33b3a8f7eb3f0`.

- Actual Backend CI script with four workers and unchanged gates: **26760 passed, 40 pre-existing skipped, 2 subtests passed**. The existing skipped tests are not claimed as executed acceptance; new knowledge and endpoint cases have no skips.
- Total line coverage **90.58%** (required75%); changed-line coverage **93.49%** (required80%).
- Explicit 24 management/content method/path entries each have positive and disabled-default HTTP cases: **48 passed**. They exercise real DI, SQLite, orchestration and HTTP adapters against scripted remote responses, not remote engine behavior.
- Initial full run surfaced endpoint-registry and worker-boot expectations plus a global-name static-analysis collision for the synchronous command wrapper. Cases and expectations were added and the sync import disambiguated without changing its behavior. An existing subprocess test timed out under the initial12-worker load; it passed unchanged in isolation and the final four-worker full run. No timeout or coverage threshold was increased.
- Public source import/credential ownership guards, shared-pool reuse/cookie isolation, cancellation cleanup, terminal lifecycle teardown and resource ownership contracts pass.
- Normal push secret scanning and SAST hooks passed. Their default lint-only mode is distinct from the separately executed full Backend CI.

No native remote ACL execution, deployed environment, real document construction, frontend, publication-stage isolation or MCP consumption is claimed. At that migration snapshot, per-library detail fan-out and aggregate slow-shard deadline limitations remained follow-up work. Batching is superseded by the final follow-up below. No DDL or deployment was performed.

## Batch-detail follow-up
- Rebased on authoritative public dev `8f71072ba`. This adds existing BaaS/frontend baseline updates; the knowledge follow-up does not modify those modules.
- Public knowledge/session/endpoint focused regression: **278 passed, zero skips**.
- New wire assertions cover 1/20-item pages, empty pages, duplicate/oversize input, whole-batch errors, ordered exact-ID/space matching and malformed/partial results. Full available-page HTTP counts are 2 for 1 or 20 items (one authorization list plus one batch), and 1 for an empty page.
- The original sync-context HTTP error body is restored and asserted. Missing remote batch support fails closed; no per-ID fallback.
- Full Backend gates and downstream pairing are recorded after their final runs. Slow-stream aggregate deadlines and pre-body raw disconnect cleanup remain unresolved and are not claimed fixed. No deployment or schema change.

## Final batch follow-up gates
- Frozen authoritative baseline: `b344e952afb5f3935644ca06708db155366d80f8`; tested runtime/test candidate: `7fcd8b8ed54ed8c57c8b5ecf6cfdf81754119867`. The public knowledge production source is identical to the batch fix before rebase; the final test commit also updates the registered endpoint fixture to require the batch POST body.
- Actual unchanged-threshold Backend CI, four workers: **26787 passed, 40 pre-existing skipped, 2 subtests passed**; total line coverage **90.58%**, changed-line coverage **93.76%**. Exit code 0.
- Registered new management/content positive and disabled-default cases: **48 passed**. The stale single-detail response fixture first reproduced a 503, then passed after adding the exact method/body batch contract. No production fallback or permission relaxation was added.
- Downstream enterprise CI on its user's latest rollout configuration: **2846 passed**, total line coverage **90.71%**, changed-line coverage **96.52%**. Fixed receiving-provider Router/SQLite pairing at `4416df9fb0`: **31 passed, zero skips**. This is local pairing, not deployed acceptance.
- Source scope error is `KNOWLEDGE_SCOPE_MISMATCH`; 1/20-item nonempty candidate pages use two HTTP requests including authorization listing, and empty pages use one. No per-ID fallback.
- The earlier interrupted pre-rebase run is not counted as passed. No threshold or timeout was raised. This follow-up changes neither publication-stage configuration nor capture defaults. Strict slow-stream deadlines and pre-body raw disconnect cleanup remain unresolved. No deployment or schema changes.

## Reviewer simplification and REL20261029 conflict repair
- User-selected contribution target: `REL20261029@0317937dfaa12ed97ff2ca1a3ad0c4c5b7d57b7f`. Tested source/test commit: `93fff5ab7fd5681a4c373cab2c1f95575edac039`; the follow-up documentation commit changes no runtime or tests.
- Rebased the existing feature branch rather than merging target history. In `api/README.md`, preserve both the release Bot-risk protocol dependency and the knowledge-content kernel dependency. In the E2E registration table, preserve both release risk flows and knowledge management flows, each once.
- AST comparison confirms all release `build_injector` module calls remain in the same relative order; BotPublicRiskModule, TaskArtifactConfigModule and TcFileUploadIntegrationModule remain present. The application composition root and release risk implementation files are byte-identical to the release target.
- Remove the session Plugin Protocol and unavailable placeholder. The ordinary access policy still denies by default; downstream retains its identity match and Cookie allowlist policy. Normal management/content routes mount during phase-one application construction through the existing resource registration function; they do not depend on OptionalRouters. Existing Bot-alias admission remains separate. `app.py` is 1,000 lines without raising or waiving the file-size gate.
- New composition guards initially reproduced three failures before the simplification. After the release rebase, knowledge/real-provider-endpoint focused tests: **342 passed**, 1,708 unrelated cases deselected, zero skips. Separate composition/file-size/E2E-registration check: **14 passed**. Both provider-mutation guards exercise the formal factories rather than replacing business services in the seed.
- Complete unchanged-threshold Backend CI, four workers: **27,070 passed, 40 skipped, 1 failed, 2 subtests passed** in 942.99 seconds; **the complete gate exited 1, not green**. The only failure was the unchanged `test_artifact_command.py::test_deadline_cancels_writer`: its initial `python3` control subprocess exceeded its existing one-second timeout and raised subprocess.TimeoutExpired instead of TimeoutError. The test and production implementation are byte-identical to the release target. Its complete three-test file subsequently passed unchanged in isolation (5.68 seconds). This does not establish a clean full-suite run or claim a reproduced clean-baseline failure. No timeout, coverage threshold or skip was changed.
- The complete run includes all 23 worker-boot tests, the source-size gate, and the risk/knowledge E2E registration checks; those passed. Normal pre-push lint/SAST preflight passed; it is not a substitute for the failed full gate. Full-run coverage is retained in the local XML, but the full gate stopped before its final report checks.
- Independent subagent review of fixed source `93fff5ab7`, the release conflict resolutions, unchanged provider ordering, normal unique routing, default denial and preserved batch/scope contracts found no evidenced P1/P2 findings in that scope. It did not independently rerun full CI or review the downstream corp branch.
- No public or enterprise PR merge, deployment, live SSO/data-plane acceptance, DDL or publication-stage isolation was performed. Existing slow-stream aggregate deadlines and pre-body raw disconnect cleanup remain unresolved.

## Bounded-query consumer update
- Started from the current public PR Head `feb30b203`, preserving the release rebase, ordinary access policy and normal route composition. The patch changes only management grant-state and available-KB reads plus matching tests/contracts.
- Focused knowledge, configured real endpoint and composition suite: **363 passed**, zero skips. Exact state POST envelope/order validation, 20-target one-request budget, malformed/legacy receiver rejection, page limits and decreasing opaque cursors are covered.
- Downstream enterprise focused suites: **120 passed**. Real OCB -> receiving-provider HTTP/SQLite pairing against the fixed bounded-query receiver: **34 passed**, zero skips. New cases exercise 501 revoked historical rows, missing-zero and composite-owner state, soft-deleted Bot CAS revoke, full multi-page traversal and continuation after anchor revocation.
- Changed Python Ruff/format and whitespace checks passed. Complete public Backend CI is running; this focused result does not claim its final status. Full downstream CI and remote mirror/pin delivery are recorded after completion. No live deployment, data-plane acceptance or schema changes.

### Complete bounded-query validation result
- Public complete Backend CI used the runner's unchanged default 12 workers: **27,089 passed, 40 skipped, 3 failed, 2 subtests passed** in 857.92 seconds. **The full gate exited 1, not green**, and stopped before its final report checks. No timeout, skip or coverage threshold was relaxed.
- Failures: `test_first_activation_is_idempotent_across_real_processes` (20-second subprocess timeout), `test_schema_invalid_output_retried_then_completed` (reschedule vs complete), and `test_bot_agent_runs_a_real_swarm_round` (agent completion assertion). All three tests and the related membership/swarm production trees are byte-identical to `REL20261029@0317937dfaa12ed97ff2ca1a3ad0c4c5b7d57b7f`. The three tests passed unchanged in isolation on both the feature (**3 passed**, 5.47s) and a target-only archive (**3 passed**, 5.82s). This supports a scheduling/load-sensitive suspicion, not a clean complete-suite result or a reproduced clean-target failure.
- Enterprise complete Backend CI passed: **2,849/2,849**, mandatory knowledge suites zero skips, total line coverage **90.72%**, changed-line coverage **97.22%**. Fixed real receiving-provider HTTP/SQLite pairing: **34 passed**, zero skips.
- Tested runtime/test commit: `39a0bc1d6`; subsequent commits change specifications and evidence only. Public and private business files did not change after these runs. No shared-stack/live prepub acceptance is claimed.

## Candidate index regression (2026-10-28)
- Rebased onto `REL20261029@e307d7109` before this fix.
- RED: 9 regression cases failed with the old metadata. Empty pages used the
  integer primary key and cost 3,093 / 30,093 / 300,093 SQLite VM instructions
  for 1k / 10k / 100k other-space rows.
- GREEN: 10 tests passed, zero skips. Empty pages cost 120 instructions at all
  three sizes and use the composite scope index. Sparse continuation isolates
  other tenants, environments and removed Bots even with a legacy space index.
- Existing-table migration control: with the legacy single-space index, 10k
  other-tenant rows in the same space cost 90,119 steps; applying the checked-in
  SQL locally reduces this to 85, with identical empty-page output. SQL matches
  ORM MySQL compilation and retains the unique identity constraint.
- These are local SQLite results, not remote engine latency guarantees.
  Production DDL was not executed. See candidate-index.md for operator steps.
