# Validation

Local implementation and verification evidence for this spec. Remote job
duration, phase timings, artifact size/file count and coverage results are not
measured here; see [Remote runtime gaps](#remote-runtime-gaps).

## Local execution

Commands run from the repository root on 2026-11-12.

| Command | Exit | Result |
| --- | --- | --- |
| `apps/backend/.venv/bin/python -m pytest -q` over the 12 prescribed focused test files | 1 | `1 failed, 69 passed, 26 subtests passed in 24.86s` |
| `bash singlebox/verity/test_singlebox_coverage_gate.sh` | 13 | Pre-existing baseline failure; output was two copies of `singlebox coverage should use an isolated standalone OpenClaw root` (the macOS `/var` vs `/private/var` standalone-root assertion). This gate did not pass and was not changed. |
| `bash -n singlebox/ci/singlebox_coverage.sh singlebox/modules/bcs.sh` | 0 | Both shell files parse. |
| YAML `safe_load` + non-empty `jobs` assertion for `unit-tests.yml`, `backend-unit-tests.yml`, `singlebox-coverage.yml` | 0 | Printed `all changed workflows parse successfully`. |
| Supplemental: `apps/backend/.venv/bin/python -m pytest -q singlebox/ci/tests/test_just_test_coverage_gate.py` (comment-only file edit, not in the prescribed suite) | 0 | `10 passed, 14 subtests passed in 2.26s`. |
| `git diff --check` | 0 | Clean; no whitespace errors. |

The only focused-suite failure is the documented pre-existing
`test_refactory_collaboration_branch_triggers_required_workflows` failure: the
test requires `dev_refactory_collaboration` on every listed workflow, but
`architecture-checks.yml` still triggers only `main`. No new failure and no
skipped test appeared in the focused suite (no environment-only Rust test was
selected).

## Trigger-selection evidence

`singlebox/ci/tests/test_unit_test_workflow_diff.py` passed, including:

- `test_backend_pathspec_ignores_bcs_workflow_but_catches_backend_inputs`, an
  executable Git fixture: a BCS-only `unit-tests.yml` commit yields no match
  against the Backend pathspec, while changes to `apps/backend`,
  `singlebox/ci/legacy_skill_compatibility.sh` and
  `.github/workflows/backend-unit-tests.yml` each match.
- `test_backend_workflow_owns_only_backend_change_detection`: the Backend
  workflow contains its own detection pathspec, the shared `unit-tests.yml` no
  longer contains a `backend` job, workflow/concurrency names stay distinct and
  the job remains `Backend unit tests`.
- `test_pr_module_diffs_use_the_checked_out_merge_parent`: the Backend merge
  parent expression and `--base "$BACKEND_BASE_REF"` live in the dedicated
  workflow, while the other modules remain in the shared workflow.

`test_refactory_collaboration_branch_triggers_required_workflows` also includes
`backend-unit-tests.yml` in its required workflow list; its single failure is
the pre-existing `architecture-checks.yml` baseline noted above.

## Coverage orchestration evidence

Passed in the focused suite:

- `test_singlebox_parallel_pipeline.py`: executable process-boundary fixtures
  assert companion join, pre-stop USR1 save, `python_services_stop` before both
  combines, Python report overlap with the BCS aggregate, join before summary,
  failure status propagation and cancellation reaping. Raw `.coverage*` inputs
  are removed by the fixture's combine and must not be recreated by the final
  cleanup flush; the live writer is signalled exactly once.
- `test_singlebox_companion_coverage.py`, `test_singlebox_e2e_phases.py`,
  `test_singlebox_acceptance_orchestration.py` and
  `test_merge_junit_reports.py`: existing producer barriers, phase handling and
  JUnit merge behavior remain intact.
- `test_bcs_panel_asset.py`: the first build runs, a fresh output is reused, and
  a newer source input forces a rebuild; `node_modules`/`dist` are excluded from
  the freshness walk.
- `test_singlebox_coverage_workflow.py` and the BCS CI tooling tests assert the
  workflow cache and artifact configuration.

Artifact upload keeps its coverage evidence and credential exclusions and adds
only `!**/node_modules/**`, so dependency trees are not uploaded; the resulting
size/file-count reduction remains a remote gap.

Cache change: `singlebox-coverage.yml` now has one Cargo cache owner,
`Swatinem/rust-cache`; the separate `actions/cache` entry for
`~/.cargo/registry` and `~/.cargo/git` is gone. `rust-cache` retains registry
indexes/archives and git databases, but it re-materializes `registry/src` and
git checkouts, so it is not a byte-for-byte superset of the removed cache.
Workspace crates stay excluded (`cache-workspace-crates: false`) because they
are the source under test. Coverage profiles are not reused across
instrumentation changes: `RUSTFLAGS`, `CARGO_PROFILE_` and `CARGO_INCREMENTAL`
participate in the cache key, and `singlebox_coverage.sh` deletes `.profraw`
files before each run, so instrumentation/profile changes force a rebuild and
stale profiles are not restored. The durable description lives in
[Push and CI validation](../../agents/validation.md).

No acceptance/shared-story parallelism was added. Production test commands and
coverage gates are unchanged; the CI self-test list only adds
`test_bcs_panel_asset.py`, and artifact upload only adds the
`!**/node_modules/**` exclusion while retaining the evidence and credential
exclusions.

## File-size and diff hygiene

Representative changed/added source files (line counts from `wc -l`):

| File | Lines |
| --- | --- |
| `singlebox/ci/singlebox_coverage.sh` | 887 |
| `singlebox/modules/bcs.sh` | 948 |
| `.github/workflows/unit-tests.yml` | 584 |
| `singlebox/ci/tests/test_singlebox_parallel_pipeline.py` | 280 |
| `singlebox/ci/tests/test_unit_test_workflow_diff.py` | 250 |
| `.github/workflows/backend-unit-tests.yml` | 96 |
| `singlebox/ci/tests/test_bcs_panel_asset.py` | 77 |

No file reaches the 1,000-line limit. `git status --short` shows the expected
modified workflow/tooling/test files plus the new Backend workflow, the new
panel-asset test and this spec directory (untracked files were included in the
scope inspection, not only `git diff`). `git diff --stat` for tracked files was
`15 files changed, 457 insertions(+), 129 deletions(-)` after the final focused
verification. `Backend unit tests` remains the Backend job name.

## Remote runtime gaps

No full local product stack and no real GitHub Actions run were executed. This
record therefore does not claim a measured speedup, coverage result or artifact
reduction. The following remain remote acceptance evidence, not local claims:

- Actual Backend/Singlebox job duration and `phase-timings.tsv` phase timings,
  including the `python_backend_report`/`python_baas_report` per-component rows
  and the `python_coverage_reports` overlap-to-join window (not pure Python
  time; do not sum the rows as serial wall-clock time).
- Whether Python report overlap reduces wall-clock time in a real run.
- Artifact size and file count after excluding `node_modules`.
- Whether unchanged coverage thresholds and results hold on the runner.
- Whether the isolated Backend workflow behaves as expected on GitHub,
  including the `Backend unit tests` required check.

## Known comment drift

`singlebox/singlebox.sh:738-740` still says the coverage panel asset is
"Always rebuild", although `build_bcs_panel_asset` now builds only missing or
stale output. That comment was intentionally not edited here because the file is
already oversized; the code behavior is covered by `test_bcs_panel_asset.py`.