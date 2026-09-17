# Fixed Loop live stories

Run from the repository root:

```sh
cargo test --manifest-path src/bcs/Cargo.toml -p bcs \
  --test provider_downlink_integration fixed_loop_live:: -- --nocapture
```

These tests are also discovered by the workspace gate
`bash src/bcs/scripts/ci_test.sh --fast-fail`.

Each story starts a complete BCS HTTP server on a random loopback port with
isolated memory stores and a temporary Bot directory. A local HTTP Provider receives Bot tasks and sends
terminal callbacks to `/bot/events`; a scripted OpenAI-compatible Judge selects
the editor outcome. The Event dispatcher delivers subscribed events to a local
webhook. The `loop-live-im` plugin is linked only into the integration test and
forwards actual Channel service notifications to another local HTTP receiver.
The test does not call Runtime or Store methods to advance or inspect a Run.

Setup uses an explicit local Human identity and creates the Group with
`start_initial_run: false`. It installs the ChannelBinding and authorized Event
Subscription before starting the Run through HTTP. Only these test servers allow
HTTP loopback Webhooks on the receiver's random port.

| FL-25 story | Product behavior checked |
| --- | --- |
| A | The editor chooses `revise`, then `approved`; the next entry receives the previous result, the third body is skipped, and the final task completes. |
| B | An empty break list runs the full Loop, then waits at HumanInput while the Run remains Running. The result keeps `complete`, while the exit edge reports `exhausted`. |
| C | HumanInput is the Loop entry. The first input has no previous result; the second query and direct notification contain the first result. A stale reply is rejected and the current execution accepts its reply. |

All three compare execution metadata across Run, Node detail, Graph, delivered
started/completed Events and session message history. Completed nodes have one
output message and one completed Event; skipped future nodes have neither an output nor a
started/completed Event.

These are normal execution stories against real HTTP routes with local external
service substitutes. They do not establish real model quality, deployment
upgrade safety, process failover, or the separate full Singlebox coverage gate.
