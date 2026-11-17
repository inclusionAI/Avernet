# BCS Store API Implementation Plan

> Execute this approved contract-only scope in the current isolated worktree.

**Goal:** Define a vendor-neutral runtime store plugin API, owning documentation,
and reusable contract tests for KV, conditional writes, atomic JSON, sorted sets
and streams. No provider, business store or runtime wiring is included.

**Architecture:** Five operation traits share metadata/error/value types in
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

## 2026-11-17 capability split

User-approved adjustment: make persistent live-incarnation CAS and basic ordered
Stream operations usable independently. Add explicit versioned creation and remove
TTL from basic CAS. MonotonicKv retains the original stronger KV history/logical
version/deletion guarantees; AtomicJson and VersionedZSet remain strong.
StreamRecovery owns atomic head/deletion evidence, represented as an optional
object so unavailable evidence cannot be mistaken for no deletion. Groups depend
only on Stream. ACK of a missing group is an idempotent zero count.

The basic and strong harnesses run separately. Basic-only fixtures demonstrate
that neither caller-assigned versions nor recovery metadata are prerequisites.
No provider implementation, private configuration or runtime wiring is added here.

Follow-up validation: 3 type tests and 22 conformance tests pass, with an
interleaved-reader fairness check and a negative fixture that reproduces the
provider-global counter starvation bug. API Clippy, Rustdoc, R10 and negative
boundary checks pass. The history-resetting fixture remains a non-blocking test
enhancement; strong history assertions have not been weakened.


## Independent KV interfaces (2026-11-17)

User-approved refinement: ordinary KvStorePlugin and VersionedKvStorePlugin are
independent injectable traits. Versioned creation/CAS retain explicit Expiry;
direct deletion and TTL queries are baseline operations. VersionedKvTtl covers
all expiry modes and atomic conditional renewal. VersionedKvConditionalDelete
covers atomic conditional deletion and returns bool. Neither extension requires
MonotonicKv; that capability remains the cross-incarnation history/logical-version
guarantee. No-TTL providers reject Preserve/After before I/O instead of silently
making values persistent. The earlier persistent-only basic CAS signature is
superseded, not a permanent limitation of the public interface.

Affected consumers: the test-only model/shared harnesses and separately maintained
providers. No business service wiring is added. Both operation traits may share
connections/private wire helpers; a versioned-only handle has no ordinary set.
A compile-fail example enforces that boundary. Conformance includes basic-only,
versioned-without-ordinary, independent TTL/delete capabilities, stale deletion,
TTL failure rollback, and retained-history versus history-resetting fixtures.
Migration and capability checks are documented in the owning crate README.

Validation: 31 conformance tests, 3 type tests and the compile-fail boundary
example passed on Rust 1.94.1. The previously deferred history-resetting fixture
is now included: basic CAS accepts it and MonotonicKv rejects it. API all-target
Clippy and the store boundary gate passed. Provider integration is validated in
its owning repository; these fixtures do not certify backend failure behavior.
