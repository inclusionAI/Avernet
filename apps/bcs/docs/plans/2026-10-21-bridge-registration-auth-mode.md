# Token-selected bridge registration implementation plan

**Goal:** Let bridge select Provider authentication from signed registration metadata, generate subbot references automatically, and remove pending registration state.

**Architecture:** Keep v2 registration tokens and add optional `auth_mode`: new issuance always supplies the current Provider mode; redemption accepts historical tokens without the field and checks present values before creating a Bot. Bridge uses Token mode metadata by default, accepts an explicit resolver override, and falls back to the distribution legacy resolver when metadata is absent. Each new subbot registration generates one random suffix in memory; unknown outcomes are reported without automatic retry or pending files.

**Tech stack:** Rust, serde, clap, Tokio, existing HTTP and SQLite contract tests.

## Approved compatibility decisions

- Old clients ignore the new signed field; the token version, signature format and existing fields remain unchanged.
- Historical v2 Tokens without mode metadata remain usable. Explicit `--provider-auth` selects a resolver; otherwise Token mode selects it, then the distribution legacy default. Unknown or unsupported Token modes still fail before configuration changes or network access.
- Retain optional `--provider-auth <resolver-id>` without client-side mode equality checks. Remove `--bot-slug`. Preserve saved runtime identity policies and existing Bot references.
- Keep local credential acquisition separate from Provider mode. Explicit internal `agentpass-auto` retains its legacy environment/SDK discovery, including static registration when no SDK is configured; a configured but broken SDK fails without downgrade. Token-selected AgentPass never falls back to static identity.
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

1. Test Token mode selection, explicit resolver precedence without mode comparison, old Tokens with explicit/default resolvers, profile reuse, removed slug input, unique refs and failures preserving config without a pending file.
2. Parse the token before selecting a resolver. Explicit CLI resolver wins; otherwise map Token wire mode to a registered resolver, with a distribution legacy default for old Tokens. Token mode governs subbot behavior independently of the resolver.
3. Generate valid random suffixes in memory; preserve response binding checks and atomic config persistence.
4. Run the bridge app and CLI suites, including profile reuse/replacement and existing runtime compatibility.

## Task 3: OCB integration

Update internal composition, Poolab/local resolvers, registration scripts and tests in the existing PR #5262 worktree. Select Poolab vs desktop credentials automatically by default and restore optional resolver selection plus `agentpass-auto` compatibility. Test configured AgentPass failures never fall back to static identity; test Poolab multiple Bots and retry-as-new behavior. Run the internal bridge suite and architecture check against the updated public dependency.

## Task 4: Documentation and delivery

Update canonical Token/API contracts, bridge usage and AgentPass subbot design docs. Review changed source sizes and full diffs. Run appropriate affected tests and repository push gates. Push the public implementation to GitHub first and open a follow-up PR to dev (the original client PR is merged); then update OCB's gitlink and PR #5262. Preserve the unrelated APISpace artifact.

## Implementation and verification

Implemented on 2026-10-21. ProviderAdmin retains plain registration through the
static resolver; a regression test reproduced the missing mapping before the fix.
The Service API advances to 0.4.0 for changed Rust authorization/command contracts.
The internal frontend omits resolver overrides for automatic Token selection. Existing commands may continue passing a resolver ID, including internal `agentpass-auto`; no client-side Provider/resolver equality check is added.

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


## Resolver compatibility verification (2026-10-21)

- Optional `--provider-auth` selects the local resolver without comparing its ID
  to Provider mode. Without it, Token mode selects the resolver; historical v2
  Tokens fall back to the distribution default.
- Token mode governs subbot suffixes, append behavior and Actor environment
  independently of resolver selection. The persisted registration policy records
  the selected resolver's actual runtime policy.
- Restored internal `agentpass-auto` covers pre/prepub static registration,
  production SDK detection, absent SDK fallback and configured SDK failure.
- Fresh verification: public bridge app/CLI 89 tests, internal bridge 83 tests,
  onboarding script 18 tests. Tests include explicit resolver precedence,
  historical Tokens, profile reuse and subbot Actor propagation.
- Internal architecture boundary checks pass; the full offline Cargo metadata
  phase is blocked by missing `windows v0.52.0`. An attempted locked fetch timed
  out. No dependency lockfiles or validation gates were changed. Live enterprise
  acceptance and artifact publication were not performed.
