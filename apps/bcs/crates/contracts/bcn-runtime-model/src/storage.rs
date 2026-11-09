use serde::{Deserialize, Serialize};
use crate::*;

pub const MAX_READ_KEYS: usize = 100;
pub const MAX_BATCH_CONDITIONS: usize = 100;
pub const MAX_BATCH_MUTATIONS: usize = 100;
pub const MAX_SCAN_LIMIT: usize = 100;
pub const MAX_KEY_BYTES: usize = 256;
pub const MAX_VALUE_BYTES: usize = 1_048_576;
pub const MAX_BATCH_BYTES: usize = 8_388_608;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Revision(pub u64);
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct VersionedValue { pub revision: Revision, pub value: Vec<u8> }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct KeyValue { pub key: String, pub entry: VersionedValue }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Expected { Absent, Revision(Revision) }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Condition { pub key: String, pub expected: Expected }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Mutation { Put { key: String, value: Vec<u8> }, Delete { key: String } }
impl Mutation {
    pub fn key(&self) -> &str { match self { Self::Put { key, .. } | Self::Delete { key } => key } }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AtomicBatch { pub conditions: Vec<Condition>, pub mutations: Vec<Mutation> }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MutationRevision { pub key: String, pub revision: Revision }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TransactionReceipt { pub revisions: Vec<MutationRevision> }
/// Cursor binds traversal to the exact scope, partition and prefix. Not a snapshot token.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScanCursor { pub scope: Scope, pub partition: String, pub prefix: String, pub after_key: String }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScanPage { pub entries: Vec<KeyValue>, pub next_cursor: Option<ScanCursor> }

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NodeIdentity { pub scope: Scope, pub node_id: String, pub incarnation: String }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Liveness { Alive, Dead, Unknown }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NodeObservation { pub node: NodeIdentity, pub liveness: Liveness, pub draining: bool }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ShardId { pub scope: Scope, pub number: u16 }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnershipFence { pub shard: ShardId, pub owner: NodeIdentity, pub epoch: u64 }
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionCheckpoint {
    pub spec: SessionSpec,
    pub revision: Revision,
    pub fence: OwnershipFence,
    pub consumed_input: u64,
    pub state: RunnerState,
    pub pending_effects: Vec<EffectIntent>,
    pub pending_events: Vec<DomainEvent>,
    pub deadlines: Vec<Deadline>,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SessionCommit {
    pub session_id: SessionId,
    pub fence: OwnershipFence,
    pub expected_revision: Revision,
    pub consumed_input: u64,
    pub transition: Transition,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct InboxEntry { pub position: u64, pub input: SessionInput }
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AdmissionRequest {
    pub admission_id: AdmissionId,
    pub caller: CallerContext,
    pub request_digest: String,
    pub spec: SessionSpec,
    pub task: TaskView,
    pub input: SessionInput,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AdmissionReceipt {
    pub admission_id: AdmissionId,
    pub caller: CallerContext,
    pub request_digest: String,
    pub task: TaskView,
    pub input_position: u64,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Command { pub command_id: CommandId, pub session_id: SessionId, pub effect: EffectIntent }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DispatchPhase { Prepared, MayHaveSent, Accepted, ResultRecorded, Unknown }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DispatchFence { pub dispatch_id: DispatchId, pub owner: NodeIdentity, pub epoch: u64 }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DispatchRecord {
    pub fence: DispatchFence,
    pub revision: Revision,
    pub request: AgentSendRequest,
    pub phase: DispatchPhase,
    pub handle: Option<ExecutionHandle>,
    pub receipt: Option<AgentEvent>,
    pub updated_at_ms: u64,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EventSnapshot { pub task: TaskView, pub cursor: EventCursor }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EventPage { pub snapshot: EventSnapshot, pub events: Vec<TaskEvent>, pub cursor_expired: bool }

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RuntimeDefaults {
    pub shards: u16, pub heartbeat_ms: u64, pub heartbeat_ttl_ms: u64,
    pub ownership_inspection_ms: u64, pub inbox_batch: u32, pub recovery_concurrency: u32,
    pub dispatch_supervision_ms: u64, pub progress_ms: u64, pub stall_ms: u64,
    pub token_batch_ms: u64, pub session_pending_tasks: u32, pub graph_nodes: u32,
    pub graph_agents: u32, pub poison_takeovers: u32, pub drain_ms: u64,
}
impl Default for RuntimeDefaults {
    fn default() -> Self {
        Self { shards: 1024, heartbeat_ms: 1000, heartbeat_ttl_ms: 3000,
            ownership_inspection_ms: 500, inbox_batch: 100, recovery_concurrency: 64,
            dispatch_supervision_ms: 1000, progress_ms: 5000, stall_ms: 15000,
            token_batch_ms: 200, session_pending_tasks: 16, graph_nodes: 32,
            graph_agents: 5, poison_takeovers: 3, drain_ms: 30000 }
    }
}
