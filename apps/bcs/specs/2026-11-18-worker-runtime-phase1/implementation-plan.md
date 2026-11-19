---
status: in-progress
date: 2026-11-19
owner: BCS distributed Worker
---

# Worker runtime implementation plan

Implements the approved development scope in [the SPEC](spec.md). The proposed
SPEC remains the review baseline; this execution record does not claim deployment
or change the boundary of phase 1. Public implementation starts from `8811efbc97`,
rebased onto GitHub dev `3a925fd474` before development and refreshed onto
`687c231d3c` before publication. The final refresh only adds unrelated upstream
changes; no feature conflict resolution was needed.

Public behavior, SQL persistence semantics, migrations, recovery and resource
limits belong in Avernet. Host repositories supply storage plugins, credentials,
configuration and thin assembly. Host database acceptance may be deferred; public
archive behavior and crash recovery are still required. Host transport validation
uses the requested Proxy transport. No production default test Runner is added.

## Execution and verification

Each substantive increment starts with a failing behavioral test, implements the
contract, then runs focused regressions. Review contract compliance before code
quality. Keep existing gates enabled. Do not use scripted port stubs as evidence
that a real provider implements the protocol. Record unresolved host verification
separately from public implementation status.

1. **Persistent document subset — SPEC §3.1.** Extend existing `DocumentCas` /
   `DocumentStorePlugin`, rather than introduce an equivalent capability. Add
   same-version RFC 6901 projection and ordered atomic patch, with shared pointer
   validation. Creation starts at 1; null/removal, failed edits, competing EQ,
   missing keys, exhaustion and uncertain writes remain explicit. Update the
   model and reusable provider conformance. Do not change full `AtomicJson`.
2. **Output and lifecycle model — §3.2–3.3, §6.** Add stable artifact identity,
   Upsert/AppendText and one pure projection shared by events, drafts and restore.
   Freeze quotas on task creation; model task queue, finalization, publication,
   deadlines, dispatch obligations and control identities. Validate entire
   decisions before any effects. Test exact encoding and quota accounting.
3. **Reliable Inbox and scheduling — §4.1–4.2.** Add persistent repair journal,
   canonical record, same-document gate/generation/inflight admission decision,
   owner/activation/next-check and bounded EQ retries. Implement independent
   repair/discovery, real Consumer/ShardFeed, reader supervision and index repair.
   Test canonical races, uncertain writes at every boundary, gate close/reopen,
   critical-input reserved capacity, fairness and bounded reads.
4. **Public durable providers — §5–6.** Implement immutable Result/Blob via the
   public DbPlugin and new migrations. Implement TaskFeed append evidence,
   snapshot publication, logical retention and Gap-safe restore/follow. Implement
   DispatchControl durable discovery, idempotent cancel/input, queue and bounded
   supervision. Validate real provider code on memory/SQLite adapters; exercise
   every ambiguous mutation and late replay, including content conflicts.
5. **Worker processor — §4.3, §5–6.** Integrate the existing coordination runtime:
   replay outbox, bounded canonical input, resolved references, frozen time/input,
   pure reduction, budget validation, durable externalization, checkpoint CAS and
   exact uncertain-result resolution, then effects and scheduling. Implement two
   commits for result archival and terminal publication before next-task init.
   Use test-only deterministic Runners and real public providers for recovery.
6. **Control and isolation — §7.** Implement stable controls/deadlines, supervision,
   independent quota termination, Normal/Quarantined/Blocked, durable risk attempt
   outcomes, owner+head control CAS, gate closure/cut/scan/terminal obligations,
   and authorized conditional recovery. Preserve terminal facts and distinguish
   transient failures from unsafe state. Test paused producers and delayed effects
   across close/reopen, takeover and faults; consumed-only work is not recovery.
7. **Budgets and acceptance — §8–9.** Enforce encoded bytes, bounded scans/CAS,
   fair work shares, reader/cache lifecycle, metrics and admission backpressure.
   Run the SPEC fault matrix against actual providers and host Proxy composition,
   recording source/config identities without secrets. Host capability guarantees
   (including StrictLease) require separate evidence; do not claim support from
   the model alone or silently weaken startup checks.
8. **Delivery.** Update owning contracts/navigation and verification evidence.
   Independently review the full diff. Commit public and enterprise changes
   separately, follow cross-repository mirror/gate requirements, and reply under
   the original PR review comments with actual changes and SPEC sections. Keep
   discussions open. No merge or deployment is implied by development approval.

## Progress

- Baseline: focused `bcs-dist-worker`, `bcs-dist-store`, `bcs-store-api` tests pass
  with Rust 1.94.1, locked/offline. An existing unrelated `bcs-bot-store` unused
  import warning remains; no broad cleanup is part of this work.
- Task 1: implemented and independently reviewed for SPEC compliance and code
  quality. Nine new subset regressions, eleven coordination primitive tests and
  the existing store/worker/API suites pass. API strict Clippy passes. Existing
  test-support-wide lint debt prevents a clean strict Clippy result there; the
  changed production source has no reported diagnostics. Host Proxy subset
  conformance passes; default projection/Patch still transfer the whole document.
- Task 2: output/lifecycle contracts and pure projection implemented; independent
  SPEC and quality reviews pass. Twelve projection tests, eight model tests, one
  model doc test, four API tests and 34 distributed harness tests pass, alongside
  the store/worker regressions. Model/projection strict Clippy passes with
  dependencies excluded; the full dependency graph has existing domain lint debt.
  Worker integration, reference resolution and complete decision validation remain
  tasks 4–7; these foundation checks do not claim runtime completion.
- Task 3: reliable admission, retained canonical decisions, scheduling and
  independent repair/discovery implemented. Final SPEC and quality reviews pass;
  all 136 store tests and strict library Clippy pass. Slow-IO regressions verify
  durable phase progress, continuous reader prefixes and fair scan resumption.
  Inbox capacity is reserved before admitting new intents. Worker failure handling,
  terminal closure/resume proofs and shared scan budgets remain Tasks 5–7.
- Task 4 archive slice: public DbPlugin Result/Blob provider, durable exact-effect
  evidence and additive SQLite/MySQL migrations implemented. Independent SPEC and
  quality reviews pass. Ten production-provider tests on file-backed SQLite and
  all 54 migration tests pass. Review caught and fixed a concurrent completion /
  checkpoint-advance race; historical reads and retries now use stable format
  bounds, with task-specific limits remaining frozen in TaskSnapshot. MySQL DDL
  has static checks only; real host database acceptance is explicitly deferred.
- Task 4 TaskFeed slice: committed append evidence, atomic snapshot publication,
  logical retention and bounded restore/follow/list implemented. Independent SPEC
  and quality reviews pass; 21 real provider tests pass with SQLite archive.
  Fault tests cover both lost replies and definite later failures after partial
  persistence, which remain OutcomeUnknown. Ref quotas count parsed public JSON
  encoding, including legal numeric expansion. Entire read calls and each CAS
  episode have enforced deadlines. This is not complete Worker acceptance.
- Task 4 Dispatch slice: four durable providers implemented; independent SPEC and
  quality reviews pass. All 160 store tests (23 Dispatch tests) and strict target
  Clippy pass. Slow IO, permanently delayed items, lost cursor replies, retained
  control receipts and disposable notification trim have regression coverage.
- Task 5: Worker processor is wired to the real public providers. Independent
  SPEC and quality reviews pass after fixing the recovery and boundary findings.
  Fourteen helper tests and ten provider execution tests cover two-commit archival,
  quota termination, large Blob recovery, canceled processing futures, takeover,
  same-owner reacquisition and reader failures with mixed healthy delivery.
  Target strict Clippy passes. The shared pure encoding helper, frozen metadata
  reader and admission profile validation are included. Full runtime acceptance
  still requires the control, capacity and host composition tasks below.
- Task 6: public control/closure providers, Worker integration, application
  authorization and independent Dispatch Supervisor are implemented. SPEC and
  quality review pass; sixteen Worker control regressions and fifteen Supervisor
  cases cover exact recovery identities, immutable terminal facts, isolated
  sessions and uncertain remote obligations.
- Task 7: shared scan quotas/IO, reader/cache lifecycle, fair local release,
  encoded metadata limits, independent input quota, temporary Thinking,
  incremental Blob/Dispatch reads and runtime observations are implemented.
  Actual public-type serialization tests cover the conservative worst checkpoint,
  ordinary outbox and finalization reserve. A final resource review found and
  fixed cancelled reader delivery, input/output quota coupling and unrepresentable
  deadlines; targeted regressions pass. Final independent quality review also
  found and fixed failed durable outboxes monopolizing local capacity: confirmed
  replay can park safely, including cancellation and concurrent isolation, while
  unknown commits remain resident. SPEC and quality reviews pass. The final
  public runtime/provider suite passes 389 tests, plus 34 distributed harness
  self-tests.

## Integration and delivery evidence

The host composition uses real Proxy transport and the public SQLite archive.
Two Worker takeover with an actually applied checkpoint reply dropped passes:
the successor replays effects without rerunning the Runner, and an independent
SQLite connection reads the same immutable archive. Isolation closes and archives
two accepted tasks, rejects unauthorized recovery, confirms exact recovery retry,
and runs a new task in the reopened generation. Dispatch supervision delivers
late definitive remote evidence without changing the completed Task archive.

Final slow-IO review added retained Dispatch scans across 100ms polls, cancellation
safe staged delivery, single-IO pool progress, and an independent Thinking input
bound. Disposable notification failure after authoritative discovery no longer
poisons scan health. Focused regressions and real Proxy composition pass. A small
explicit capacity fixture verifies normal-budget refusal, reserved critical input,
provider restart and complete historical discovery; no production capacity profile
is enabled or certified. Restricted source/config/measurements stay in the host.

The public full suite and final independent quality review pass. Target strict
lint and delivery checks are recorded separately. The repository-wide architecture inventory reports
existing boundary/naming/conformance debt; required push gates remain enabled.
New public contracts have reusable assertions invoked by concrete provider tests;
static inventory was compared with GitHub dev to distinguish existing missing
entries from this change. Reconstructed readers use the same conformance harness.
Production failover durability and real host database verification remain distinct;
the latter was explicitly deferred. GitHub publication, internal mirror availability,
OCB gitlink integration and deployment are separate delivery states.
