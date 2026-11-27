# Context boundary

- Role: public generic Worker runtime, processor, Dispatch supervisor and output
  read facade.
- Consumes: membership/shard/session/Inbox/Dispatch/TaskFeed/Result/Blob/control
  repository ports, shared scan budget, typed mode runner, output projection,
  injected time, peer transport and pure output formatter.
- Owns: readiness, HRW, bounded recovery and handoff, one round per Session,
  atomic checkpoint/effect preparation, replay, task queue and two-commit terminal
  publication, durable isolation, authorized recovery and local resource fairness.
- Output reads: resolve immutable references, verify their digests and invoke the
  injected formatter; Gateway forwards the resulting transient views/events.
- Does not own: storage commands, transport authentication, enterprise assembly,
  concrete group workflow algorithms, Agent transport or Connector execution.
- Entry points: [runtime](README.md), [processor](src/processor/README.md),
  [supervisor](src/supervisor/README.md).
- Callers: explicit host composition and authenticated runtime application facades.
- Contract changes require failure-path integration tests and provider acceptance;
  model tests alone do not prove production timing or capacity.

The current RunnerRegistry adapts one installed mode/version. Every Worker in
one shared namespace must install the same supported mode/version; Direct-only
and other-mode deployments must use separate namespaces. Shard assignment does
not route by mode. Unsupported configurations are isolated, not silently handed
to another Worker. Multi-mode routing is deferred to the StateMachine integration.

CancelGrace measures terminal confirmation processed by the Worker. A matching
terminal event in the currently delivered input wins over a due grace timer.
An event still queued in Inbox may miss that window during backlog or slow
archive publication; the Worker then starts a fresh downstream context. This is
a conservative loss of conversation continuity, not data corruption. No Inbox
scan or cross-store confirmation check is added at the timer boundary.
