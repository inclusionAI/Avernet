# Phase 1 implementation validation

## Current addendum: Bot-owned Provider storage (2026-09-21)

This follow-up was implemented and validated in an isolated checkout based on
`5d52048dd29027faf304e57c63114b1902f66095`. It supersedes the historical journal,
retry/replay, migration-version and file-splitting notes below. Root and parent
feature checkouts are unchanged. The user subsequently authorized commit and
push with `--no-verify` directly to PR #2358's head branch,
`vzvince/Avernet:codex/provider-token-registration`. This authorization does not
include a PR description edit or production migration.

Provider identity/mode/webhook metadata now resides in `bcs_bots`. Gateway writes
still maintain the legacy binding projection; upstream writes do not. A typed
configuration selects binding-based (default) or Bot-mode delivery reads. Runtime
registration journal reservation/replay is removed; duplicate scoped refs return
409 and distinct refs can reuse a valid token. Bot/binding SQL writes are atomic,
but later owner-edge failures or lost responses can require operator repair.
Historical journal tables and credentials remain unchanged, not dropped.

### Final verification

Run from `src/bcs`, with `CARGO_TARGET_DIR` pointing to a separate temporary build
directory. Both final test commands exited successfully after the last code fix:

```sh
cargo test --offline --locked --no-fail-fast \
  -p bcs-bot -p bcs-bot-store -p bcs-app-register -p bcs-domain \
  -p bcs-service-api -p bcs-api-http -p bcs-test-support -p bcs-admin --quiet

env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  -u http_proxy -u https_proxy -u all_proxy \
  cargo test --offline --locked -p bcs --features test-utils --lib \
  --test provider_registration_openapi --test provider_registration_config \
  --test provider_bot_webhook_integration --test openapi_v1_mount --quiet

cargo check --offline --locked -p bcs --lib --examples
cargo run --offline --locked -p bcs-admin --quiet -- \
  db migrate --dialect mysql --check-files
```

- Affected modules: **866 passed, zero failed, 10 ignored**.
- Bootstrap and selected HTTP/WS integration: **302 passed, zero failed,
  5 ignored**. Loopback server tests ran on the host.
- Combined: **1,168 passed, zero failed, 15 ignored**; focused reruns are not
  counted again. Registration HTTP coverage exercises both direction read sources.
- Compilation and all **30** MySQL migration file checks passed. New additive
  migrations are SQLite **031** / MySQL **030**; historical migrations are frozen.
- OpenAPI validated **72 operations**. Store boundaries, port purity,
  forbidden-symbol and interceptor checks passed, as did `git diff --check`.
- Regression tests were observed failing before fixes for legacy gateway deletion,
  Provider switching authorization, backfill evidence validation and application
  repository access. Shared contracts cover metadata, compatibility bindings and
  Provider-admin deletion. Review was author self-review, not independent review.

### Remaining verification and rollout limits

- Live MySQL migration/conformance was not run: Docker is installed but its
  daemon is stopped, and no local server was available. Static checks and SQLite
  tests are not proof of a live MySQL upgrade.
- The complete Singlebox coverage/E2E stack was not run.
- The full architecture gate is not green or complete. Dependency-script,
  import and trait-naming failures were reproduced in the unchanged parent
  checkout; the broad conformance discovery build was interrupted. A sandboxed
  telemetry socket test failed on permissions; the final host bootstrap run
  passed. No gates or baselines were weakened.
- All added/modified source line counts were checked. Existing files above
  1,000 lines remain intact per the user's explicit no-splitting instruction;
  size-driven extractions were reverted. No size allowlist was changed, and a
  source-size CI gate may therefore still reject these existing files.
- Backfill requires writer fencing, an issue-free audit and a staging rehearsal.
  The configuration switch is restart-based. Read-source rollback is supported;
  rolling back to arbitrary old writer binaries is not automatically safe.

See [the rollout and rollback guide](../../docs/provider-bot-storage-migration.md)
for migration commands, legacy affiliation gaps, lifecycle reconciliation and
retained-journal cleanup constraints. CLI packaging and bridge startup remain
outside this storage follow-up.

## Historical Phase 1 and rebase record

The following records describe earlier commits, not the current storage model
or current publication authorization.

Branch: `codex/provider-token-registration`.
Original base: freshly fetched `upstream/dev`, commit
`7d39e392b99d8bf8351b029c3128d97d1c230411`.
Rebase target: `upstream/dev` commit
`8aca0d4f6ce61bc8e8ab7334af0b3bdebbfc9e61`; no merge commit.

Only the OpenAPI registration flow is extended. Legacy routes, Provider-admin
registration, CLI packaging and bridge process startup are not changed.

## Original pre-rebase verification

The final post-review affected-module run passed 826 tests (6 ignored):

```sh
cd src/bcs
cargo test --offline -p bcs-domain -p bcs-service-api -p bcs-bot-store \
  -p bcs-bot -p bcs-app-register -p bcs-api-http -p bcs-test-support --quiet
```

Bootstrap unit tests and selected real-server integration tests passed another
282 tests (5 ignored). These tests ran on the host because they bind loopback
sockets; the new HTTP client explicitly disables environment proxies.

```sh
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  -u http_proxy -u https_proxy -u all_proxy \
  cargo test --offline -p bcs --features test-utils --lib \
  --test provider_registration_openapi --test provider_registration_config \
  --test openapi_v1_mount --test provider_bot_webhook_integration --quiet
```

Both final commands exited successfully after the credential-read/readiness fixes
and formatting. Total: 1,108 passed, 0 failed, 11 ignored. The final shared-harness
module relocation has an additional focused conformance rerun: 19 passed,
0 failed (`cargo test --offline -p bcs-bot-store --test conformance_bot_repo
-p bcs-test-support --quiet`).

Additional successful checks:

- OpenAPI validator: 72 operations validated.
- Store boundaries, port purity, forbidden-symbol and interceptor-chain checks.
- HTTP application-boundary tests and Service API contract tests.
- `git diff --check`.

Regression tests were observed failing before the relevant fixes for deleted
pending registration resurrection and self-service callback authorization.
Independent review confirmed the atomic create-only change, shared-bearer
protection, gateway credential readiness and fallible credential reads. Delayed
database INSERT/acknowledgement, rotated credentials,
tombstones, uniqueness and read/write failures have focused tests.

## Post-rebase verification (2026-09-21)

The two main commands above were rerun against the rebased working tree:
835 affected-module tests passed (6 ignored), and 300 bootstrap/selected HTTP
integration tests passed (5 ignored). `cargo test --offline -p bcs-admin --quiet`
passed 26 tests (5 ignored). Total: **1,161 passed, 0 failed, 16 ignored**.

The focused migration run (`cargo test --offline -p bcs --lib migrations::
--quiet`) additionally passed 33 tests, a subset of the bootstrap run. Two new
regression tests first failed with the upstream-only migration runner, then
passed after registration was appended as SQLite 030 / MySQL 029. They verify
unique consecutive versions, coexistence with Fixed Loop, upgrade from SQLite
029, preservation of every prior history row, and idempotent journal retention.

`cargo run --offline -p bcs-admin --quiet -- db migrate --dialect mysql
--check-files` passed for all 29 MySQL files. The real-MySQL full-chain test now
expects 29 migrations and checks the journal columns; it remains ignored locally
because the host Docker daemon is not running. Static checks are not execution
evidence for a live MySQL deployment.

OpenAPI validation passed for 72 operations. Store boundaries, port purity,
forbidden-symbol and interceptor-chain checks, new-test formatting, and
whitespace checks passed again. All upstream migration
SQL and the extracted SQLite baseline/schema modules are unchanged. Both renamed
registration SQL files have the same Git blob hashes as before the rebase.

The fork's remote `dev` was fast-forwarded to upstream commit
`8aca0d4f6ce61bc8e8ab7334af0b3bdebbfc9e61`. Both remote heads were checked again;
their tree is `ef9894ee61fc0ec19613ded276ad9ca3e55f597b`, with an empty tree diff.
Only the feature commit is replayed; no merge commit is introduced. The original
feature tip is retained locally as
`codex/provider-token-registration-pre-rebase-20260921`.

## Deployment and known limits

- MySQL has schema/query/driver-error-shape coverage, but no live MySQL run was
  available. Apply migration 029 before deploying; SQLite runs migration 030.
- The complete Singlebox coverage/E2E stack has not been run.
- The full architecture gate is not reported green: its dependency check fails
  with the host's system Bash (`d` followed by a full-width parenthesis is parsed
  as an invalid variable), and import checks report existing violations. Both
  were reproduced in the untouched dev checkout. Gates/baselines were not weakened.
- Durable partial-registration recovery requires the SQL-backed repository.
  Memory mode is process-local; cancellation after file publication but before
  memory publication fails closed on retry instead of blindly restoring a Bot.
- Self-service gateway callers must inherit the Provider default callback.
  Provider creator/owners may supply Bot overrides. Independent self-service
  callbacks require Bot-scoped downlink credentials, outside this phase.

## Unresolved source-size policy

All new source files are below 1,000 lines. Four existing files necessarily
touched for configuration and trait/composition wiring already exceed
that limit on dev:

- `crates/bootstrap/bcs/src/config.rs`
- `crates/bootstrap/bcs/src/server.rs`
- `crates/services/bcs-bot-store/src/lib.rs`
- `crates/services/bcs-bot-store/src/memory.rs`

New logic is in separate small modules; these files contain minimal additions.
This is an outstanding repository-policy issue, **not an approved waiver**. The
user has been asked whether to approve a narrowly scoped exception with follow-up
splitting, or include the larger restructuring now. No allowlist was changed.

The original dev checkout is unchanged; implementation work is isolated in its
worktree. Publication uses `git commit --no-verify` and `git push --no-verify`
at the user's explicit request. The validation results above were collected
during implementation; bypassing local hooks does not waive the documented
source-size issue or replace required CI checks.
