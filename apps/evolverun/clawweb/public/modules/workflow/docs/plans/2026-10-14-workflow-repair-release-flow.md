# Workflow Repair release flow implementation plan

> For agentic workers: use superpowers:executing-plans inline. The user explicitly requested continuous implementation; do not ask for another stage approval. A single fresh whole-branch review follows full verification; no per-task subagents.

**Goal:** Deliver one usable task flow from readable Git changes through exact candidate approval/publication to attributable post-deploy validation.

**Architecture:** Avernet owns contracts, durable task transitions, HTTP/UI and verification data. OCB composes authenticated Bot dispatch and Git access. ClawMind publishes the immutable, approved full Pack through its existing publication implementation. TaskGuard generation stays unchanged.

**Tech Stack:** TypeScript, React, React Query, Express, SQLite/MySQL/OB, Git, Vitest and existing ClawMind node tests.

**Spec:** [Workflow Repair release flow](../specs/2026-10-14-workflow-repair-release-flow.md)

## Global constraints

- No live deployment or automatic real business test runs during implementation/acceptance.
- Publish the reviewed candidate commit, never latest saved/workspace content.
- Keep input/evidence/actor/target authority server-side; authenticated routes and explicit edit/publication authorization.
- Candidate approval and deployment are separate durable actions.
- Idempotency and durable reconciliation cover lost responses, crashes and retries.
- Matching workflow_deploy_number is required for effect attribution; insufficient data cannot close a problem.
- Preserve dirty pre-existing worktrees and their unsubmitted changes.
- Semantic UI tokens; source files below 1,000 lines; exports-only package imports.
- Config stays in existing config files/OB records, credentials in existing credential providers.
- Bounded Git diff and runtime payloads; private logs do not contain secrets.

## Review focus

1. Reviewed historical revision changes while a new AIS attempt is running: block stale approval/publication, preserve the successful candidate.
2. Git publication succeeds but DB acknowledgement fails: reconcile the same publication identity without another release.
3. Pack includes scripts/resources in addition to YAML: reviewed and deployed trees must match in full.
4. Runtime rows lack deployment identity or mix old versions: exclude them and expose insufficient evidence.
5. Read-only users, long/truncated files, nested fences and multiple changed files: no hidden active actions, no fabricated complete review.

## Task 1: durable public release contract and approval

**Files:** workflow/server/contracts/repair-release.ts (new); workflow/server/repositories/repair-release-repository.ts (new); shared/server/schema-repair-release.ts (new); workflow/server/services/repair-release-service.ts (new); repair-workbench.ts, repair-batches.ts, repair-batch-service.ts; tests adjacent to those sources.

Paths above are relative to apps/evolverun/clawweb/public/modules. Use fresh Avernet checkout at /Users/wenyang/.codex/worktrees/workflow-repair-release-flow/Avernet.

**Interfaces:** Produce RepairApproval, RepairDeployment, RepairVerification, RepairPublicationPort and RepairReleaseService. Mutations accept taskId/revision/stateVersion/requestId plus risk acknowledgements/verified item conclusions; server selects and freezes candidateCommit, checksDigest, baselineDeployNumber, target and actor. Publication port uses dispatch(frozenDeployment), reconcile(deployment) and authorizedTargets(workflowId,actorId).

```ts
type DeploymentState = 'created' | 'dispatching' | 'uncertain' | 'running' | 'failed' | 'succeeded';
type ReleaseRequest = { revision: number; expectedStateVersion: number; requestId: string };
type PublicationReceipt = { requestId: string; candidateCommit: string; packDigest: string;
  deployNumber: number; releaseVersion: number; tagName: string; targetId: string };
```

- [ ] Write failing repository/service/route tests with real SQLite transactions. Test approve preserves active release, stale candidate/failed checks/unresolved items cannot approve, duplicate request is stable and request reuse with different body is rejected. A nonexistent approval cannot deploy.
- [ ] Run affected Vitest tests and observe assertions/imports fail for the missing release capability.
- [ ] Implement additive migrations and CAS transitions. Store review, deployment request and verification evidence separately. Extend task detail/capabilities without changing generation/retry contracts. Routes use existing verified actor/workflow access adapters, including publication-specific target checks.
- [ ] Verify approval stale guards and publication terminal transitions. A matching successful receipt moves only applied items into awaiting_verification; unrelated/unresolved items stay open. Cancellation cannot rollback or release an in-flight deployment lock.
- [ ] Run workflow/server package tests and migration conformance; expected zero failures. Commit only tested coherent changes (no cosmetic micro-commits).

## Task 2: exact reviewed candidate publisher and host wiring

**Files:** ClawMind/src/controller/repair-publication.ts (new), src/git/candidates.ts, src/controller/version-commands.ts, src/command-parser.ts, src/dispatch.ts, src/db/api-repositories/evolve-api-repository.ts, docs/workflow-save.md and docs/INDEX.md where present; matching tests. OCB internal/bootstrap/clawweb/server/repair-publication.ts (new), index.ts, repair-git-store.ts and their tests.

**Interfaces:** Consume frozen deployment identity from Task 1. Produce deployReviewedCandidate(deps,request) returning PublicationReceipt, plus host createRepairPublicationPort(...). Claim/complete callbacks are authenticated, bind request+Bot+actor, and never trust a browser-supplied commit or unauthenticated result.

```ts
type ReviewedCandidate = { workflowId: string; packId: string; specPath: string;
  candidateCommit: string; packDigest: string; specDigest: string;
  baselineDeployNumber: number; deploymentId: string; requestId: string };
```

- [ ] Write a local bare-Git integration test: reviewed A and latest saved B differ; publication must release A, including script/resource files. Seed active deployment N; N+1 mismatch blocks before writes. A changed target/receipt digest cannot succeed.
- [ ] Run focused ClawMind/OCB tests and observe the missing exact candidate publisher fail.
- [ ] Add a typed reviewed-publication command that reads its trusted claim through the API repository, verifies exact source objects and baseline, exports to an isolated full Pack and invokes existing candidateSnapshot publication. Do not generate new code with an LLM or overwrite local user edits. Bind the tag/history note to deployment identity so a lost acknowledgement can be reconciled.
- [ ] Compose target resolution and dispatch via the existing authenticated host/Bot runtime transport. Freeze target owner/Bot/environment. Persist request before dispatch; an unknown response is uncertain, not failed/successful. Use same identity to recover Git/history/activation facts.
- [ ] Test interrupted tag/history publication, retry and active target activation. Existing save/deploy/rollback tests must remain green. Host verifier must pass against public package exports.

## Task 3: truthful post-deploy evidence and per-item conclusions

**Files:** workflow/server/services/repair-verification-service.ts (new), repair-release-repository.ts, repair-release-service.ts, flow-run-repository.ts, repair-batches.ts and service/routes tests.

**Interfaces:** Consume successful PublicationReceipt and approved baseline; produce EffectReport with deployment identities, baseline/candidate sample counts, excluded unknown rows, rates, durations/tokens, signature evidence references and verdict collection.

```ts
type VerificationVerdict = 'observing' | 'verified' | 'ineffective';
type VerificationConclusion = { itemId: string; verdict: VerificationVerdict;
  reason: string; flowIds: string[] };
```

- [ ] Write tests with literal runs for baseline, candidate, another deployment and missing deployment identity. Only exact identity matches enter the report; zero candidate rows yields insufficient evidence rather than a pass. Supplied evidence must belong to this workflow/deployment.
- [ ] Run those tests RED, implement bounded queries and durable conclusions, then run GREEN.
- [ ] Use real terminal execution evidence; no automatic business-triggering runs. Persist actor, evidence and reason; close only explicitly verified applied items. Ineffective keeps the actual deployment fact and opens a new repair path; observing never closes.
- [ ] Exercise deployed task cancellation, rollback history and empty/failed query paths. Existing run visibility restrictions remain enforced.

## Task 4: genuine Git diff and complete task UI

**Files:** workflow/server/contracts/repair-workbench.ts; OCB/server/repair-git-store.ts; workflow/web/components/workflow-workspace/repair-batch/RepairCandidateDiff.tsx (new), RepairReleaseActions.tsx (new), RepairVerificationPanel.tsx (new), RepairTaskPanel.tsx, RepairWorkspace.tsx or the actual task caller discovered by imports; web/api/repair-batches.ts; focused UI tests.

**Interfaces:** Consume release detail and EffectReport. Extend RepairDiff with per-file counts and bounded Git hunks; maintain old file payload compatibility without using truncated text to infer complete diffs. Fetch file/page context by exact candidate/base identity.

```ts
type DiffLine = { kind: 'context' | 'add' | 'delete'; oldLine: number | null;
  newLine: number | null; text: string };
type DiffHunk = { oldStart: number; newStart: number; lines: DiffLine[] };
```

- [ ] Write RED tests for deep-only changes, binary/large/truncated files, two files, empty/no-change, additions/deletions and long lines. Opening shows first change with line numbers; previous/next navigation reaches the right hunk. Unified/parallel views use aligned rows and shared scrolling.
- [ ] Implement bounded unified Git diff reads and a line renderer with context folding, file stats and clipboard icons. Preserve digest/base labels and cache identity; truncated/failed reads are explicit and cannot count as complete review.
- [ ] Write RED tests covering approve without deploy, deploy confirmation target/risk summary, dispatch uncertainty then reconcile, failed deployment retry, successful deployment → verification, no samples, per-item ineffective/verified outcomes, and read-only/historical revision actions.
- [ ] Implement a sticky step-aware action bar and clear step status. Primary action: confirm candidate, deploy approved candidate, reconcile uncertain publication or view verification depending on durable state. Secondary: request changes. Show actionable capability reasons instead of blank disabled buttons.
- [ ] Run all workflow UI tests, typecheck and build; no cosmetic-only partial delivery.

## Task 5: integrated acceptance and one final review

**Files:** new local integration/Playwright acceptance fixture under workflow tests or ignored test-results; public and host workflow-repair docs; approved design/plan progress record.

**Interfaces:** Consume the actual HTTP/UI, SQLite persistence, local Git publication adapter and deterministic runtime fixture; external network/model calls only replaced at the boundary. The product state machine, diff generation and deployment receipt validation are real.

- [ ] Run one complete scenario: selected suggestions → generated candidate → visible diff → approve → explicit deploy → exact release receipt → new-version executions → verification verdict. Verify Git tag tree and DB active deployment, not only button rendering or fake callback counts.
- [ ] Run failures: stale baseline, denied actor, invalid checks, changed reviewed commit, lost dispatch reply, Git-success/DB-failure, retry, unknown run identity, insufficient samples and ineffective outcome. Reload UI at every durable stage.
- [ ] Capture desktop screenshots and network assertions; inspect scroll/focus/contrast/disabled reasons and safe copy controls. No live preprod publish.
- [ ] Run each affected package suite/typecheck/build, ClawMind release/runtime tests and OCB verify_clawweb_workspace boundary gate. Record exact counts and any skipped/unavailable integration proof.
- [ ] Dispatch one fresh whole-branch reviewer using the executing-plans/requesting-code-review skills. Fix result-changing findings with RED→GREEN evidence; leave unrelated work untouched.
- [ ] Final handoff identifies commits, local acceptance evidence, required release/config steps, and what was not proven live. PR creation/merge/live deployment are separate authorized actions.
