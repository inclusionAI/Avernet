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
graceful handoff with a pending task.
The handoff driver keeps the source processor running during drain so recovery
and committed effects can reach a quiescent transfer point; new source rounds
remain fenced by the runtime. Stopping that processor can leave drain unfinished
even after the target becomes shard owner. The companion Dispatch story exercises
HumanReply through a real held dispatch, ambiguous Sending/Cancel supervision,
late definitive evidence, live follow, snapshot coverage, retention and cursor Gap.
The retained-obligation probe crosses a durable scan boundary before concluding
absence; the supervisor may have already consumed the current round prefix.
It is a bounded functional suite, not a load or capacity benchmark. Test-only
Group-scenario runner workloads from [bcs-dist-test-runner](../../../../test-support/bcs-dist-test-runner/README.md)
provide decisions; all admission, checkpoint, projection,
archival, isolation and ownership behavior comes from production services.

The same story starts a second companion with `--direct` and a loopback HTTP
A2A agent. It sends external JSON-RPC requests through the Direct Gateway,
checks resolution of the fixture target, streams replacement text and a
delta, answers a remote question, and starts a new turn after confirmed
cancellation. The agent records the downstream context and remote task ID.
This process uses the production Direct directory, Gateway, Worker runner, and
Connector with an explicit test-only authorization stub, test registry, memory
coordination store, and SQLite archive. Target validation and configuration
resolution run in the real directory. Concrete authorization policy integration
is deferred and is not verified by the test stub.

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
instrumented companion (or accepts `--run-only` after
`build_e2e_companions.sh worker`), removes only its own profiles, and invalidates
stale success evidence before starting. BCS runs it in its coverage story phase,
before profile aggregation. The wrapper requires a report containing all 18
scenarios and the explicit in-memory Store and SQLite archive declarations.
The validated report is printed into the existing Singlebox `bcs/e2e.log`; on
failure the same log records diagnostics, and the BCS story phase fails.
Existing coverage thresholds and source exclusions are unchanged.
