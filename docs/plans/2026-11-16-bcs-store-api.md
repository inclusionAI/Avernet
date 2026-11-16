# BCS Store API Implementation Plan

> Execute this approved contract-only scope in the current isolated worktree.

**Goal:** Define a vendor-neutral runtime store plugin API, owning documentation,
and reusable contract tests for KV, conditional writes, atomic JSON, sorted sets
and streams. No provider, business store or runtime wiring is included.

**Architecture:** Four capability traits share metadata/error/value types in
`bcs-store-api`. Service repositories own keys and business protocols; providers
own atomic storage operations. Advanced capabilities are explicitly advertised,
never emulated with unprotected client read/write sequences. Reusable harnesses
live in `bcs-test-support`; private test fixtures exercise the harness itself.

**Tech Stack:** Rust, async-trait, serde_json, thiserror; Tokio for tests.

## Tasks

1. Add the workspace member and type/object-safety tests, run the tests to observe
   the missing API, then define shared capability/error types and four traits.
   Files: `apps/bcs/Cargo.toml`, `apps/bcs/crates/plugin-api/bcs-store-api/`.
   Distinguish plain writes, exact-version CAS, strictly newer version writes,
   rejected conditions and unknown write outcomes. No domain Revision is added.
2. Add KV/conditional and atomic JSON harnesses. Exercise binary values, ordered
   multi-get, expiry, single-winner create/CAS, stale/equal versions, preserved
   sibling paths, atomic rollback and delete/recreate ABA protection.
3. Add sorted-set/stream harnesses. Exercise bounded ordering, collection-wide
   version conflicts and no-op invalidation; automatic/explicit stream IDs,
   multi-stream read, trimming evidence and group acknowledgement/reclamation.
4. Run those harnesses against private test-only fixtures and mutation cases
   that deliberately violate atomicity/version rules. This verifies the tests,
   not a production backend's durability or failover behavior.
5. Document capability support, lifecycle, uncertainty, limits, dependency
   boundary and provider validation. Update plugin/app navigation. Run targeted
   Cargo tests, rustdoc, dependency/boundary checks and changed-file hygiene.

## Validation

From `apps/bcs` (use the installed compiler explicitly if needed):
- `cargo +1.94.1 test -p bcs-store-api`
- `cargo +1.94.1 test -p bcs-test-support --test conformance_store`
- `cargo +1.94.1 clippy -p bcs-store-api --all-targets -- -D warnings`
- `cargo +1.94.1 doc -p bcs-store-api --no-deps`
- Relevant `scripts/ci` architecture checks and `git diff --check`.

## Review focus

No backend SDK types or business models cross the API; no unsafe fallback for
advanced operations; no false success after timeout or duplicate stream ID;
version metadata survives expiration/removal; bounded reads do not silently
skip data. Real provider durability, failover and cancellation remain required
provider conformance work, not claims established by test fixtures.

## Execution record

- Baseline: authoritative GitHub dev `ab4fbc135d325dc9fd3f84acd6e253e83396c6a4`.
- Ruling: use four operation traits plus one capability declaration trait;
  startup can reject unsupported requirements without probing writes.
- Ruling: ordinary operations are available independently; a provider advertising
  conditional/versioned operations must make every mutation participate in that
  key's version history. Native command availability alone is insufficient.

## Delivery and verification

- Completed definitions, crate documentation/context, app/plugin navigation and
  shared provider harnesses. Test-only fixtures remain private to the test target.
- Added enforcement for runtime-store symbols in the existing core/store boundary
  checker, an isolated negative shell test, and a no-BCS-dependency check for this
  new plugin API. No production provider, repository or OCB integration was added.
- Independent review found four issues, all fixed: provider-generated versions
  must not be assumed below a constant; fractional TTLs round up; JSON/ZSet CAS
  harnesses need concurrent contenders; zero ZSet versions are invalid input.
  Each regression was observed failing before its fix. Deliberately interleaved
  check/write implementations initially escaped the old harness and are now caught.
- Rust 1.94.1: 3 API type tests and 18 contract/model tests passed. API Clippy with
  warnings denied, rustdoc with warnings denied, source-size/new-link checks,
  `git diff --check`, R10 and its negative fixture passed.
- Full dependency gate remains red on three unchanged baseline dependencies:
  bcs-protocol -> bcs-domain; bcs-service-api -> bcs-config-api/bcs-storage-api.
  Reproduced the same failures in the prior clean worktree and verified affected
  manifests are unchanged from the new branch's dev baseline.
- R25.1/R25.2 static scans report existing harness/implementation-entry gaps.
  Compared the public-trait missing-harness set against dev: unchanged; all five
  new traits have matching harness functions. Full workspace discovery/tests were
  not run; the actual new conformance target was compiled and executed.
- Broad bcs-test-support Clippy is blocked by existing lint failures (including
  expect_used in auth_oauth_mock); no diagnostics reference the new store harness.
  This is separate from the passing bcs-store-api Clippy check.
- No claim of real TBase/Redis durability, failover, latency or provider conformance.
  Verification was completed locally on codex/bcs-store-api before publication.
  Commit/push with --no-verify and a dev PR were subsequently requested; no host
  gitlink update or deployment is part of this change.
