# Push and CI validation

Read before pushing or changing test/CI gates. Commands below run from the
repository root. Backend and other module guides select the affected checks.

## Pre-push Module Selection

Install the repository hooks separately in every Git worktree:

```bash
devops/hook/install_git_hooks.sh
```

The pre-push hook first scans added lines in each pushed commit range for
likely plaintext credentials. It blocks the push on private keys, recognizable
provider tokens (including Alibaba Cloud AccessKey/STS credentials),
bearer/JWT credentials, or high-entropy values assigned to credential-like
fields. When a finding is reported, the hook prints the
corresponding added line with the matched sensitive value partially masked,
alongside its file, line, and rule, so authors can locate and remove it without
leaking the complete credential into terminal or CI logs. This check is always
enabled and is independent of the lint/test mode below.

By default the pre-push hook runs in **lint-only** mode: for changed Python
modules it runs the fast `python_sast_local.sh` SAST/lint gate, but skips the
heavier unit tests, changed-line coverage, and Singlebox E2E. Set
`OCB_PRE_PUSH_RUN_CI=1` to run the full gates for a push, or run
`devops/hook/pre_push.sh` manually. The module-gate table below describes the
full behavior; in lint-only mode only the SAST/lint step of each Python module
runs, and modules without a standalone lint step (`apps/gateway`, `apps/frontend`,
`apps/bcs`, and the singlebox coverage paths) run nothing.

```bash
OCB_PRE_PUSH_RUN_CI=1 git push
```

The pre-push hook models the change set of a pull request. Its merge target
defaults to the remote branch `origin/dev`. Override it persistently for the
current worktree or repository when the eventual PR targets another branch:

```bash
git config --worktree avernet.prePush.mergeTarget upstream/release/2026-07
```

Use `AVERNET_PRE_PUSH_MERGE_TARGET` for a one-command override; it has higher
priority than Git config:

```bash
AVERNET_PRE_PUSH_MERGE_TARGET=origin/main git push
```

Target values must use `<remote>/<branch>` form. Before selecting modules, the
hook fetches the target branch, resolves its latest commit SHA, calculates
`git merge-base <target-sha> <local-sha>`, and diffs that merge base against
the local SHA. Do not replace this with a direct `git diff origin/dev HEAD`:
when `dev` has advanced but the feature has not rebased, a direct tree diff
incorrectly includes target-only paths.

The hook fails the push if the configured remote branch cannot be fetched or
resolved, or if it has no merge base with the pushed commit. It must not fall
back to a stale target, the pushed branch's old remote SHA, or the root commit,
because those ranges can run unrelated module tests.

Module gates are selected from the committed files in the resulting diff:

| Changed path | Pre-push gate |
| --- | --- |
| `apps/backend/` | Backend SAST, unit tests, changed-line coverage, and singlebox coverage |
| `apps/baas/` | BaaS SAST, unit tests, changed-line coverage, and singlebox coverage |
| `engine/adapter/` | Engine SAST, unit tests, and changed-line coverage |
| `apps/bcs/` | BCS/BCN unit tests in fast-fail mode, then unified singlebox coverage with BCS user-story E2E |
| `apps/frontend/` | Frontend CI |
| `apps/proxy/` | sandbox-proxy lint, unit tests, and changed-line coverage |
| singlebox scripts and Backend/BaaS acceptance or E2E paths | singlebox coverage |

The hook only checks committed changes in the pushed ref. Uncommitted working
tree changes are outside the natural boundary of a pre-push hook.

The unified `singlebox/ci/singlebox_coverage.sh` starts one standalone product
stack and reuses it for Backend acceptance and BCS user-story E2E. BCS runs as
an LLVM-instrumented server; the gate requires all BCS E2E stories to pass,
runtime line coverage of at least 54%, method coverage of at least 48%,
100% HTTP endpoint and bcs-cli leaf-command coverage, and E2E changed-line
coverage above 65% for changes with at least 20 instrumentable changed lines in
the BCS E2E scope (no exemption; smaller or out-of-scope changes are reported
only). Its canonical artifacts
are copied to `singlebox/.dependencies/coverage/singlebox/reports/bcs/` and are
included in `summary.json`, `summary.md`, and `dashboard.html`. Keep pre-push
and `.github/workflows/singlebox-coverage.yml` pointed at this same entrypoint,
then run `verify_singlebox_coverage_artifacts.py` against the generated report
directory so local pushes and GitHub PRs enforce the same artifact baseline.

The BCS unit-test CI gate requires a 100% pass rate and overall line coverage
strictly above 87%. Changed-line coverage must be strictly above 85% when
there are more than 50 instrumentable changed lines; changes with 1–50 such
lines are reported only, and zero instrumentable changed lines are N/A. This
exemption never relaxes the pass-rate or overall-line coverage gates.

### Singlebox Coverage Details

`singlebox/ci/singlebox_coverage.sh` reads
`singlebox/ci/singlebox_coverage_modules.yaml`. With no `--module` arguments it
runs every registered module; focused diagnosis can select one or more modules
with repeated `--module <name>` arguments. The runner starts one standalone
product stack, shares it across Backend acceptance stories and BCS user-story
E2E, then calculates the Core, Router API, and Plugin API denominators declared
by each module.

The per-module non-regression results and the shared-stack evidence are written
to `singlebox/.dependencies/coverage/singlebox/reports/`: `summary.json`,
`summary.md`, `dashboard.html`, acceptance JUnit/logs, Backend and BaaS
coverage reports, plus the copied BCS reports under `bcs/`. GitHub's
`singlebox-coverage-artifacts` artifact uploads that same directory. The
artifact verifier must run against this report directory after the coverage
runner, so local pre-push and PR CI enforce the same result.

When adding a module, add meaningful live acceptance stories, declare the
complete Core/Router/Plugin denominators in
`singlebox/ci/singlebox_coverage_modules.yaml`, establish thresholds from a
fresh focused run, then run the default all-module gate to catch shared-stack
interference. Do not inflate a result by excluding production Core paths or by
adding test-only calls to domain logic.
