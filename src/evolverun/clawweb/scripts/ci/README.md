# ClawWeb test CI

From `src/evolverun/clawweb`, use Node 20.19.0:

```sh
npm ci --no-audit --no-fund
npm run ci:build
npm run ci:check
node --test scripts/ci/run-tests.test.mjs
npm run test:ci
```

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

GitHub publishes the summary and uploads reports even after test failure. Only
the added test step is advisory during observation; install/build/check remain
required. No coverage threshold is introduced. Existing business test failures
must be tracked separately; this change does not repair or skip them.

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
