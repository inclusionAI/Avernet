# PR 3488 review fixes

**Goal:** Preserve Provider identity, serialize local profile writes, and honor
explicit avatars on A2A Provider registration.

**Architecture:** Keep identity policy in BotCore, file coordination in
MemoryBotRepo, and A2A registration orchestration in the application layer.
Reuse existing core/repository contracts; no migration or wire change is needed.

**Validation approach:** Reproduce each reported failure before implementation,
retain consumer-level regression tests, then run affected crate and architecture
checks. The existing feature worktree is the implementation workspace.

## Identity preservation — implemented

- Add admin avatar-only and explicit reparent regression cases to
  `bcs-bot/tests/provider_registration/subbots.rs` using the real Provider fixture.
- In `bcs-bot/src/core/bot_profile.rs`, apply `protect_provider_identity` to the
  proposed capabilities before invoking the existing-only repository write.
- On identity denial, a strict repository read distinguishes a deleted target
  (`Missing`) from a live denied target. Read failures propagate. This adds one
  bounded lookup only on the denial path and never adds a write or scan.
- Run `cargo test -p bcs-bot --test provider_registration`.

## Local record writes — implemented

- Add `bcs-bot-store/tests/memory_profile_concurrency.rs` covering concurrent
  profile/Keep and avatar/Set, ordinary capabilities/token/attribute rewrites,
  readers during replacement, and preservation of the old file on write failure.
- Extend the existing control-plane write mutex to coordinate all local record
  read-modify-write operations, from their initial snapshot to memory publication.
  Keep it outside the existing authority/bot/token locks. This is a process-local
  memory-store guard, not cross-process coordination or a new avatar cache.
- Replace direct truncating writes with a shared temporary-file/atomic-rename
  helper. Prepare the temporary file on a blocking worker; publish it while the
  calling future still holds the write guard. Failed or cancelled preparation
  must not modify the current record.
- Run `cargo test -p bcs-bot-store --test memory_profile_concurrency` and the
  existing profile, token, control-plane and registration suites.

## A2A registration — implemented

- Add URL/inline avatar input and failure cases to the A2A registration contract
  tests using the real registration chain and SQLite migration 040.
- Inject the registry core through bootstrap, then apply Set/Clear after the
  ownership/Card registration composite succeeds; Keep adds no write.
- Propagate Missing and storage errors before returning a registration result.
  As with ordinary Provider registration, an avatar failure leaves the already
  committed Bot/Card intact and adds no rollback or automatic retry.
- Run `cargo test -p bcs-app-a2a-bot`.

## Integration — verified

- Update affected context and validation documents with behavior and evidence.
- Run affected module tests, workspace target checks and architecture checks.
- Check all changed source files against the 1,000-line limit and inspect the
  final diff for unintended changes.

Final evidence is recorded in [bcs-validation.md §9](bcs-validation.md#9-pr-3488-复审修复2026-11-03):
725 application/consumer tests and 365 bootstrap tests passed; workspace target
checks passed. All 15 added regression tests passed. The storage suite retains
the two previously recorded failures, and legacy import/dependency architecture
rules still fail on unchanged code. No gates were relaxed. Full Singlebox/E2E
and real MySQL were not rerun for this follow-up.
