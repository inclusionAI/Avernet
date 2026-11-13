# Explicit BCN runtime composition

This composition root wires Gateway, Worker and Connector to shared RuntimePorts
and the Direct/structured reducers. All roles share environment, tenant and runtime
namespace; process/node incarnation is distinct. It does not replace the old
execution stack or migrate in-flight sessions.

Configuration requires `scope`, `profile` (test/local/production), and `storage`
(memory/external). Admission is disabled by default. Enabling requires an explicit
nonempty target allowlist. Unknown top-level and limits keys are rejected.
All RuntimeDefaults are fixed for this MVP, and every non-default override,
including zero and a shard count other than 1024, is rejected. Catalog policies
must use pending tasks 16, graph nodes 32 and agents 5. Per-session execution and
human timeout values remain validated by the reducer.

Fixed defaults: 1024 shards; heartbeat 1 s / TTL 3 s; ownership inspection 500 ms;
inbox batch 100; recovery concurrency 64; dispatch supervision 1 s; progress 5 s /
stall 15 s; token batch 200 ms; pending tasks 16; graph nodes 32 / agents 5;
poison takeovers 3; drain 30 s. Configuration cannot silently tune only part of a
pipeline. Selection of durable storage or policy/credential-checked HTTP clients
belongs to the deployment composition root.

`build(config, components, ports)` requires injected catalog, policy registrations,
Judge and target-bound connector registrations. `ConnectorSelection` constructs
the shipped A2A or Provider connector using a caller-supplied policy-checked Card
or endpoint and credential-bound reqwest 0.13 client; it also accepts an explicit
local connector. No automatic discovery, retry transport switch or secret lookup
occurs here. The allowlist wraps only catalog resolution for a new Session.
Existing sessions keep frozen specs; every operation still passes authorization.
Disabled fresh admission preserves existing queries, replies and cancellation.

Memory selection is explicit and only works in local/test. External selection
requires supplied RuntimePorts; production has no automatic memory fallback.
Runtime.run runs the two independent lifecycles with the shared shutdown receiver;
each uses its existing bounded drain behavior. No network listener is implicitly
started. Mount the delivery router separately with an explicit authentication
boundary. Production implementations and deployment evidence remain external work.

Run the local single-process harness (from a public environment):

```sh
cargo run --manifest-path /path/to/avernet/apps/bcs/Cargo.toml -p bcn-runtime --example local
```

It uses shared volatile stores and an explicit fake connector, demonstrating both
Direct and structured execution through saved results. These tests and the example
do not simulate durable multi-process storage, crash safety or production capacity.

For new structured Sessions the catalog wrapper freezes live_output_nodes to the
final_output BotTask (or [] for a terminal HumanInput). Only that node's live
output appears in public snapshots/streams. Internal per-node projections and
all saved node results remain intact; intermediate saved results are available
through ResultStore/read_group_records and are not extra A2A Task artifacts in
this MVP. None on older specs preserves their existing aggregate behavior.
