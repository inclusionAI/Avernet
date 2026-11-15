# Validation — Provider initial managers

> Historical implementation/validation record. The 2026-11-09 fixes and current
> transaction/migration contract are in [carolynli-followup.md](carolynli-followup.md).


The results below describe the original implementation. For the subsequent code
review fixes, MySQL migration 043 and full Singlebox run, see `review-followup.md`.

## Executed

- Red test: `bcs-bot::provider_initial_managers` initially failed with the
  existing exactly-one-owner rejection; passed after implementation.
- Review regression: the new Memory manager Human expired after 600 seconds
  without a heartbeat. The test failed first, then passed after matching
  `ensure_human_actor`'s non-expiring token-backed lifecycle.
- `SERVER_ENV=dev cargo test -p bcs-bot -p bcs-bot-store -p bcs-app-a2a-bot
  -p bcs-app-register -p bcs-service-api --tests --no-fail-fast`:
  **1,148 passed, 0 failed, 7 ignored**, exit 0 (112 suites).
  The inherited `SERVER_ENV=local` caused two existing control-plane fixtures
  with hardcoded `dev` rows to fail in the first broad run; both pass with the
  explicit test environment, without modifying those tests or production policy.
- Final focused rerun after removing unused ownership-builder audit arguments:
  `SERVER_ENV=dev cargo test -p bcs-bot-store --lib -p bcs-bot
  --test provider_initial_managers -p bcs-bot-store --test provider_initial_managers
  -p bcs-app-a2a-bot --test conformance_provider_a2a_registration`:
  **135 passed, 0 failed, 4 ignored**, exit 0.
- `SERVER_ENV=dev BCS_TEST_MYSQL_URL=<isolated disposable database>
  cargo test -p bcs-bot-store --test provider_initial_managers_mysql -- --ignored`:
  **1 passed**, exit 0. Ran against native MySQL 9.7.2 with the repository's
  full MySQL migration chain. Verified 100 managers, strict role reads,
  replay after revocation, native constraint failure rollback and recovery.
  This tests MySQL, not OceanBase or other MySQL versions.
- `git diff --check`: passed. All 36 added/modified Rust files are <=1,000 lines
  (maximum 992). The existing oversized Memory fixture's final registration
  scenario was moved unchanged into a child module to preserve this limit.

## Coverage and access cost

Tests cover Gateway/Plugin replay, ownership transfer preservation, manager
inability to request owner transfer, invalid/duplicate identity inputs, actor
liveness, Provider audit identity/snapshot, SQLite mid-transaction Human/edge/
audit failures and retry, A2A whole-registration rollback, maximum batch size,
and constant five-statement manager SQL composition for both dialects.

No schema changes or new dependencies. New manager work is bounded to 100 Humans
and 100 edges per initialization; SQL uses batched statements instead of
per-manager round trips. See `bcs-bot-store/CONTEXT.md` for governance-read costs.

## Gates not fully passed/run

- `scripts/ci/arch-check.sh` reported existing import and trait-naming violations
  plus other repository-wide conformance findings. Re-ran the unmodified import
  and trait scripts against a clean `git archive HEAD` snapshot: the same four
  import-rule failure categories and ten trait violations, with zero added or
  removed findings. After static failures, stopped the runner's expensive
  full-workspace test enumeration; no complete architecture-gate pass is claimed.
  Gates, allowlists and waivers were not changed.
- Full Singlebox E2E and unit/E2E coverage gates were not run: validation here was
  scoped to affected crates and isolated native MySQL, without starting the full
  product stack. No push or PR was attempted.
