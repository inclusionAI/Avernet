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
    /// Input append and wake are atomic; repeated event_id is idempotent.
    async fn append_and_wake(&self, session_id: &SessionId, input: SessionInput) -> RuntimeResult<u64>;
    async fn read_batch(&self, session_id: &SessionId, after: u64, limit: u32) -> RuntimeResult<Vec<InboxEntry>>;
    /// Only reclaim through a checkpoint's committed cursor, under its current fence.
    async fn advance(&self, session_id: &SessionId, fence: &OwnershipFence, committed_through: u64) -> RuntimeResult<()>;
}
#[async_trait]
pub trait CommandBus: Send + Sync {
    async fn publish(&self, command: Command) -> RuntimeResult<()>;
    async fn receive(&self, scope: &Scope, consumer: &NodeIdentity, limit: u32) -> RuntimeResult<Vec<Command>>;
    /// Delivery ACK is not execution completion.
    async fn ack(&self, command_id: &CommandId, consumer: &NodeIdentity) -> RuntimeResult<()>;
}
#[async_trait]
pub trait DispatchStore: Send + Sync {
    async fn claim(&self, request: AgentSendRequest, owner: NodeIdentity) -> RuntimeResult<DispatchRecord>;
    /// Commit MAY_HAVE_SENT under this fence before external I/O. PREPARED alone is unsent proof.
    async fn mark_may_have_sent(&self, fence: &DispatchFence, expected: Revision) -> RuntimeResult<DispatchRecord>;
    async fn save_handle(&self, fence: &DispatchFence, expected: Revision, handle: ExecutionHandle) -> RuntimeResult<DispatchRecord>;
    /// Atomically fence receipt and append correlated inbox input; stale connectors cannot publish hard results.
    async fn save_receipt(&self, fence: &DispatchFence, expected: Revision, receipt: AgentEvent,
        session_id: &SessionId, input: SessionInput) -> RuntimeResult<DispatchRecord>;
    async fn takeover(&self, current: &DispatchFence, next: NodeIdentity) -> RuntimeResult<DispatchRecord>;
}
#[async_trait]
pub trait TaskEventStore: Send + Sync {
    /// Fenced against connector epoch and the persisted Reset boundary.
    async fn append(&self, fence: &DispatchFence, event: TaskEvent) -> RuntimeResult<EventCursor>;
    /// Persist full snapshot before ordered Reset; retry continues a pending Reset before deltas.
    async fn publish_reset(&self, fence: &DispatchFence, snapshot: EventSnapshot, event_id: EventId) -> RuntimeResult<EventCursor>;
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
