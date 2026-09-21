# Provider Bot Storage Consolidation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move Provider membership and connection mode into `bcs_bots`, remove journal-backed registration, retain gateway-only binding writes, and configure the delivery read source.

**Architecture:** Bot records become the membership authority. Gateway registrations, webhook updates, soft deletion and existing upstream-to-gateway switches maintain the legacy binding projection. Bootstrap injects a typed delivery resolver with a legacy default; membership authorization remains separate from direction detection.

**Tech Stack:** Rust, async Service API/repository contracts, Memory/SQLite/MySQL stores, additive SQL migrations, TOML configuration, Cargo contract tests.

## Approved scope

- Add Bot `provider_id`, `provider_bot_ref`, nullable migration-state `connection_mode`, nullable `webhook_url`, and preserve any binding timestamps required by existing public responses.
- New ordinary Bots explicitly use upstream with no Provider. Provider upstream Bots never write HTTP bindings. Gateway Bots persist both Bot metadata and compatibility bindings.
- Enforce one Provider per Bot/environment and unique Provider/ref across modes. Do not silently reuse deleted identities, replace tokens, change Provider, or change mode on registration replay.
- Reuse Bot soft deletion; mirror gateway deletion to legacy binding disabled state. WS disconnection is not deletion.
- Proposed setting: `downlink_detection_source = "binding" | "bot_connection_mode"`, default `binding`. Use the same direction policy for delivery, WS admission, active/online checks and downlink-only coordination/authentication. Never silently fall back on missing Bot-mode migration data.
- Preserve OpenAPI legacy token/register and old Provider-admin `plugin`/`gateway` defaults and credential behavior. Upstream requires no webhook or downlink credential. Gateway endpoints resolve Bot override before Provider default.
- Eliminate journal reservation/completion and credential replay. Scoped duplicate refs conflict; distinct refs can reuse a valid registration token. Preserve legacy admin replay behavior.
- Use existing database transactions for coupled persistent writes. Full Bot/Human/owner-edge exactly-once registration is not promised; all failures propagate.
- Keep committed numbered migrations frozen. Provide audited backfill/preflight, writer fencing, consistency validation and rollback documentation. Do not auto-delete inconsistent or populated legacy data.
- Work only in this isolated checkout. No subagents, parent checkout edits, PR description edits or production migrations. The user subsequently authorized commit and push with `--no-verify` directly to PR #2358's head branch, `vzvince/Avernet:codex/provider-token-registration`; do not modify the parent checkout or its local branch.
- Preserve existing file layout. Per the user's latest instruction, do not split existing files to meet the 1,000-line guideline; keep changes limited to the requested behavior.

## Task 1: Baseline and contracts

**Files:** `src/bcs/crates/contracts/bcs-domain/src/provider.rs`; focused new domain/type modules; `src/bcs/crates/service-api/bcs-service-api/src/port/repo/provider.rs`; matching contract tests.

1. Run unchanged registration suites before implementation.
2. Write failing tests for typed membership/mode and resolution source, legacy configuration default and invalid enum values.
3. Add minimal contracts separating membership, direction and availability. Keep credentials out of debug output.
4. Run focused contract tests; compile consumers.

Baseline command from `src/bcs`: `CARGO_TARGET_DIR=/private/tmp/avernet-provider-bots.nFknHp/target cargo test --offline --locked -p bcs-bot --test provider_registration -p bcs-bot-store --test provider_registration`.

## Task 2: Schema and persistence

**Files:** new MySQL migration after 029; SQLite migration after 030 under `src/bcs/crates/bootstrap/bcs/src/migrations/`; focused new membership module under `src/bcs/crates/services/bcs-bot-store/src/`; corresponding store/migration tests.

1. Write failing fresh-schema/upgrade and Memory/SQLite repository tests.
2. Add nullable migration-state columns and environment-scoped Provider/ref uniqueness without changing historical migrations.
3. Implement authoritative Bot-backed membership reads/writes, gateway compatibility projection, webhook synchronization and deletion synchronization.
4. Verify duplicate refs across modes, ownership conflicts, transaction rollback, restart and environment isolation. Preserve Bot credentials, UUID and binding timestamps.
5. Run real MySQL migration-chain validation when a configured local server is available; otherwise report it as unverified, not passing.

## Task 3: Registration without a journal

**Files:** `src/bcs/crates/services/bcs-bot/src/core/provider_registration.rs`; `src/bcs/crates/service-api/bcs-service-api/src/core/provider_registration.rs`; registration result types; `src/bcs/crates/application/v1/bcs-app-register/src/lib.rs`; registration tests.

1. Write failing tests for persisted upstream membership without binding, gateway dual writes, duplicate-ref conflict and reusable register tokens across different refs.
2. Replace reservation/completion and replay with Bot-backed creation and uniqueness.
3. Preserve signed Provider/owner authorization, fresh redemption checks, webhook override permissions and gateway credential readiness.
4. Run scoped and legacy registration suites, including write and owner-edge failures.

## Task 4: Mutation and membership consumers

**Files:** `src/bcs/crates/services/bcs-bot/src/core/provider_core.rs`; `src/bcs/crates/services/bcs-bot/src/application/provider.rs`; `src/bcs/crates/services/bcs-bot/src/application/bot.rs`; `src/bcs/crates/services/bcs-bot/src/core/bot_control_plane_core.rs`; Provider discovery/organization storage and tests.

1. Write regression tests for admin plugin registration, credential preservation, gateway replay, webhook PATCH, deletion and upstream-to-gateway switching.
2. Persist membership in both modes while maintaining gateway-only bindings across every mutation.
3. Move membership consumers to Bot-backed data with explicit compatibility reads for unmigrated gateway rows. Preserve public list scopes and owner/Provider permissions.
4. Run affected Bot and organization contracts. Do not split existing modules solely because of their line counts.

## Task 5: Configurable unified delivery resolution

**Files:** `src/bcs/crates/services/bcs-bot/src/core/bot_core.rs`; runtime application methods; `src/bcs/crates/bootstrap/bcs/src/config.rs`; composition roots; configuration templates and tests.

1. Write failing ordinary/upstream/gateway behavior tests under both sources.
2. Inject default binding and opt-in Bot-mode selectors; reject unknown configuration values.
3. Unify delivery, WS admission, online/active and downlink-only coordination/authentication classification without broadening Provider permissions.
4. Test strict missing-mode errors and gateway readiness. Test switching configuration back after webhook changes, deletion and mode switching.

## Task 6: Migration, cleanup and verification

**Files:** focused migration/preflight tools and tests; Provider integration docs; affected `CONTEXT.md`; this plan and progress ledger.

1. Write failing tests for mismatch detection, repeatable backfill and classification of existing journal data.
2. Implement audited preflight/backfill and document additive schema, legacy-read dual-write rollout, writer fencing, consistency gates, read cutover and rollback.
3. Remove obsolete journal runtime code and wiring after replacement contracts pass. Do not rewrite old migration files or auto-drop populated tables.
4. Run affected module, conformance and architecture checks; run `git diff --check`. Report any existing file-size gate conflict without refactoring files to satisfy it.
5. Self-review (subagents prohibited) and report actual checks, unavailable validations and remaining risks. Do not publish.

## Review focus

- Every gateway mutation maintains both representations; a read-source switch never changes writes.
- Membership does not automatically grant HTTP-event, delete or coordination privileges.
- Provider credentials cannot be redirected by unauthorized Bot webhook overrides.
- Unknown migration state is not treated as upstream; missing historical affiliation is never guessed.
- Old writers are fenced before new cross-mode registration is enabled.
- Disabled-binding/active-Bot inconsistencies are reported, not blindly deleted.

## Progress

- Created isolated checkout from `5d52048dd29027faf304e57c63114b1902f66095`; parent checkout untouched. Native task attachment failed; continue using the existing checkout explicitly.
- Baseline: 28 tests passed, 1 subprocess helper ignored by design, zero failures. Separate Cargo target: `/private/tmp/avernet-provider-bots.nFknHp/target`.
- Typed domain contract: 3 tests passed. Additive SQLite schema/uniqueness upgrade: 2 tests passed.
- Initial Memory/SQLite Bot membership, gateway dual-write and rollback contract: 3 tests passed. Historical migrations remain unchanged.
- Initial implementation milestone: scoped registration and runtime composition use Bot-backed Provider metadata; validation was still in progress at this point.
- Scope correction: reverted size-driven splits of six existing source/test containers and removed the 35 extracted files, preserving the intended feature edits. `cargo check --offline --locked -p bcs --lib --examples`, 27 focused store tests, the downlink configuration test, and `git diff --check` all passed after restoration. Main and parent feature checkouts remain clean. This verifies the scope correction, not full feature acceptance.
- Final implementation: all six tasks are implemented. Bot-backed membership, gateway-only dual writes, both read selectors, legacy mutation compatibility, journal-free scoped registration, additive schemas and audited backfill are present. Historical registration tables/data are retained; no production migration was performed.
- Final regressions fixed and verified: missing SQL test-fixture columns, MySQL migration-count expectations, unmigrated gateway deletion, upstream Provider switching authorization, incomplete/missing-Provider backfill evidence, and the application-to-repository boundary. Shared conformance covers the new metadata/projection ports and Provider deletion core contract. Review was author self-review only; no independent reviewer or subagent was used.
- Final verification (2026-09-21): affected module suites passed 866 tests (10 ignored); host bootstrap/selected real HTTP/WS suites passed 302 (5 ignored). Total: 1,168 passed, zero failed, 15 ignored. Library/example compilation, 30-file MySQL migration static validation, OpenAPI validation (72 operations), store boundaries, port purity, forbidden-symbol/interceptor checks, and whitespace checks passed.
- Remaining verification limits: live MySQL is unavailable (Docker daemon stopped); full Singlebox was not run; the full architecture gate is not green/completed. Dependency/import/trait-naming failures were reproduced in the unchanged parent checkout, and the broad conformance discovery run was interrupted. No gate or allowlist was weakened. Existing oversized files were counted and left intact as requested.
- Delivery state at the implementation-validation checkpoint: changes were uncommitted in the isolated checkout, with root and parent feature checkouts clean. Subsequent publication is authorized directly to PR #2358's head branch using `commit --no-verify` and `push --no-verify`; no PR description edit or production data change is included. See the current addendum in `src/bcs/specs/2026-09-20-provider-token-registration/validation.md` and `src/bcs/docs/provider-bot-storage-migration.md` for evidence and rollout constraints.
