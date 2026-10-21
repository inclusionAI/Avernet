# Token-selected bridge registration implementation plan

**Goal:** Let bridge select Provider authentication from signed registration metadata, generate subbot references automatically, and remove pending registration state.

**Architecture:** Keep v2 registration tokens and add optional `auth_mode`: new issuance always supplies the current Provider mode; redemption accepts historical tokens without the field and checks present values before creating a Bot. Bridge requires mode metadata and delegates credential acquisition to its distribution's identity resolver. Each new subbot registration generates one random suffix in memory; unknown outcomes are reported without automatic retry or pending files.

**Tech stack:** Rust, serde, clap, Tokio, existing HTTP and SQLite contract tests.

## Approved compatibility decisions

- Old clients ignore the new signed field; the token version, signature format and existing fields remain unchanged.
- New clients ask for a fresh token when mode metadata is absent. Unknown or unsupported modes fail before configuration changes or network access.
- Remove the CLI `--provider-auth` and `--bot-slug` options. Preserve saved runtime identity policies and existing Bot references.
- Keep existing local desktop credential acquisition separate from Provider auth mode; never downgrade an AgentPass Provider to static registration when its credentials are unavailable.
- Remove pending read/write/cleanup. Old pending files do not block registrations. A lost response can leave an extra remote Bot; no automatic credential recovery is claimed.
- No new endpoint, request slug field, database migration, or Provider migration.

## Task 1: Signed registration metadata

Files: `apps/bcs/crates/contracts/bcs-domain/src/provider_registration_token.rs`, `crates/service-api/bcs-service-api/src/core/provider_registration.rs` and related application contracts (paths relative to `apps/bcs`). Update issuance/redemption in `crates/application/v1/bcs-app-register`, Provider core and its conformance suite.

1. Add tests for issued mode metadata, legacy token acceptance, unknown/tampered fields, and mode mismatch producing no Bot writes.
2. Observe the focused test failures; add optional signed metadata and return the mode alongside authorization.
3. Check the signed mode against the Provider configuration using the existing Provider read; retain legacy missing-field behavior.
4. Run `cargo test --locked -p bcs-domain provider_registration_token`, `cargo test --locked -p bcs-app-register`, and `cargo test --locked -p bcs-bot --test provider_registration`.

## Task 2: Shared bridge behavior

Files: `apps/bcs/crates/tools/bcs-bridge/app/src/{app,cli,identity}.rs`, `registration/{mod,token,client,subbots_tests}.rs`, delete `registration/pending.rs`, and update CLI integration fixtures.

1. Test new Token mode selection and removed flags, missing mode rejection, unique refs across invocations, and failure preserving existing config without a pending file.
2. Parse the token before selecting a resolver. Map wire modes to registered resolvers, with distribution overrides for credential acquisition.
3. Generate valid random suffixes in memory; preserve response binding checks and atomic config persistence.
4. Run the bridge app and CLI suites, including profile reuse/replacement and existing runtime compatibility.

## Task 3: OCB integration

Update internal composition, Poolab/local resolvers, registration scripts and tests in the existing PR #5262 worktree. Select Poolab vs desktop credential acquisition without a user authentication-mode flag. Test AgentPass failures never fall back to static identity; test Poolab multiple Bots and retry-as-new behavior. Run the internal bridge suite and architecture check against the updated public dependency.

## Task 4: Documentation and delivery

Update canonical Token/API contracts, bridge usage and AgentPass subbot design docs. Review changed source sizes and full diffs. Run appropriate affected tests and repository push gates. Push the public implementation to GitHub first and open a follow-up PR to dev (the original client PR is merged); then update OCB's gitlink and PR #5262. Preserve the unrelated APISpace artifact.

## Implementation and verification

Implemented on 2026-10-21. ProviderAdmin retains plain registration through the
static resolver; a regression test reproduced the missing mapping before the fix.
The Service API advances to 0.4.0 for changed Rust authorization/command contracts.
The internal frontend no longer emits removed flags or promises SDK-based fallback.

Focused verification covers the domain codec (24 tests), registration application
(22), Provider registration core (29), OpenAPI routes (9), bridge app (32 including
integration), public CLI (55), internal bridge (76), and assembled frontend (16).
The ten session-recovery and seven start tests pass with one test thread; a broad
concurrent run hit their existing six-second timeouts. Public and internal binaries
share the same name, so run their suites sequentially when reusing a target directory.
OpenAPI validation covers 73 operations. The repository-wide architecture checker
is not green: it reports existing import/conformance issues and a sandbox-denied
telemetry socket test; the broad workspace build was stopped. No gates were weakened.
Live Poolab/AntFamily traffic and deployed end-to-end verification remain separate.
