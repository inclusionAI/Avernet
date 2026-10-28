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
