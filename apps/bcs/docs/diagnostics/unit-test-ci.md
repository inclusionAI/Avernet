# BCS unit-test CI failures

BCS owns the test runner, process-test helpers and coverage reports described
here. Start with the failing step in the
[unit-test workflow](../../../../.github/workflows/unit-tests.yml), then inspect
the JUnit and coverage artifacts from that same run.

## Tests fail before the BCS child starts

When several process tests report `No such file or directory`, distinguish the
executable path from missing configuration or a startup failure. The shared
[process helper](../../crates/bootstrap/bcs/tests/support/e2e_helpers.rs) includes
the selected executable path in spawn errors. An error before spawning has no
BCS readiness or application assertion evidence.

Cargo's intermediate build layout is not a path contract. Test executables can
live below `debug/build/<package>/<hash>/out/`, so walking two parents from
`current_exe()` does not locate `debug/bcs` reliably. The helper uses runtime
`CARGO_BIN_EXE_bcs`, including nextest's remapped path when reusing a build. The
compile-time Cargo value supports runners that do not set the runtime variable.

Run the affected process suites from `apps/bcs`:

```bash
cargo nextest run -p bcs --test integration_http_api --test e2e_bot_authority --retries 0
```

These suites verify HTTP readiness, independent ports and Bot Authority flows.
Passing them does not by itself establish workspace coverage or MySQL behavior.

## The test step succeeds but the final gate fails

In its non-fast-fail CI mode, [ci_test.sh](../../scripts/ci_test.sh) finishes the
inventory and writes reports even if some tests fail. The later
[cov_gate.py](../../scripts/cov_gate.py) step checks the JUnit pass rate and both
coverage thresholds. Read its three verdicts separately; a successful runner
step alone does not prove all tests passed.

From `apps/bcs`, run the unit producer and then the gate using the actual base
SHA selected by the workflow:

```bash
bash scripts/ci_test.sh --coverage --reuse-build
uv run --no-project python scripts/cov_gate.py --base-ref <base-sha> --bcs-dir "$PWD"
```

The required pass rate is 100%. Overall line coverage must exceed 87%. Changed
line coverage must exceed 85% when at least 51 instrumentable changed lines
map to the report; smaller changes are report-only for that metric.

## Coverage contradicts a passing behavior assertion

Check actual execution, profile collection and source mapping separately:

1. Restore failed process tests before estimating remaining coverage work.
2. Check that instrumented children inherit `LLVM_PROFILE_FILE` and finish
   writing profiles before the report is generated.
3. Compare the marked source lines with the LLVM report and the test's observable
   assertions. Macro expansion lines require evidence before being classified
   as untested behavior or a reporting defect.
4. Add meaningful cases for confirmed missing behavior, or a minimal regression
   for a proven reporting defect. Preserve the existing thresholds and scope.

The `tracing/log` feature adds fallback forwarding into the legacy `log` facade.
Its macro-generated fallback expressions can be reported as unhit on the same
source lines whose native tracing fields were observed by a live subscriber.
Keep production dependency features unchanged when repairing test coverage.
The legacy `log` to tracing bridge is checked by the
[bootstrap bridge test](../../crates/bootstrap/bcs/tests/log_facade_bridge.rs).
For the intended logging behavior, see
the [logging ownership](../observability/request-operation-logging.md).

The [reusable coverage runner](../../scripts/ci/reusable_coverage.sh) finalizes
unit reports before ignored MySQL contracts execute. The
[contract runner](../../scripts/ci_contract_test.py) puts their profiles and JUnit
results in separate directories. Keep these scopes separate when comparing
reports. Exact coverage percentages depend on the tested revision and toolchain;
this guide does not claim that fixing process startup guarantees the gates pass.
