# Provider Initial Managers Implementation Plan

> Historical implementation/validation record. The 2026-11-09 fixes and current
> transaction/migration contract are in [carolynli-followup.md](carolynli-followup.md).


> Execute inline using executing-plans and test-driven-development; no separate agents.

**Goal:** Interpret Provider registration `owners` as one owner followed by initial managers.

**Architecture:** Preserve Provider authentication and existing manager command restrictions. Add a Provider-only authority initialization operation to Registry Core/Repo; commit initial manager Human actors, edges, owner/version and audit in the same transaction/staged memory publication. Existing initialized bots never synchronize managers on replay. Extend the existing A2A atomic registration payload with initial managers; Human-token registration supplies an empty list. The review follow-up adds MySQL migration 043 to align actor unique keys with the byte-exact identity contract; see `review-followup.md`.

**Tech Stack:** Rust, Tokio, SQL plugin transactions (SQLite/MySQL), shared MemoryBotRepo.

## Tasks

- [x] Add `crates/services/bcs-bot/tests/provider_initial_managers.rs`: register `[alice,bob,carol]` through real ProviderManagement and assert strict owner/manager roles; prove current code rejects it. Cover duplicate identities, malformed IDs, replay after revocation and Plugin replay.
- [x] Add bounded ordered identity normalization in Service API types; reject empty/invalid entries, deduplicate after trimming, exclude owner, cap raw inputs to 101. Add initial managers to `OwnershipInitialization` (all unrelated callers pass `Vec::new()`). Keep default implementations fail-closed.
- [x] Add Provider-only initialization to Core/Repo. SQL stages Human checks/creation, manager edges and audit alongside existing owner/version transaction. Memory stages Human rows/edges/audit and publishes only after all validation. Non-Provider registration cannot carry managers.
- [x] Wire ordinary Provider and Provider A2A registration. Check actual ownership version for both Gateway and Plugin replays, not binding presence alone. Preserve standalone public A2A single-owner policy.
- [x] Add real SQLite rollback/retry and A2A cases, update old single-owner rejection tests and registration docs. Document bounded DB work and unchanged registration/authority transaction boundary.
- [x] Run focused Cargo suites, relevant architecture gates, diff whitespace checks and source line counts. Run native MySQL in an isolated disposable database; report unavailable full E2E coverage explicitly. Do not commit or push automatically.

## Verification commands

```sh
cargo test -p bcs-bot --test provider_initial_managers
cargo test -p bcs-bot --test provider_core --test authority_provider
cargo test -p bcs-bot-store --test provider_initial_managers
cargo test -p bcs-app-a2a-bot --test conformance_provider_a2a_registration
git diff --check
```

## Contract

`owners: [alice,bob,carol]` yields `owner=alice`, `managers=[bob,carol]`; `created_by=alice`. Validation happens before creation. Only version-0 authority initialization applies the snapshot. Fully initialized registrations preserve current edges (including revocations and transferred owners); no historical backfill. Manager failures cannot commit owner/version. Provider is the audit principal. Existing ordinary Bot/binding writes may already have committed when authority initialization fails; the error propagates and a retry can initialize the still-version-0 Bot. A2A retains its whole-registration atomicity.
