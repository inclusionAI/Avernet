//! A2A (Agent2Agent) protocol v1.0 wire types.
//!
//! This crate is the single wire contract for the A2A JSON-RPC binding used by
//! bcs-bridge (A2A Server) and the BCS `a2a` downlink (A2A Client). It is not
//! related to `bcs_protocol::a2a`, which models BCS-internal bot-to-bot chat.
//!
//! Source of truth: `specification/a2a.proto` (package `lf.a2a.v1`) of the
//! A2A specification. JSON uses ProtoJSON conventions: camelCase field names,
//! enum values as their full proto names (`ROLE_USER`, `TASK_STATE_WORKING`),
//! and `oneof` members as mutually exclusive object keys (no `kind` field).

pub mod card;
pub mod jsonrpc;
pub mod types;

pub use card::*;
pub use jsonrpc::*;
pub use types::*;

/// Protocol version this crate implements (`Major.Minor`, spec §3.6).
pub const A2A_PROTOCOL_VERSION: &str = "1.0";
/// Service parameter header carrying the requested protocol version.
pub const A2A_VERSION_HEADER: &str = "A2A-Version";
/// Service parameter header listing requested extension URIs (comma separated).
pub const A2A_EXTENSIONS_HEADER: &str = "A2A-Extensions";
/// Well-known AgentCard path relative to an agent base URL (spec §8.2).
pub const AGENT_CARD_WELL_KNOWN_PATH: &str = ".well-known/agent-card.json";
/// Content type for push notification payloads and the HTTP+JSON binding.
pub const A2A_JSON_CONTENT_TYPE: &str = "application/a2a+json";
/// Binding name for [`AgentInterface::protocol_binding`].
pub const PROTOCOL_BINDING_JSONRPC: &str = "JSONRPC";

/// JSON-RPC method names (spec §9.1).
pub mod methods {
    pub const SEND_MESSAGE: &str = "SendMessage";
    pub const SEND_STREAMING_MESSAGE: &str = "SendStreamingMessage";
    pub const GET_TASK: &str = "GetTask";
    pub const LIST_TASKS: &str = "ListTasks";
    pub const CANCEL_TASK: &str = "CancelTask";
    pub const SUBSCRIBE_TO_TASK: &str = "SubscribeToTask";
    pub const CREATE_PUSH_CONFIG: &str = "CreateTaskPushNotificationConfig";
    pub const GET_PUSH_CONFIG: &str = "GetTaskPushNotificationConfig";
    pub const LIST_PUSH_CONFIGS: &str = "ListTaskPushNotificationConfigs";
    pub const DELETE_PUSH_CONFIG: &str = "DeleteTaskPushNotificationConfig";
    pub const GET_EXTENDED_AGENT_CARD: &str = "GetExtendedAgentCard";
}

/// Avernet extension URIs. The vocabulary is shared with the Python engine
/// adapter (`engine/adapter/.../transport/a2a/card.py`); `progress` is added
/// here for the `metadata.kind` progress-event vocabulary.
pub mod extensions {
    pub const BASE: &str = "https://avernet.local/a2a/ext";
    pub const INJECT: &str = "https://avernet.local/a2a/ext/inject/v1";
    pub const REPLAY: &str = "https://avernet.local/a2a/ext/replay/v1";
    pub const CANCEL: &str = "https://avernet.local/a2a/ext/cancel/v1";
    pub const PROGRESS: &str = "https://avernet.local/a2a/ext/progress/v1";
}

/// `metadata` keys used by the Avernet extensions. Kept identical to the
/// Python engine adapter's `wire.py` where the concept overlaps.
pub mod meta {
    /// Monotonic per-task event sequence; equals the SSE `id`.
    pub const SEQ: &str = "seq";
    /// Progress kind: `tool`, `thinking`, `lifecycle`, `commentary`,
    /// `commentary.delta`, `interaction.requested`, `interaction.resolved`.
    pub const KIND: &str = "kind";
    pub const INTERACTION_ID: &str = "interactionId";
    pub const INTERACTION_KIND: &str = "interactionKind";
    /// Lossless engine-side payload for extension-aware consumers.
    pub const ENGINE_FRAME: &str = "engine_frame";
    pub const ITEM_ID: &str = "itemId";
    pub const DELTA: &str = "delta";
    pub const ERROR_CODE: &str = "errorCode";
    pub const ERROR_MESSAGE: &str = "errorMessage";
    /// `Task.metadata` list of `{interactionId, interactionKind, engine_frame}`
    /// awaiting an answer, for snapshot (poll / resume) consumers.
    pub const PENDING_INTERACTIONS: &str = "pendingInteractions";
}
