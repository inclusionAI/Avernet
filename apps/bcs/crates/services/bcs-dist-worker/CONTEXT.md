# Context boundary

- Role: public generic Worker runtime, processor, Dispatch supervisor and output
  read facade.
- Consumes: membership/shard/session/Inbox/Dispatch/TaskFeed/Result/Blob/control
  repository ports, shared scan budget, pure Runner/projection, group directory,
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
- Strict REST starts: `lookup_group_start` is a read of the scoped message
  registry, not a second admission entry. Only an accepted row/license returns
  a receipt. The Gateway compares the original intent before considering a new
  start; an unpublished group can still serve a currently authorized replay.
