# bcn-runtime-model

Neutral versioned model contracts.

See [BCN runtime contracts v1](../../../../../docs/architecture/bcn-runtime.md)
for lifecycle, limits, compatibility, isolation and composition boundaries.

Run focused tests with `cargo test -p bcn-runtime-model` from `apps/bcs`.

Repository integration adds CommittedEffectRef, deterministic typed
`effect_command_id`, logical `session_shard`, admission TaskMetadata,
ConnectorProjection and required DispatchContext. Recovery records retain
command/session/task/node routing without parsing hashed identifiers or retaining
a live command map. Task-global event epochs differ from dispatch ownership
epochs. Projection inputs cannot supply status or admission metadata. These are
pre-release v1 corrections; payloads missing required dispatch context must be
explicitly migrated/discarded before writers are enabled. No legacy runtime
payload or session is changed.

Application compatibility: SubmitRequest.interaction_id and
AgentSendResponse::Message.handle are optional serde-default fields (omitted
when None); an actual remote context-only handle is valid. TimerFired.output is
serde-default empty, frozen into the durable timer before reduction. Existing
Failed retains its original shape; FailedWithOutput represents bounded partial
output without inventing a handle. Internal TaskFailure is a Worker-only input
for a bounded result-barrier failure of an already accepted input. External
protocol clients must not construct SessionInput. CommandConsumerKind routes
before leasing; SessionRecovery is durable poison metadata, not process memory.
Older readers do not understand new tagged variants; deploy new contracts,
reducers, stores and applications together in the explicitly enabled namespace.
No old runtime session migration or permissive decoding fallback is provided.

`AgentEvent::ObservationFailed { error, output }` is distinct from remote
Failed/FailedWithOutput: local observation stopped without proving remote
termination. Its durable receipt drives the application's TaskFailure path,
retaining the saved-result barrier, known-handle cancellation and uncertain
Direct context blocking. Existing failure wire shapes are unchanged.
