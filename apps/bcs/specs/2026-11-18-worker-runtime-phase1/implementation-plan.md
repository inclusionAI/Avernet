---
status: in-progress
date: 2026-11-19
owner: BCS distributed Worker
---

# Worker runtime implementation plan

Implements the approved development scope in [the SPEC](spec.md). The proposed
SPEC remains the review baseline; this execution record does not claim deployment
or change the boundary of phase 1. Public implementation starts from `8811efbc97`,
rebased onto GitHub dev `3a925fd474`. Existing coordination/store/API tests pass.

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
- Remaining Task 4 Dispatch provider, Tasks 5–8 and full host composition: pending.
