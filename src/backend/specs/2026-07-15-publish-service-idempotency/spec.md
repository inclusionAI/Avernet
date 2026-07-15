# Publish Service Idempotency: crash-safe operations via an operation ledger

GitHub issue: [#197](https://github.com/inclusionAI/Avernet/issues/197)
(scope refined in the [issue discussion](https://github.com/inclusionAI/Avernet/issues/197#issuecomment-4978735223)).

## Summary

Every service-bot publish operation that talks to the BaaS layer — verify
release, online release (first release and upgrade), restart, scale, offline,
rollback, eval publish/teardown, and the human-approval triggers — must become
**crash-safe under sequential re-run**: the process may die at any instant, and
a later re-run of the same operation must converge to the intended end state
without creating a duplicate BaaS bot or workflow, losing a BaaS-returned
publish id, or leaving a BaaS workflow permanently unapproved.

Today only the build phase has this property. Everywhere else the pipeline asks
BaaS to mint a publish workflow id first and persists that id — and the
follow-up obligations it implies, like approve — in separate, later writes. A
crash between any two of those writes either orphans BaaS-side state or strands
the record. The existing `is_online_release_recorded` marker covers one narrow
window of one operation and does not cover approval at all.

The change introduces a persistent **operation ledger** (a dedicated table, per
the issue discussion — not more ext keys) that records intent *before* the BaaS
call and tracks each subsequent obligation (id received, approved, recorded)
as an individually completed step, so a re-run resumes at the first incomplete
step instead of blindly re-executing from the top. Alongside it, the remaining
fire-and-forget execution paths (restart, offline's background destroy) move
onto the durable task queue, approval becomes a single re-drivable step whose
failure is visible, and BaaS request ids become deterministic everywhere. The
worst structural offenders in this pipeline (multiple 100–190-line methods
mixing orchestration, BaaS I/O, and persistence) are restructured into the
step pattern as part of the same work.

## Motivation

The durable task queue is explicitly at-least-once: a task is re-run after a
crash, a lease expiry, or an error. That contract is only sound if every
handler is idempotent — and issue #197's investigation found that almost none
of the publish operations are. Concretely (all confirmed against current code):

- A crash between the BaaS create and the ext write that records its publish id
  loses the id entirely; the re-run creates a **second BaaS bot** (the
  "Option-C orphan" the code already acknowledges as accepted damage).
- A crash — or a silently swallowed approve failure; the approve helper catches
  all exceptions, returns `False`, and the release path ignores the return —
  after the id is recorded but before approve leaves the workflow **stuck
  unapproved forever**. Nothing in the system ever re-drives an approve; the
  progress poll waits on it until a 24-hour deadline and then fails the task.
- Restart is fire-and-forget (`asyncio.create_task`), clears its previous
  ext marker *before* submitting the new BaaS workflow, and swallows the
  persist failure while still approving — a pod restart mid-flight loses the
  operation with no record it ever ran.
- Offline performs three status writes with no CAS guard (duplicate drafts on
  re-run) and destroys the online bot in a fire-and-forget background task —
  process death leaks a live online bot with the DB claiming RELEASED.
- Rollback's two un-transactioned CAS writes can crash into a half-rolled-back
  state that its own precondition check then permanently rejects.
- Scale and `restart_devices` generate timestamp/uuid request ids, so retries
  of the same logical operation cannot even be correlated in logs, while the
  create/approve path reuses one request id for two logically distinct
  operations. (Note: BaaS does **not** dedup on request id at all — see
  "Recovery model" below — so recovery never rides on request ids; they are
  correlation/audit only.)
- Eval publish creates a real BaaS bot and persists nothing anywhere; a crash
  orphans the bot with no record to reconcile or tear down.
- The human-approval flow persists the workflow platform's `puid` only after
  creating the approval instance (crash → orphan + duplicate on re-run), and
  an approval that lands AGREED but crashes before triggering the release is
  never retried.

These are not theoretical: issue #151 (publish record FAILED while the publish
actually succeeded) is the visible symptom class, and #157's restart-readiness
fix depends on the restart marker being reliably written — which today it is
not. Prior fixes (#162, #168) patched individual instances of this class;
this work removes the class.

The code-quality goal rides along because it is the same surface: the crash
windows exist precisely because 100–190-line methods interleave BaaS calls and
persistence ad hoc. Restructuring them into explicit resumable steps is both
the fix's mechanism and the cleanup.

## User Stories

- As an operator, when a backend pod dies mid-publish (any stage), I want the
  flow to resume and converge on its own after restart, so that no publish is
  stranded "in progress" forever and no duplicate bot appears on the BaaS side.
- As a bot owner, when I retry a failed publish, I want the retry to pick up
  exactly where the previous attempt actually stopped (including re-driving an
  un-sent approval), so that retrying never creates a second bot or waits on a
  workflow that was never approved.
- As an operator, when an approve call to BaaS fails, I want the operation to
  report failure (and be retryable), not report success while the bot silently
  never starts.
- As a bot owner, when I take a bot offline, I want the online bot to actually
  be destroyed even if the backend restarts mid-operation, so that offline
  bots do not keep running (and billing) on the BaaS side.
- As a bot owner, when I restart a bot, I want the restart tracked durably so
  that its progress is queryable (and #157's readiness signal has data) even
  across a backend restart.
- As a maintainer, I want every BaaS mutation to carry a deterministic request
  id and be recorded in one ledger, so that any incident can be reconciled by
  asking "which step of which operation did this record reach".
- As a maintainer, I want the release/restart/offline/rollback orchestration
  decomposed into small, individually-guarded steps, so that reasoning about
  a crash at any point is tractable and new operations follow the pattern by
  construction.

## Recovery model (BaaS facts confirmed during spec review)

Verified against the in-repo BaaS server (`src/baas`), because they shape what
recovery can and cannot rely on:

- **`request_id` is correlation-only.** It is client-generated, required, logged
  and echoed back — but never persisted (no column on the publish table), never
  deduped on, and not queryable. No recovery step may rely on BaaS request-id
  semantics.
- **BaaS enforces one active publish per bot** (SVC-PUB-15 in
  `create_publish`): a second mutation on the same bot while a workflow is
  active is rejected, and BaaS self-heals its own orphan publish rows
  (publish without batch records) by failing them and letting the new publish
  proceed.
- **Workflows are queryable by bot**: `list_publishes(tenant, bot_id, status)`
  / `get_active_by_bot_id` exist server-side.

Consequences for this design:

- The ledger's resume-at-step prevents *our* blind re-issues — that alone
  closes most windows.
- For mutations on an **existing bot** (upgrade, restart, scale, stop/destroy,
  rollback deploy): if we crash after BaaS accepted but before we persisted
  the returned workflow id, the re-run recovers the id by querying the bot's
  publishes (and SVC-PUB-15 guarantees a re-issue can't stack a duplicate
  active workflow).
- For **bot creation** (first release, eval): there is no same-bot guard to
  lean on. The window between "create issued" and "response persisted" is
  closed by reconcile-before-create (query BaaS for a bot matching the
  intent's identity) if bot identity is queryable — otherwise it remains a
  **bounded, observable orphan**: the ledger records that a create was in
  flight, so the orphan is discoverable and cleanable instead of silent
  (today's behavior). See Open Questions.

## Acceptance Criteria

Crash-resume convergence (the core invariant):

- [ ] For each operation — verify first release, verify upgrade, online first
      release, online upgrade, restart, scale, offline, rollback deploy,
      eval publish, eval teardown, approval create, approval callback — killing
      the process between **any two consecutive persistence/BaaS steps** and
      re-running the operation converges to the intended end state with:
      no second BaaS bot or workflow created, no BaaS-returned publish id
      lost, and no workflow left unapproved. For the bot-creation window
      specifically, the guarantee is per the Recovery model: at worst a
      recorded, discoverable, cleanable orphan — never an untracked duplicate.
- [ ] The convergence guarantee is for sequential re-runs (crash → re-run).
      Concurrent overlap of the same task (lease-expiry double-claim) is
      explicitly out of scope per the issue discussion.
- [ ] Resume-at-step never traps a doomed operation: a user-driven retry can
      **abandon** an in-flight ledger operation and restart from an earlier
      phase (e.g. rebuild after fixing a bad artifact, then re-release). The
      abandoned operation is marked as such in the ledger — and its BaaS-side
      workflow/bot reconciled or cleaned where possible — so the escape hatch
      that exists today (phase-level retry from `source_status`) is preserved,
      not replaced, by step-level resume.

Operation ledger:

- [ ] Every BaaS mutation in the publish pipeline records intent (operation
      kind, target, deterministic request id, parameters) in a dedicated
      ledger table **before** the BaaS call is issued.
- [ ] The BaaS-returned publish id is persisted against that intent record as
      its own step; approval completion is persisted as its own step; a re-run
      of the operation resumes at the first incomplete step.
- [ ] `retry()`'s choice between "BaaS restart" and "re-run the release work"
      is driven by the ledger's per-step state, not by the coarse
      `ext.publish.online` presence check.
- [ ] Per-operation workflow state (the current `ext.restart.*`, `ext.scale.*`
      markers and the approval `puid`) lives in the ledger, shrinking the ext
      blob's read-modify-write surface.

Approval:

- [ ] There is exactly one approve step per BaaS workflow (the double approve
      in the release path is removed).
- [ ] An approve failure fails the operation step visibly and is re-driven on
      re-run; no operation reports success while its workflow is unapproved.
      (Today the failure is swallowed: the flow reports success and the record
      waits on the unapproved workflow until the poll deadline. Step-level
      re-drive replaces that; the abandonment criterion above covers the case
      where re-driving is pointless because an earlier phase must be redone.)
- [ ] Approve sends its own **client-generated** request id (we generate it
      and record it in the ledger; BaaS only echoes it), distinct from the
      create's — two different operations must be distinguishable in logs and
      in the ledger.

Durability of background work:

- [ ] No fire-and-forget `asyncio.create_task` remains in the publish
      pipeline: restart and offline's bot destroy run as durable task-queue
      tasks (same pattern as the existing verify/online tasks).

Request ids (correlation/audit only — BaaS does not consume them for
idempotency, per the Recovery model):

- [ ] Every BaaS mutation sends a client-generated request id that is
      deterministic per logical operation (stable across re-runs of the same
      operation, distinct across different operations) and is recorded in
      that operation's ledger row — so any BaaS-side log line is traceable to
      the exact ledger step that issued it. The timestamp id in scale and the
      uuid in `restart_devices` are replaced.
- [ ] No recovery logic depends on BaaS request-id semantics; recovery uses
      the ledger plus BaaS's queryable state (publishes by bot, active-publish
      guard) only.

Known-defect fixes subsumed by the above:

- [ ] Offline's status writes are CAS-guarded; a re-run cannot create a
      duplicate draft.
- [ ] Rollback cannot get stuck half-rolled-back: a crash between its two
      record flips is recoverable by re-run.
- [ ] Restart no longer clears its previous marker before the new workflow id
      is safely recorded.
- [ ] Eval publish persists the created bot_uuid/workflow id (in the ledger)
      so an orphaned eval bot is reconcilable/tear-downable.
- [ ] An approval that reaches AGREED but crashes before triggering the
      release/offline is re-driven (not stuck).

Code quality:

- [ ] The touched orchestration methods are decomposed so that no method in
      the pipeline exceeds ~80 lines or interleaves BaaS I/O with persistence
      outside the step pattern.
- [ ] The duplicated POST/error-check/extract-publish_id boilerplate in
      `baas_service.py`'s mutation methods is consolidated.

Tests:

- [ ] Crash-window tests exist per operation: simulate a kill between each
      pair of steps, re-run, assert convergence (single bot, id retained,
      approved) — against the real repository layer, not an in-memory fake.
- [ ] Full `tests/community` suite stays green.

## In Scope

- The service-bot publish pipeline under
  `core/service_bot/services/` (publish_flow_service + publish_flow/ mixins
  and runners, bot_publish_service, bot_build_service,
  publish_approval_service, publish_rollback_mixin) and the BaaS client
  methods those paths call.
- A new ledger table + repository (following the existing repository/protocol
  conventions) and its wiring into the operations above.
- Migrating restart and offline-destroy onto the existing `TaskQueueService`.
- Request-id generation policy and the single-approve consolidation.
- Method decomposition of the long methods listed in #197.
- New crash-window/resume tests; updating existing tests broken by the
  restructuring.

## Out of Scope

- Concurrent double-claim hardening (lease-expiry overlap, enqueue dedup) —
  infra-side if ever needed, per the issue discussion.
- Atomic status-advance+enqueue (outbox) and the stuck-record
  sweep/reconciler — postponed to #198.
- BaaS-server-side behavior changes — with one possible exception under
  discussion (Open Question 1b: a narrow request-id dedup addition to close
  the bot-creation window exactly). Absent that, we only consume BaaS's
  existing queryable state.
- The #157 restart-readiness UX itself (this work only makes its data source
  reliable).
- Backfilling/repairing records already stranded in production (manual
  remediation continues; #198's reconciler is the systematic answer).
- The build phase (already crash-safe) beyond touching it where the step
  pattern requires interface alignment.

## Open Questions

1. ~~BaaS request_id dedup semantics.~~ **Resolved during spec review**: BaaS
   neither dedups on `request_id` nor persists it — correlation-only (see
   Recovery model). Recovery instead uses the ledger + BaaS's per-bot
   active-publish guard + publishes-by-bot queries. Two follow-on questions:
   1a. **Reconcile-before-create feasibility.** For the bot-creation window,
       can an in-flight create be detected by querying BaaS (is the bot name
       or another identity we send unique/queryable per tenant)? If yes, the
       create window closes fully; if no, it degrades to the bounded
       observable orphan. To be answered in the plan phase by reading the
       BaaS bot model.
   1b. **Optional BaaS-side dedup.** BaaS is in this repo, so adding real
       server-side idempotency (persist `request_id`, unique per tenant,
       return the existing workflow on replay) is feasible and would close
       the creation window exactly. The issue currently scopes BaaS server
       changes out — is that worth revisiting for this one narrow addition,
       or do we stay client-side only?
2. **Ledger write vs. publish-record status write atomicity.** Ledger steps
   and the publish record's status/ext CAS live in different tables. Is a
   same-transaction guarantee required for any step pair, or is
   "ledger-first, record-second, re-run converges" acceptable everywhere?
   (Assumed acceptable; flag if not.)
3. **Eval orphan handling.** Is persisting the eval bot's identity in the
   ledger (making orphans discoverable/manually tear-downable) sufficient, or
   should eval teardown be automatically driven (e.g. a TTL task)?
4. **Approval re-drive trigger.** For an AGREED approval whose trigger
   crashed: is it acceptable to re-drive it from the approval callback's
   redelivery plus a check on the next user-visible operation, or does it
   need its own durable task? (The workflow platform's redelivery behavior
   determines this.)
