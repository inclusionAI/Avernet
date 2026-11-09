# Validation — isolated companion overlap and three Owner workers

## Baseline

PR #3711, commit b6c769a85, run 42565738267: 27m00s, 73 acceptance tests
(68 serial + 5 Owner), Owner wall time 268.182s, BCS E2E phase 466s.
Instrumented Rust cache missed. This is not a controlled hot-cache baseline.

## Local evidence

- Red/green execution tests cover the two BCS phases; default standalone test
  inventory stays intact. Shared-only retains runtime profiles and leaves the
  server running. Aggregate-only preserves HTTP/CLI evidence and executes all
  gates. Invalid phase combinations fail before touching services.
- Real subprocess supervision with a fake Cargo boundary proves one build for
  both binaries, Caller/OAuth overlap, joining both producers on a test failure,
  build-failure invalidation, and SIGTERM descendant cleanup.
- Full canonical shell pipeline fixtures prove overlap with acceptance, the
  aggregation barrier, failure propagation, and cancellation ordering.
- Actual pytest argv observations verify Owner default 3, fallback 1/2, and
  rejected 0/4/negative/non-numeric settings. No production reschedule change.
- Focused execution tests: 26 passed (including existing OAuth regressions).
- All CI Python tests: 263 passed, 3 failed, 131 subtests passed. The same three
  baseline failures concern absent `singlebox/ci/pre_push.sh` (two tests) and
  the unrelated architecture workflow branch trigger (one test).
- `bash singlebox/verity/test_singlebox_coverage_gate.sh`: PASS. Its fixture now
  follows the explicitly requested relative coverage root instead of hardcoding
  the default, addressing the known baseline test-fixture failure without
  changing production isolation behavior.
- Changed shell syntax, Python parsing, YAML parsing, focused Ruff checks and
  `git diff --check`: PASS. Changed sources remain below 1,000 physical lines.

## Runtime validation

No local product stack was launched: this worktree does not have a provisioned
isolated stack. The pre-existing backend Python environment was used only to
run tooling tests. A fresh PR CI run is required to validate real SQLite/BCS
contention and actual elapsed time; local subprocess fixtures are not evidence
of live concurrency stability or measured speedup.

## New evidence and rollback

- Parent phase start/end messages now appear live; `phase-timings.tsv` separates
  `bcs_shared_stories`, `bcs_companion_join` and `bcs_coverage_report`.
- `reports/bcs/companion-timings.json` records build/Caller/OAuth exit codes and
  monotonic elapsed durations. These overlap parent phases and must not be
  added to parent wall-clock timings. Separate build/caller/oauth logs remain
  in the uploaded reports directory.
- `SINGLEBOX_ACCEPTANCE_OWNER_WORKERS=1` or `2` restores the conservative Owner
  scheduling. Reverting this change restores sequential companion execution;
  standalone `e2e_coverage.sh` still performs the original complete flow.
