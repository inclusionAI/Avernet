# Worker runtime Singlebox companion

`bcs-worker-e2e` is available only with the `worker-runtime-e2e` feature. It
assembles the public Worker runtime/processor, Inbox, TaskFeed, Dispatch Supervisor
and administrative application facade. `bcs-test-support::store_model::Model`
is the explicit in-memory Store mock; Result/Blob use the existing SQLite provider
and migration chain. No test fixture is added to production startup.

The external [process story](../../../../../scripts/e2e-test/worker_runtime_story.py)
drives JSON-lines commands over stdin/stdout and checks document CAS/projection,
normal and large-AppendText archive/restore, duplicate admission, late Cancel,
independent cancellation, Quarantined/Blocked handling, authorization/recovery,
takeover by a second Worker incarnation sharing the same memory Store, and
graceful handoff with a pending task. The companion Dispatch story exercises
HumanReply through a real held dispatch, ambiguous Sending/Cancel supervision,
late definitive evidence, live follow, snapshot coverage, retention and cursor Gap.
It is a bounded functional suite, not a load or capacity benchmark. Test-only
Runner workloads from [bcs-dist-test-runner](../../../../test-support/bcs-dist-test-runner/README.md)
provide decisions; all admission, checkpoint, projection,
archival, isolation and ownership behavior comes from production services.

The Store mock disappears on process exit. In-process Worker takeover must not
be reported as persistence across a process or storage-node restart. Actual
persistent-provider verification remains separate; SQLite archives do not make
the Store mock durable.

Run from the repository root:

```bash
cargo build --locked --manifest-path apps/bcs/Cargo.toml -p bcs \
  --features worker-runtime-e2e --bin bcs-worker-e2e
python3 apps/bcs/scripts/e2e-test/worker_runtime_story.py \
  --binary apps/bcs/target/debug/bcs-worker-e2e \
  --report apps/bcs/target/worker-e2e.json
```

[worker_e2e_coverage.sh](../../../../../scripts/worker_e2e_coverage.sh) builds an
instrumented companion (or accepts `--run-only` after the shared builder), removes
only its own profiles, and invalidates stale success evidence before starting.
The Singlebox companion supervisor joins Caller, OAuth and Worker before
aggregation and propagates each failure. The artifact gate requires all Worker
stories and the explicit memory-only declaration. Existing coverage thresholds
and source exclusions are unchanged.
