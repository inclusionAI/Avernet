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


`ExecutionSnapshot.observation_error` and `AgentSendResponse::Message.observation_error`
are optional, serde-default local projection errors, separate from a remote task
execution error. Old messages/snapshots without the fields still decode; None is
not serialized. A snapshot retains actual remote status and handle. Direct Message
responses remain terminal, with only an actual optional server context and no
fabricated task identity. Consumers must persist a returned handle before acting
on observation failure, retain previously known output if failed projection
produced none, and distinguish live uncertainty from confirmed termination.

`ExecutionSnapshot.input_prompt` optionally carries an InputRequired question
separately from artifact `output`. A present empty list explicitly means no
question; only legacy snapshots with None use output as the prompt fallback.
The field defaults to None and is omitted when absent. This is a neutral runtime
contract, not an additional A2A or Provider wire field.

## Public question and live-output projections

TaskView.active_interactions is a serde-default current list of scoped IDs and
question strings, separate from output artifacts. InputRequired upserts its ID;
InteractionResolved removes only that ID; terminal publication clears the list.
NodeExecution.interaction_prompt is serde-default optional checkpoint data, so
current questions participate in the existing cumulative checkpoint budget.
HumanReply, cancellation, timeout and failure consume/clear the matching question
through committed events. No unbounded event-history reconstruction is needed.

SessionSpec.live_output_nodes is an optional, frozen list of node IDs in this
Session's definition. None preserves previous all-node token projection behavior.
Some selects distinct final BotTask nodes; Some([]) hides all live tokens while
retaining status/questions/results. Direct does not accept a graph selector.
The new bootstrap selects the unique final_output BotTask, or an empty list for
a terminal HumanInput. Private/Shared graph visibility fields are not repurposed.
Intermediate saved node results remain in ResultStore/read_group_records; this
MVP does not merge them into additional public A2A Task artifacts. The authoritative
terminal Task artifact remains the final summary result.

These additive defaults preserve old serialized snapshots/specs/checkpoints.
Rust construction sites must initialize the new fields. New InteractionResolved
consumers and the selection-aware repository must deploy together in an isolated,
explicitly enabled namespace; no legacy in-flight migration is provided.

TaskPage separates exact collection total_size from its bounded items and opaque
next_cursor. Runtime task lists order by updated_at_ms descending; that same time
is exposed as A2A status.timestamp. Collection mutations explicitly invalidate
page cursors rather than retaining unbounded snapshot history. Generic Page<T>
remains unchanged for other repository traversals.

SubmitOutcome carries the normal TaskView plus optional reply_interaction_id from
durable acceptance. It is application evidence for the submitted reply, not a new
wire field or a mutable task attribute; current unresolved questions remain in
TaskView.active_interactions.
