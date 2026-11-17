# Context boundary

- Role: public Worker coordination engine implementing `DistRuntimeControlPort`.
- Consumes: MembershipPort, ShardLeasePort, SessionStorePort, InboxConsumerPort,
  InboxSchedulingPort, DistWorkerPeerPort, and an injected checkpoint initializer.
- Owns: readiness, HRW priority, bounded scans, local session lifecycle accounting,
  incoming/outgoing handoff resolution, and shutdown budgets.
- Does not own: storage commands, transport authentication, workflow algorithms,
  dispatch execution, or persisted-effect execution.
- Callers: the runtime application facade and the embedding Worker processor.
- Changes to these lifecycle contracts require the failure-path integration tests.
