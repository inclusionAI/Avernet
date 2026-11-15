# CI trigger isolation and conservative Singlebox performance

## Status

Design approved by the user on 2026-11-12. Local implementation and focused
verification are complete; see [Validation](validation.md). Remote runtime
validation (job duration, phase timings, artifact size/file count and unchanged
coverage) has not started.

## Owners and contracts

- GitHub Actions ownership: `.github/workflows/unit-tests.yml`,
  `.github/workflows/backend-unit-tests.yml` and
  `.github/workflows/singlebox-coverage.yml`.
- Backend verification ownership: `apps/backend/scripts/ci_test.sh` and the
  [Backend development guide](../../../apps/backend/README.md).
- Singlebox orchestration ownership: `singlebox/ci/singlebox_coverage.sh` and
  `singlebox/modules/bcs.sh`.
- Repository constraints: [development rules](../../agents/development.md),
  [push and CI validation](../../agents/validation.md), and
  [CI enforcement](../../arch/ci.enforce.md).

This is a reversible CI change, not a new product or architecture contract. No
ADR is required.

## Problems and evidence

### BCS-only configuration runs Backend unit tests

The Backend detector includes the entire shared
`.github/workflows/unit-tests.yml` in its pathspec. Consequently a BCS-only
workflow edit is indistinguishable from a Backend workflow edit.

PR #3893 and run 43166317698 provide an exact reproduction:

- The PR has no `apps/backend/**` change.
- Commit `94f001b4a404eb6867ec7af94263d5f0699926df` changes only the BCS
  authority scenario count from 63 to 64 in the shared workflow.
- Backend detection reports the shared workflow as its only match and the
  Backend unit job runs for approximately 16m33s.

Removing the shared workflow from the pathspec without replacing its coverage
would create the opposite bug: edits to Backend CI commands or gates could skip
Backend validation.

### Singlebox retains avoidable serial and repeated work

Recent successful runs show a normal 20–24 minute range rather than a fixed
30-minute baseline. Dev push run 43153014066 completed in 20m33s; the inspected
PR sample median is approximately 24.4 minutes. Dependency installation and
build variance explain some longer outliers.

The approved first batch targets only measured avoidable work:

- Python coverage reports run after, rather than overlap with, the independent
  BCS aggregate phase. Recent Python report cost is approximately 79 seconds.
- Cargo registry/git content is restored and saved by both `actions/cache` and
  `Swatinem/rust-cache`.
- The BCS panel is built during BCS setup and rebuilt by the coverage startup
  path in the same checkout.
- Artifact upload includes reconstructable `node_modules` trees; the inspected
  artifact contains about 49,683 files and upload takes roughly 40–55 seconds.

## Scope

### Included

1. Isolate Backend unit-test CI into its own workflow with precise change
   detection and a stable required-check name.
2. Overlap Python coverage reports with BCS aggregation while preserving all
   producer barriers, reports, status propagation and cleanup behavior.
3. Remove the duplicate Cargo registry/git cache while retaining `rust-cache`.
4. Reuse a fresh BCS panel build and rebuild it whenever its inputs change.
5. Exclude only reconstructable `node_modules` directories from Singlebox
   artifacts.
6. Add focused regression tests and update affected verification documentation.

### Excluded

- Parallelizing ordinary acceptance with Owner acceptance.
- Parallelizing Backend acceptance with BCS shared stories.
- Changing Rust workspace-cache policy, compiler flags or toolchain pinning.
- Parallelizing the three `cargo llvm-cov report` invocations.
- Removing or skipping frontend, demo bots, tests, coverage raw data, runtime
  databases, HTML reports, JUnit or diagnostic logs.
- Changing coverage denominators, thresholds, changed-line gates, test commands,
  artifact verification requirements or required-check semantics.
- Replacing apt-based protoc installation; that is already being addressed in a
  separate workflow change and is not part of this batch.

## Decision: independent Backend workflow

Create `.github/workflows/backend-unit-tests.yml` containing the existing
Backend job with these changes only:

- Workflow `name` and concurrency group are distinct from the shared Unit Tests
  workflow so their runs cannot cancel each other.
- The job remains named `Backend unit tests`, preserving the required status
  check consumed by branch protection.
- Backend detection watches `apps/backend`,
  `singlebox/ci/legacy_skill_compatibility.sh`, and
  `.github/workflows/backend-unit-tests.yml`.
- Python setup, uv setup, test commands, coverage gates, Legacy Skill gate,
  timeout and artifact paths remain unchanged.

Remove the Backend job from `.github/workflows/unit-tests.yml`; its other jobs
and their behavior remain unchanged.

A BCS-only edit therefore causes no full Backend test execution. An edit to the
Backend workflow, Backend sources or Legacy Skill entrypoint still executes the
same Backend gate. The small always-created detection job is acceptable; this
change does not attempt to suppress the workflow run itself.

A job-aware diff parser was rejected because line-based or text-based parsing is
fragile under job moves, indentation changes and future shared configuration.
Simply deleting the workflow path was rejected because it loses necessary CI
coverage.

## Decision: overlap independent coverage reports

Retain the canonical `singlebox/ci/singlebox_coverage.sh` entrypoint and use this
order:

1. Finish acceptance, BCS shared stories and all companion producers.
2. Flush the live Backend/BaaS writers with USR1. BaaS has no SIGTERM coverage
   handler, so this pre-stop save is mandatory.
3. Stop Backend and then BaaS so their shutdown/lifespan coverage is written before
   any combine deletes raw inputs. BCS remains running for shutdown calls and its
   own later aggregate.
4. Start the existing Backend and BaaS `combine_python_coverage` branches in the
   background.
5. Run the existing BCS aggregate phase concurrently.
6. Join both Python branches and the BCS aggregate before writing summary or
   module artifacts.
7. Run the existing final flush and stack cleanup only after the report
   producers have exited.

The existing Backend/BaaS internal parallelism remains unchanged. The two
branches use disjoint inputs and outputs: Python reads `.coverage*` data and
writes Backend/BaaS reports; BCS reads LLVM profiles and writes BCS reports.

The parent records the two per-component Python report durations
(`python_backend_report` and `python_baas_report`), the
`python_coverage_reports` overlap-to-join window, the independent
`bcs_coverage_report` aggregate timing, plus the earlier
`python_services_stop` phase. The window spans first combine launch through last
join, so it is not pure Python time, and no timing row is serial wall-clock
time. Existing status priority remains acceptance, BCS
E2E/aggregate, Backend report, BaaS report, summary, module artifacts, required
artifacts. A failing branch does not suppress the other branch's diagnostics.
Normal, failure and cancellation paths reap background report processes before
final coverage flushing or stack cleanup. A failure reported by the service-stop
command is fail-closed: partial data must not be combined after such a reported
failure.

Implementation review identified why a USR1 flush alone is insufficient:
`coverage combine` deletes its raw inputs by default, while Backend/BaaS write
shutdown/lifespan coverage only at process exit. A second review also confirmed
that BaaS has no SIGTERM coverage handler, so the pre-stop USR1 save and the
stop-before-combine barrier are both mandatory to preserve the original gated
evidence. The final cleanup performs one flush; no post-combine flush may
recreate empty raw files.

## Decision: remove duplicate Cargo registry cache

Delete the standalone `actions/cache` entry for `~/.cargo/registry` and
`~/.cargo/git`. Keep `Swatinem/rust-cache`, which already owns those paths as
well as the instrumented target. Keep its `cache-workspace-crates: false`,
`cache-bin: false`, environment inputs and trusted-push-only `save-if` behavior.
No coverage profiles or generated reports are cached.

## Decision: reuse only fresh BCS panel output

Add panel-output freshness evaluation inside `singlebox/modules/bcs.sh` rather
than expanding the already oversized `singlebox/singlebox.sh`.

`build_bcs_panel_asset` skips `npm run build` only when
`dist/index.umd.js` exists and is newer than every panel input. Inputs include
package/lock files, source files and build configuration. `node_modules` and
`dist` are excluded from the input walk. Missing output or any newer input keeps
the existing dependency-install/build/failure path. The expected artifact check
remains mandatory after a build.

This preserves source freshness in a restored checkout while eliminating the
second build in the same coverage setup/start sequence.

## Decision: exclude only reconstructable artifact dependencies

Add an artifact exclusion for `**/node_modules/**`. Preserve coverage raw data,
combined reports, runtime databases, standalone configuration, HTML reports,
JUnit and logs. Preserve the existing Provider credential exclusions and run
artifact verification before upload exactly as today.

## Acceptance criteria

- A BCS-only edit to `unit-tests.yml` does not execute Backend setup, unit tests,
  coverage or Legacy Skill steps.
- A Backend-source, Legacy Skill entrypoint or Backend-workflow edit still runs
  the unchanged Backend gate under the stable `Backend unit tests` check name.
- BCS aggregation cannot start before acceptance, shared stories and companion
  producers finish. Live Python writers must receive USR1 before Backend and BaaS
  exit successfully, and only then may either Python combine start. Summary
  generation cannot start before BCS aggregation and both Python report branches
  finish.
- Report failures retain their exit status and available artifacts; cancellation
  leaves no background report producer running before stack cleanup.
- Fresh panel output is reused, while missing or stale output is rebuilt.
- Singlebox artifact paths retain all evidence and credentials exclusions while
  omitting only `node_modules`.
- No modified or added source file exceeds the repository file-size limit.

## Validation

Use red-green regression coverage for:

- Backend trigger selection across BCS-only, Backend-source, Backend-workflow and
  Legacy Skill changes.
- Report overlap ordering, pre-stop USR1 coverage saves, Python writer shutdown
  before combine, summary barriers, failure propagation and cancellation cleanup
  using executable pipeline fixtures.
- Panel first build, fresh-output reuse and rebuild after an input change.
- Workflow cache and artifact configuration assertions.

Run the focused Singlebox CI Python tests, the canonical shell coverage-gate
fixtures, relevant BCS CI tooling tests, YAML parsing for both workflows,
`bash -n` for changed shell files, and `git diff --check`.

A full local product stack and a measured remote performance improvement are not
claimed by this design. After implementation, compare a real GitHub run's job
duration, `phase-timings.tsv`, artifact size/file count and all coverage results
against the recorded baseline. Report unavailable remote validation separately.

## Documentation and delivery

Update the affected reusable verification references so Backend CI points to its
new workflow and the Singlebox report-overlap behavior remains accurately
described beside the canonical entrypoint. Do not create a new architecture
contract or duplicate threshold documentation.

Implementation, test evidence, performance measurements and any remaining gaps
must be appended as a delivery record. Merge, deployment and remote CI success
are separate outcomes and are not implied by this approved design.
