# ClawWeb test CI

From `src/evolverun/clawweb`, use Node 22.14.0 to match CI:

```sh
npm ci --no-audit --no-fund
npm run ci:build
npm run ci:check
node --test scripts/ci/run-tests.test.mjs
npm run test:ci
```

The private development workspace requires Node >=22.14.0; CI pins 22.14.0 for
reproducibility. The monitoring preview HTTP tests import `node:sqlite`, which
Node 20 does not provide. This changes the development/test baseline, not the
published packages' production runtime contracts. After switching Node major
versions, rerun `npm ci` so native dependencies such as `better-sqlite3` match
the selected runtime.

This workflow only needs this repository. Packages are discovered from the existing
workspace list. Build precedes tests because package exports reference `dist`.
The CI runner runs packages serially and retains every package result; an earlier
failure cannot be overwritten by a later success. Timeouts, missing reports and
failed suites return a nonzero status. A package with no tests is `NO_TESTS`, not
a passing test.

Reports: `test-results/summary.{md,json}`, `junit.xml`, `coverage/` (LCOV,
Cobertura and Istanbul JSON), and `packages/*/` (individual JSON/JUnit/HTML).
Coverage measures each package's own server/web/src source, including untouched
files, excluding tests, fixtures and generated output. Shared code executed only
through another package is not counted as that package's own coverage. Incomplete
coverage is labelled explicitly; do not interpret it as complete application coverage.

GitHub publishes the summary and uploads reports even after test failure. Install,
build, check and test failures all fail the workflow. No coverage threshold is
introduced. Existing business test failures must be tracked separately; this change does not repair or skip them.

## Local entry

From this repository's `src/evolverun/clawweb` directory:

```sh
./scripts/test-clawweb.sh
```

The script installs dependencies, builds and checks packages, then runs every
public package test with coverage. Existing test failures remain visible and
produce a nonzero exit status.

To test the internal composition, pass the OCB repository path:

```sh
./scripts/test-clawweb.sh --ocb /path/to/open_ocb
```

This uses the same local symlink association as `start-clawweb.sh`; it does not
checkout, copy, or switch either repository.
