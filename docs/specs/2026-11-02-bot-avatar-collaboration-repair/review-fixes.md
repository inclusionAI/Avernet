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

## Runtime identity preservation on profile writes — implemented

Round 2 (PR-3488 comment by @carolynli): an ordinary online WebSocket Bot
(no Provider affiliation) lost its AgentPass identity on an admin profile
update. The onboarding merge builds capabilities without credentials, the
core Provider guard restores them only for affiliated Bots, and both store
implementations then published the identity-less snapshot wholesale —
leaving the live entry with `(agent_code, agent_token) = (None, None)`,
which makes `group_flow/interceptors.rs` skip the whole outbound
interceptor chain. The persistent store additionally nulled the
`agent_code` column and the bot_info identity keys. Legacy
`save_to_storage` preserved both fields when the incoming capabilities
omitted them.

- Reproduced before implementation via the shared profile contract
  (`tests/support/bot_profile_contract.rs`, 身份保留 section): WS-online
  posture (`register` with the AgentPass code, `add_bot_info` runtime
  token), then an identity-less profile write — both Memory and real
  SQLite roots returned `(None, None)` from `get_agent_credentials` on the
  pre-fix stores.
- `bcs-bot-store` (`persistent_repo/profile.rs`,
  `persistent_repo/registry_storage.rs`): the profile UPDATE now uses a
  profile-only column projection — the `agent_code` column and the
  `$.agent_code`/`$.agent_token` bot_info keys never appear in the
  statement; cache publication is field-wise, preserving the live entry's
  identity. The registration/save path keeps the full projection.
- `bcs-bot-store` (`memory_profile.rs`): the stored record preserved the
  current identity (live entry wins, stored record fallback), and the
  in-memory publication is field-wise over profile fields only.
- `bcs-service-api` (`types::bot_profile`, `port::repo::bot`): contract
  documentation states that a profile write never writes the runtime
  registry identity and implementations preserve it. Doc-only; no API
  change beyond the already-bumped 2.0.0.
- Intentionally not done, per the review guidance and the sanitized-read
  contract: no identity backfill from `RegisteredBot` (it never carries
  the token), and no runtime token written to the shared database.
- Run `cargo test -p bcs-bot-store` (all profile/avatar/token suites pass;
  the two pre-existing control-plane failures stay as recorded),
  `cargo test -p bcs-bot`, `cargo test -p bcs-service-api`,
  `cargo test -p bcs-http --test avatar_write_contract`,
  `cargo test -p bcs-app-group --test v1_group_service`,
  `cargo test -p bcs-app-session --test v1_session_service`, and
  `cargo check --workspace --all-targets`.

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
