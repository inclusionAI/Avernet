//! Business persistence contracts. Errors propagate; backends must not fake atomicity.
use async_trait::async_trait;
use bcn_runtime_model::*;

#[async_trait]
pub trait OwnershipPort: Send + Sync {
    /// Compare the observed owner atomically; epoch increases even when a node name is reused.
    async fn claim_shard(&self, shard: ShardId, candidate: NodeIdentity, expected: Option<OwnershipFence>) -> RuntimeResult<OwnershipFence>;
    async fn transfer_shard(&self, current: OwnershipFence, next: NodeIdentity) -> RuntimeResult<OwnershipFence>;
    async fn read_owner(&self, shard: &ShardId) -> RuntimeResult<Option<OwnershipFence>>;
}
#[async_trait]
pub trait SessionStore: Send + Sync {
    async fn activate(&self, spec: SessionSpec, state: RunnerState, fence: OwnershipFence) -> RuntimeResult<SessionCheckpoint>;
    async fn load_spec(&self, session_id: &SessionId) -> RuntimeResult<Option<SessionSpec>>;
    /// Remove wake only when no inbox, timers, current/queued task or outbox remains.
    async fn deactivate(&self, session_id: &SessionId, fence: &OwnershipFence, expected: Revision) -> RuntimeResult<()>;
    async fn load(&self, session_id: &SessionId) -> RuntimeResult<Option<SessionCheckpoint>>;
    /// One transaction checks current ownership AND revision, writes state/input/effects/events/timers.
    async fn commit(&self, commit: SessionCommit) -> RuntimeResult<SessionCheckpoint>;
    async fn scan_active(&self, shard: &ShardId, cursor: Option<String>, limit: u32) -> RuntimeResult<Page<SessionId>>;
}
#[async_trait]
pub trait AdmissionStore: Send + Sync {
    /// Receipt + inbox + wake must be durable before success; digest mismatch is Conflict.
    async fn admit(&self, request: AdmissionRequest) -> RuntimeResult<AdmissionReceipt>;
    async fn get_receipt(&self, id: &AdmissionId) -> RuntimeResult<Option<AdmissionReceipt>>;
    async fn repair_pending(&self, scope: &Scope, cursor: Option<String>, limit: u32) -> RuntimeResult<Page<AdmissionReceipt>>;
}
#[async_trait]
pub trait InboxPort: Send + Sync {
    /// Trusted internal boundary, never a protocol input API. Input append and wake
    /// are atomic; repeated event_id is idempotent. Submit must use AdmissionStore.
    /// ResultSaved may only carry an actual authoritative ResultStore return; verify
    /// its exact committed intent/session and atomically persist the saved receipt.
    /// Replaying that committed result across ownership changes is safe.
    async fn append_and_wake(&self, session_id: &SessionId, input: SessionInput) -> RuntimeResult<u64>;
    async fn read_batch(&self, session_id: &SessionId, after: u64, limit: u32) -> RuntimeResult<Vec<InboxEntry>>;
    /// Only reclaim through a checkpoint's committed cursor, under its current fence.
    async fn advance(&self, session_id: &SessionId, fence: &OwnershipFence, committed_through: u64) -> RuntimeResult<()>;
}
#[async_trait]
pub trait CommandBus: Send + Sync {
    /// Load canonical committed effect, atomically enqueue and acknowledge only its identity.
    async fn publish(&self, fence: &OwnershipFence, effect: CommittedEffectRef) -> RuntimeResult<Command>;
    /// Advance next_cursor even on an empty eligible page, then cycle at end.
    async fn receive(&self, scope: &Scope, consumer: &NodeIdentity, cursor: Option<String>, limit: u32) -> RuntimeResult<Page<Command>>;
    /// Delivery ACK is not execution completion. Connector commands require a durable
    /// dispatch claim before ACK; active dispatch enumeration then owns recovery.
    async fn ack(&self, command_id: &CommandId, consumer: &NodeIdentity) -> RuntimeResult<()>;
}
#[async_trait]
pub trait DispatchStore: Send + Sync {
    async fn read(&self, dispatch_id: &DispatchId) -> RuntimeResult<Option<DispatchRecord>>;
    /// Active operation index survives delivery ACK; bounded restart supervision.
    async fn scan_active(&self, scope: &Scope, cursor: Option<String>, limit: u32) -> RuntimeResult<Page<DispatchRecord>>;
    /// Persist the complete tagged operation. Validate scope/target/binding consistency;
    /// dispatch and operation IDs are distinct for send, continuation and cancellation.
    /// Reclaim/recovery must return the same immutable operation, never a fabricated send.
    async fn claim(&self, command_id: &CommandId, owner: NodeIdentity) -> RuntimeResult<DispatchRecord>;
    /// Commit MAY_HAVE_SENT under this fence before external I/O. PREPARED alone is unsent proof.
    async fn mark_may_have_sent(&self, fence: &DispatchFence, expected: Revision) -> RuntimeResult<DispatchRecord>;
    async fn save_handle(&self, fence: &DispatchFence, expected: Revision, handle: ExecutionHandle) -> RuntimeResult<DispatchRecord>;
    /// Atomically fence receipt and append correlated inbox input; stale connectors cannot publish hard results.
    /// Receipt kind must match the operation; Cancellation preserves Unknown/AlreadyTerminal/Confirmed.
    /// The correlated Cancellation input must carry the same dispatch/operation IDs and outcome.
    async fn save_receipt(&self, fence: &DispatchFence, expected: Revision, receipt: DispatchReceipt,
        session_id: &SessionId, input: SessionInput) -> RuntimeResult<DispatchRecord>;
    async fn takeover(&self, current: &DispatchFence, next: NodeIdentity) -> RuntimeResult<DispatchRecord>;
}
#[async_trait]
pub trait TaskEventStore: Send + Sync {
    /// Publish the authoritative pending event committed with the referenced session.
    /// Atomically check CURRENT ownership (including incarnation/epoch) and event/session scope;
    /// load the committed payload, append it and persist publication receipt before clearing pending.
    /// Unknown/uncommitted events fail closed. Retry by event ID returns the same cursor after
    /// checking ownership; stale owners return OwnershipLost without appending or acknowledging.
    /// Use the current task stream cursor, never ownership epoch as a connector projection epoch.
    /// No DispatchFence is required, including HumanInput before any agent dispatch.
    async fn publish_hard_event(&self, fence: &OwnershipFence, event: CommittedEventRef) -> RuntimeResult<EventCursor>;
    /// Connector deltas only: fence against connector epoch and persisted Reset boundary.
    /// event.cursor is the expected current task cursor; the store allocates the next.
    /// Reject Domain/Reset here; committed Worker facts use publish_hard_event instead.
    async fn append(&self, fence: &DispatchFence, event: TaskEvent) -> RuntimeResult<EventCursor>;
    /// Atomically persist this canonical node's projection and the aggregate task Reset.
    /// expected_cursor fences the task-wide stream; per-dispatch epochs are independent.
    /// Retry by event identity is idempotent. Reset must precede deltas after takeover.
    async fn publish_reset(&self, fence: &DispatchFence, snapshot: ConnectorProjection, event_id: EventId) -> RuntimeResult<EventCursor>;
    /// Snapshot/cursor and events must cover concurrent subscription without a gap.
    async fn read_after(&self, task_id: &TaskId, cursor: Option<EventCursor>, limit: u32) -> RuntimeResult<EventPage>;
    async fn read_snapshot(&self, task_id: &TaskId) -> RuntimeResult<Option<EventSnapshot>>;
}
#[async_trait]
pub trait ResultStore: Send + Sync {
    /// Stable result_id, immutable payload. Return the authoritative existing result on retry.
    async fn put_if_absent(&self, intent: ResultIntent) -> RuntimeResult<SavedResult>;
    async fn get(&self, result_id: &ResultId) -> RuntimeResult<Option<SavedResult>>;
    async fn list_tasks(&self, ctx: &CallerContext, query: TaskQuery) -> RuntimeResult<Page<TaskView>>;
    async fn read_group_records(&self, task_id: &TaskId, cursor: Option<String>, limit: u32) -> RuntimeResult<Page<SavedResult>>;
}

/// Admission-owned read authority includes accepted, nonterminal tasks. Result payloads
/// cannot establish principal/target authorization or invent admission metadata.
#[async_trait]
pub trait TaskQueryStore: Send + Sync {
    async fn get_task(&self, ctx: &CallerContext, task_id: &TaskId) -> RuntimeResult<TaskView>;
    async fn list_tasks(&self, ctx: &CallerContext, query: TaskQuery) -> RuntimeResult<Page<TaskView>>;
}
