# BCS unit CI performance implementation plan

## Goal and evidence

Preserve the complete workspace test inventory, single-pass execution, the existing
unit-only Cobertura/JUnit reports and thresholds (100% pass, >84% overall lines,
>=80% changed lines), and all real-MySQL contracts and isolated databases.

PR #3248, run 40800760874 / job 123208053363, took 28m34s: tools 4m01s,
coverage 11m24s (compile 7m07s, tests 3m18s), MySQL contracts 7m46s
(compile about 5m48s), cache save 4m06s (9.4 GB), other work 1m17s.
No CI duration reduction is claimed before an actual Linux Actions comparison.

## Ordered changes

1. **Prebuilt tools.** Test installer success, version reuse/replacement, platform
   selection, download fallback and checksum rejection with local release fixtures.
   Pin the versions observed in #3248 (nextest 0.9.150, llvm-cov 0.9.1), verify
   release archive SHA-256 before extraction, and call the installer from CI.
2. **Build metadata.** Reproduce redundant rebuilds using the actual two build
   scripts in dependency-free fixture crates. Share Git metadata tracking that
   resolves real HEAD/refs, supports linked worktrees and packed refs, watches only
   existing paths and refreshes the embedded commit after a new commit.
3. **Cache and change detection.** Test synthetic PR merges, multi-commit pushes,
   initial pushes, dispatch, unrelated changes and CI-only edits using temporary
   Git repositories. Fail closed on invalid diffs. Keep PR first-parent semantics;
   push diffs use the event's before SHA; dispatch always runs. This enables dev
   builds to populate a reusable trusted cache. Replace full-target caching with
   Rust dependency caching for the actual instrumented target, segregated by
   compiler/build configuration; do not persist raw profiles or test binaries.
4. **MySQL build reuse.** Test an end-to-end tiny workspace that records which
   test binary nextest executed. Run selected ignored MySQL contracts directly
   from nextest's build metadata rather than invoking a second Cargo build. Keep
   unit reports finalized before MySQL execution, preserve each isolated database
   and existing aggregate/scenario assertions, reject missing/ambiguous binaries,
   zero tests, ignored tests and nonzero exits. No tests are removed or retried.

## Validation

- Run existing workflow regression tests before editing; run each new regression
  first against the old implementation, then against its fix.
- Run all affected Python/script tests, shell syntax checks and workflow parsing.
- Validate both real build scripts in ordinary and linked Git fixtures, including
  unchanged builds, commits and packed refs.
- Smoke-test real prebuilt downloads into a temporary Cargo home.
- Compile/run the BCS workspace where local dependencies permit. Real MySQL
  contracts require a MySQL server; explicitly report unavailable validation.
- Review the final diff, unchanged thresholds/contracts and all modified source
  line counts. Do not run global formatters, change business behavior, weaken
  gates, or commit/push without the user's follow-up request.

## Compatibility and rollback

Changes affect CI orchestration and build metadata only. Existing local/pre-push
ci_test.sh usage remains valid. The unit artifacts retain their current names and
scope. The contract runner is an additional opt-in export consumed by GitHub CI.
Reverting this change restores the old installer/cache/Cargo contract steps; no
schema or runtime migration is involved.

## Validation results

Implemented on `fix/bcs-unit-ci-performance`, based on dev `b57b8e2d7`.

- Focused CI tooling/workflow regressions: **35 passed**, including actual Cargo
  rebuild fixtures, both old/new coverage modes and execution of an existing
  instrumented binary after deliberately breaking its source.
- Final full workspace run with the CI-equivalent environment (`SERVER_ENV=dev`):
  **7,721 passed, 62 skipped**, no retries. The explicit coverage target was
  `apps/bcs/target/llvm-cov-target`, matching the cache configuration. No Rust test
  source was changed; #3248's 7,725-test inventory belongs to a different branch.
- Coverage gate: **100% pass rate, 86.38% overall lines**. Changed-line gate had no
  instrumentable mapped changes (build scripts and CI orchestration are outside
  the existing report). Thresholds and report scope are unchanged.
- Native MySQL **9.7.2**, five isolated schemas: **12 of 13** existing contract
  steps passed, including all four authority inventories. The full migration
  chain failed with `column 'key' is missing or NULL`. Direct execution without
  the reuse runner on a freshly recreated schema reproduced the same failure.
  CI's exact **MySQL 8.4** environment is not available locally, so its full-chain
  result and Linux cold/warm cache timing still require GitHub Actions.
- MySQL execution left both unit XML reports byte-for-byte unchanged; metadata
  reuse did not invoke compilation. Zero, ambiguous, non-ignored and failed
  contract selections are covered by regression tests and fail closed.
- Broader Singlebox CI Python checks exposed four unrelated baseline failures:
  one conservative secret-scanner expectation, two stale references to
  `singlebox/ci/pre_push.sh`, and the architecture workflow branch-trigger
  expectation. The same four failures reproduced in an untouched dev snapshot.
  They were not changed as part of this optimization.
- The first local workspace attempt inherited `SERVER_ENV=local`, causing two
  existing control-plane tests that assume `dev` to fail. Explicit `dev` resolved
  both; no business implementation, test expectation or selection was altered.
- The real installer verified/installed llvm-cov's prebuilt archive. A slow local
  nextest download exercised the pinned source fallback successfully. Fixture
  tests cover verified installation of both tools and fail-closed corruption.
- Shell syntax, focused Python static checks, workflow YAML parsing, unchanged
  non-BCS jobs/service definitions, whitespace and source-file size checks passed.

Evidence logs remain outside the repository under `/tmp/avernet-bcs-*` and
`/tmp/avernet-singlebox-ci-*`; no logs, database state or binaries are committed.
