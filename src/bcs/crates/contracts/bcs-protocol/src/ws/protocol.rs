//! BCS streaming protocol types.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;

// ---------------------------------------------------------------------------
// Protocol versioning
// ---------------------------------------------------------------------------

/// Current protocol version supported by this build.
/// v1: Initial protocol. session_context as structured field, engine formats context header.
/// v2: BCS prepends Group Context header to message content automatically.
pub const BCS_PROTOCOL_VERSION: u32 = 2;
/// Minimum protocol version still accepted.
pub const BCS_MIN_SUPPORTED_VERSION: u32 = 1;

// ---------------------------------------------------------------------------
// Frame layer (mirrors OpenClaw frames.py)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ErrorShape {
    pub code: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub details: Option<Value>,
    #[serde(default)]
    pub retryable: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub retry_after_ms: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RequestFrame {
    /// Always "req" (set by BcsFrame tag, not serialized directly)
    #[serde(rename = "type", default, skip_serializing)]
    pub frame_type: String,
    pub id: String,
    pub method: String,
    /// Method parameters (optional for some methods).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub params: Option<Value>,
}

impl RequestFrame {
    pub fn new(id: impl Into<String>, method: impl Into<String>, params: Option<Value>) -> Self {
        Self {
            frame_type: "req".into(),
            id: id.into(),
            method: method.into(),
            params,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResponseFrame {
    /// Always "res" (set by BcsFrame tag, not serialized directly)
    #[serde(rename = "type", default, skip_serializing)]
    pub frame_type: String,
    pub id: String,
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub payload: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<ErrorShape>,
}

impl ResponseFrame {
    pub fn ok(id: impl Into<String>, payload: Value) -> Self {
        Self {
            frame_type: "res".into(),
            id: id.into(),
            ok: true,
            payload: Some(payload),
            error: None,
        }
    }

    pub fn err(id: impl Into<String>, error: ErrorShape) -> Self {
        Self {
            frame_type: "res".into(),
            id: id.into(),
            ok: false,
            payload: None,
            error: Some(error),
        }
    }

    /// Create a success response (alias for `ok`).
    pub fn success(id: impl Into<String>, payload: Value) -> Self {
        Self::ok(id, payload)
    }

    /// Create an error response with code and message.
    pub fn error(id: impl Into<String>, code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            frame_type: "res".into(),
            id: id.into(),
            ok: false,
            payload: None,
            error: Some(ErrorShape {
                code: code.into(),
                message: message.into(),
                details: None,
                retryable: false,
                retry_after_ms: None,
            }),
        }
    }

    /// Create an error response with details.
    pub fn error_with_details(
        id: impl Into<String>,
        code: impl Into<String>,
        message: impl Into<String>,
        details: Value,
    ) -> Self {
        Self {
            frame_type: "res".into(),
            id: id.into(),
            ok: false,
            payload: None,
            error: Some(ErrorShape {
                code: code.into(),
                message: message.into(),
                details: Some(details),
                retryable: false,
                retry_after_ms: None,
            }),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EventFrame {
    /// Always "event" (set by BcsFrame tag, not serialized directly)
    #[serde(rename = "type", default, skip_serializing)]
    pub frame_type: String,
    pub event: String,
    /// Event payload (optional for some events).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub payload: Option<Value>,
    /// Sequence number for ordering (optional).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seq: Option<u64>,
    /// State version for synchronization (optional).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub state_version: Option<u64>,
}

impl EventFrame {
    pub fn new(event: impl Into<String>, payload: Option<Value>, seq: Option<u64>) -> Self {
        Self {
            frame_type: "event".into(),
            event: event.into(),
            payload,
            seq,
            state_version: None,
        }
    }
}

/// Top-level streaming message, one of the three frame types.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum BcsFrame {
    #[serde(rename = "req")]
    Request(RequestFrame),
    #[serde(rename = "res")]
    Response(ResponseFrame),
    #[serde(rename = "event")]
    Event(EventFrame),
}

/// Type alias for gateway usage (same as BcsFrame).
pub type GatewayFrame = BcsFrame;

// ---------------------------------------------------------------------------
// Error Codes
// ---------------------------------------------------------------------------

/// Error code constants for streaming protocol.
pub mod error_codes {
    /// Invalid request format or parameters.
    pub const INVALID_REQUEST: &str = "invalid_request";
    /// Authentication failed or token invalid.
    pub const UNAUTHORIZED: &str = "unauthorized";
    /// Resource not found.
    pub const NOT_FOUND: &str = "not_found";
    /// Service unavailable.
    pub const UNAVAILABLE: &str = "unavailable";
    /// Unknown method.
    pub const UNKNOWN_METHOD: &str = "unknown_method";
    /// Internal server error.
    pub const INTERNAL_ERROR: &str = "internal_error";
}

// ---------------------------------------------------------------------------
// Bot registration & heartbeat (Bot → BCS)
// ---------------------------------------------------------------------------

/// Bot capabilities for streaming registration.
/// Note: This is a simplified version for the streaming protocol.
/// Use the main `BotCapabilities` type from lib.rs for HTTP API.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WsBotCapabilities {
    pub summary: String,
    #[serde(default)]
    pub domains: Vec<String>,
    #[serde(default)]
    pub skills: Vec<String>,
    #[serde(default)]
    pub scopes: Vec<String>,
}

/// Parameters for bot.connect request (initial connection handshake).
/// bot_uuid is NOT required - it can be derived from the token.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotConnectParams {
    /// Optional token from previous session for reconnection.
    /// If valid, returns is_new: false and the bot_uuid.
    /// If empty/invalid, returns is_new: true and a new token.
    #[serde(default)]
    pub token: Option<String>,

    /// Optional preconfigured bot_id from ~/.credentials.
    /// Format: "{BOT_ID}:{OWNER_ID}" (e.g., "example_bot:example_owner").
    /// If provided and not already connected, BCS will use this bot_id.
    /// If already connected, returns bot_id_conflict error.
    #[serde(default)]
    pub bot_id: Option<String>,

    /// Protocol version requested by the client.
    /// Omitted or None → server defaults to BCS_PROTOCOL_VERSION.
    #[serde(default)]
    pub protocol_version: Option<u32>,

    /// Optional client source hint. `plugin` means the bot runtime has native
    /// BCS coordination tools available.
    #[serde(default)]
    pub client_kind: Option<String>,
}

/// Protocol deprecation notice returned in bot.connect response.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProtocolDeprecation {
    pub message: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sunset_date: Option<String>,
}

fn default_protocol_version() -> u32 { BCS_PROTOCOL_VERSION }

/// Response for bot.connect request.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotConnectResponse {
    /// Whether this is a new bot (needs onboarding) or reconnection.
    pub is_new: bool,
    /// Session token for this connection. Client should persist this.
    pub token: String,
    /// Bot UUID (for reconnection, this is the existing bot_uuid).
    pub bot_uuid: String,
    /// Negotiated protocol version for this session.
    #[serde(default = "default_protocol_version")]
    pub protocol_version: u32,
    /// Minimum protocol version the server still supports.
    #[serde(default = "default_protocol_version")]
    pub min_supported_version: u32,
    /// Deprecation notice when the negotiated version is scheduled for removal.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deprecation: Option<ProtocolDeprecation>,
    /// Environment variables the engine should set for child processes (e.g., bcs-cli).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub env: Option<HashMap<String, String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum BotStatus {
    Idle,
    Busy,
    Error,
}

/// Parameters for bot.status update.
/// All fields are optional — an empty `{}` is a valid heartbeat.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotStatusParams {
    #[serde(default)]
    pub status: Option<BotStatus>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dynamic_summary: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub load: Option<f32>,
}

// ---------------------------------------------------------------------------
// Onboard (BCS ↔ Bot)
// ---------------------------------------------------------------------------

/// Parameters for onboard.request (BCS → Bot).
///
/// BCS sends this to a new bot that just connected and needs to provide its info.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OnboardRequestParams {
    /// Server-assigned bot_uuid for the bot to know its identity.
    pub bot_uuid: String,
    /// Session token for HTTP fallback if needed.
    pub token: String,
}

/// Response payload for onboard.response (Bot → BCS).
///
/// Bot provides its capabilities when responding to onboard.request.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OnboardResponsePayload {
    /// Bot display name.
    pub name: String,
    /// Capability summary.
    #[serde(default)]
    pub summary: Option<String>,
    /// Domains this bot covers (e.g., ["development", "database"]).
    #[serde(default)]
    pub domains: Vec<String>,
    /// Skills this bot has (e.g., ["code_review", "deployment"]).
    #[serde(default)]
    pub skills: Vec<String>,
    /// Access scopes this bot has (e.g., ["production", "staging"]).
    #[serde(default)]
    pub scopes: Vec<String>,
}

// ---------------------------------------------------------------------------
// Message down-send (BCS → Bot)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContentBlock {
    #[serde(rename = "type")]
    pub block_type: String, // "text" | "image"
    #[serde(skip_serializing_if = "Option::is_none")]
    pub text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
}

impl ContentBlock {
    pub fn text(text: impl Into<String>) -> Self {
        Self {
            block_type: "text".into(),
            text: Some(text.into()),
            url: None,
        }
    }

    pub fn image(url: impl Into<String>) -> Self {
        Self {
            block_type: "image".into(),
            text: None,
            url: Some(url.into()),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MessageContent {
    pub role: String,
    pub content: Vec<ContentBlock>,
    pub timestamp: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ChannelSource {
    WebUi,
    DingTalk,
    Api,
}

/// BCS extension: identifies the originating channel and end-user.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChannelInfo {
    pub source: ChannelSource,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub user_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub actor_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub actor_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub thread_id: Option<String>,
}

/// Group context injected by BCS when routing messages to bots.
///
/// This context helps bots understand:
/// - Which group they're in
/// - Who sent the message
/// - Whether they should respond
///
/// BCS uses this context to enable "originator-first" protocol where
/// the bot who created the group is the default coordinator.
///
/// Key principle: ALL messages are broadcast to ALL participants.
/// @mentions indicate who should respond, but everyone sees all messages.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupContext {
    // Basic session info
    /// Unique group/session identifier.
    pub session_id: String,
    /// All participant bot IDs in this session.
    #[serde(default)]
    pub participants: Vec<String>,
    /// Current recipient bot UUID.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recipient: Option<String>,
    /// Current recipient display name.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recipient_name: Option<String>,
    /// Current recipient role in this group, e.g. driver/consultant/observer.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recipient_role: Option<String>,
    /// Delivery type for this recipient: send or inject.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delivery_type: Option<String>,

    // Response control fields for bot decision-making
    /// Who created this group (the originator/coordinator).
    /// Bots should check if they are the originator to decide whether to respond to broadcasts.
    pub originator: String,
    /// Who sent this message.
    pub from: String,
    /// Bot UUID of the sender (raw identifier, unlike `from` which may be a display name).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub from_bot_id: Option<String>,
    /// Owner (created_by) of the sender bot. None when sender is human (from starts with "human_").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub from_bot_owner: Option<String>,
    /// Was the current bot @mentioned in this message?
    #[serde(default)]
    pub you_are_mentioned: bool,
    /// Did the current bot send this message?
    #[serde(default)]
    pub is_sender: bool,
    /// All @mentions in the message.
    #[serde(default)]
    pub mentions: Vec<String>,
    /// Structured response directive (preferred over you_are_mentioned for new bots).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub response_directive: Option<ResponseDirective>,

    // Message content
    /// The message content.
    pub message: String,

    /// Group routing mode (from RoutingPolicy).
    /// Values: "structured", "mention", "hybrid".
    /// Absent/null for groups with no explicit policy (defaults to hybrid).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub routing_mode: Option<String>,

    /// Group type: "task" for task groups, absent/null for normal groups.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub group_type: Option<String>,
}

/// Delivery intent used when building protocol group context.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GroupContextDeliveryType {
    /// The recipient should process and respond to the message.
    Send,
    /// The recipient should observe the message silently.
    Inject,
}

/// Protocol-owned participant snapshot for group context construction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupContextParticipant {
    pub id: String,
    pub name: Option<String>,
    pub role: Option<String>,
    pub is_bot: bool,
}

/// Protocol-owned group snapshot for group context construction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupContextInput {
    pub session_id: String,
    pub driver_bot: String,
    pub originator: String,
    pub participants: Vec<GroupContextParticipant>,
    /// BCS session layer id (`{group_id}:{8_hex}`). None when not pinned to a session.
    pub bcs_session_id: Option<String>,
}

impl GroupContext {
    /// Format a human-readable context header for LLM agents.
    /// Used by BCS (protocol v2+) to prepend group context to message content.
    pub fn format_header(&self) -> String {
        let mut lines = vec!["[BCS Group Context]".to_string()];
        lines.push(format!("- 参与者: {}", self.participants.join(", ")));
        if let Some(recipient) = self.format_recipient_identity() {
            lines.push(format!("- 你是: {}", recipient));
        }
        if let Some(role) = &self.recipient_role {
            lines.push(format!("- 你的角色: {}", role));
        }
        if let Some(delivery_type) = &self.delivery_type {
            let directive = match delivery_type.as_str() {
                "send" => "需要响应",
                "inject" => "静默观察，不要主动回复",
                _ => "遵循 session_context.response_directive",
            };
            lines.push(format!("- 当前投递: chat.{} ({})", delivery_type, directive));
        }
        lines.push(format!("- 发起方(driver): {}", self.originator));
        lines.push(format!("- 消息来自: {}", self.from));
        if let Some(bot_id) = &self.from_bot_id {
            lines.push(format!("- 发送者bot_id: {}", bot_id));
        }
        if let Some(owner) = &self.from_bot_owner {
            lines.push(format!("- 消息来自owner: {}", owner));
        }
        lines.join("\n")
    }

    fn format_recipient_identity(&self) -> Option<String> {
        match (&self.recipient_name, &self.recipient) {
            (Some(name), Some(recipient)) if name != recipient => Some(format!("{}({})", name, recipient)),
            (Some(name), _) => Some(name.clone()),
            (None, Some(recipient)) => Some(recipient.clone()),
            (None, None) => None,
        }
    }
}

/// Parameters for chat.send request.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatSendParams {
    pub session_key: String,
    pub bcs_group_id: String,
    pub message: MessageContent,
    pub channel: ChannelInfo,
    pub session_context: GroupContext,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timeout_ms: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub idempotency_key: Option<String>,
    /// BCS session layer id (`{group_id}:{8_hex}`). None when not pinned to a session.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bcs_session_id: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tags: Vec<String>,
}

/// Response for chat.send request.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatSendResponse {
    pub run_id: String,
}

/// Parameters for chat.inject event.
///
/// BCS sends chat.inject to bots that should observe silently (not mentioned, not originator).
/// The bot receives the message for context but should NOT respond.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatInjectParams {
    /// Session key for client identification.
    pub session_key: String,
    /// BCS group ID for this session.
    pub bcs_group_id: String,
    /// The message content being injected.
    pub message: MessageContent,
    /// Channel information.
    pub channel: ChannelInfo,
    /// Group context explaining why this bot received the message.
    pub session_context: GroupContext,
    /// BCS session layer id (`{group_id}:{8_hex}`). None when not pinned to a session.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bcs_session_id: Option<String>,
}

/// Parameters for chat.abort request.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatAbortParams {
    /// Session key (or bcs_group_id) to abort chats for.
    pub session_key: String,
    /// Specific run ID to abort (optional, aborts all if not provided).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub run_id: Option<String>,
}

// ---------------------------------------------------------------------------
// Streaming events (Bot → BCS, mirrors OpenClaw events.py)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum AgentStream {
    Lifecycle,
    Assistant,
    Tool,
    Thinking,
    Error,
}

/// Payload for `event: "agent"` frames.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentEventPayload {
    pub run_id: String,
    pub bcs_group_id: String,
    pub stream: AgentStream,
    pub ts: u64,
    /// Stream-specific data (delta text, tool call info, etc.)
    pub data: Value,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ToolPhase {
    Start,
    Result,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolEventData {
    pub name: String,
    pub phase: ToolPhase,
    #[serde(rename = "toolCallId")]
    pub tool_call_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub args: Option<Value>,
    #[serde(rename = "isError", default, skip_serializing_if = "Option::is_none")]
    pub is_error: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub result: Option<ToolResult>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResult {
    #[serde(default)]
    pub content: Vec<ToolResultContent>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResultContent {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub text: Option<String>,
    #[serde(rename = "type", default, skip_serializing_if = "Option::is_none")]
    pub content_type: Option<String>,
}

impl ToolEventData {
    pub fn result_text(&self) -> Option<String> {
        let mut text = String::new();
        let mut found_text = false;

        for block in self.result.as_ref()?.content.iter() {
            if let Some(block_text) = &block.text {
                text.push_str(block_text);
                found_text = true;
            }
        }

        found_text.then_some(text)
    }

    pub fn from_data(data: &Value) -> Option<Self> {
        serde_json::from_value(data.clone()).ok()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ChatEventState {
    Delta,
    Final,
    Aborted,
    Error,
    ToolCallStart,
    ToolCallEnd,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UsageInfo {
    pub input: u32,
    pub output: u32,
}

// ---------------------------------------------------------------------------
// Structured Routing (B2: Final Event Routing Metadata)
// ---------------------------------------------------------------------------

/// Wire representation of a route selector in the protocol layer.
///
/// Uses explicit object format: `{ "type": "capability", "value": "database" }`
/// rather than bare strings for stability and extensibility.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RouteSelectorWire {
    /// Selector type: "bot", "name", "role", "capability", "participants", "originator", "driver".
    #[serde(rename = "type")]
    pub selector_type: String,
    /// Selector value (required for most types, omitted for "originator"/"driver").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub value: Option<String>,
}

/// Response mode for structured routing.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ResponseMode {
    Required,
    Optional,
}

impl Default for ResponseMode {
    fn default() -> Self {
        Self::Required
    }
}

/// Structured routing metadata attached to a final chat event.
///
/// When a bot's runtime captures a `bcs_route` tool call, the routing intent
/// is attached to the `chat.event(state=final)` as this metadata.
/// BCS uses it to generate `RoutingDecision` without parsing @mentions from text.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatEventRouting {
    /// Target selectors (OR/union semantics for multiple selectors).
    pub responders: Vec<RouteSelectorWire>,
    /// Response mode (defaults to Required if omitted).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mode: Option<ResponseMode>,
    /// Reason for routing (audit and context).
    pub reason: String,
    /// Whether `participants:all` includes the sending bot (default false).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub include_self: Option<bool>,
    /// Idempotency key.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dedupe_key: Option<String>,
}

/// Action directive for a bot receiving a routed message.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum DirectiveAction {
    /// Bot should respond to this message.
    Respond,
    /// Bot should observe silently.
    Observe,
}

/// Source of the routing request.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RequestSource {
    /// Routing from structured metadata on final event.
    StructuredMetadata,
    /// Routing from legacy @mention parsing.
    LegacyMention,
    /// Routing from group default policy.
    DefaultPolicy,
    /// Routing from sender_routes static forwarding table.
    SenderRoutes,
}

/// Response directive injected into GroupContext.
///
/// Tells the receiving bot why it got the message and what to do.
/// New bots should read this instead of `you_are_mentioned`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResponseDirective {
    /// What the bot should do: respond or observe.
    pub action: DirectiveAction,
    /// Response mode (only meaningful when action=respond).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mode: Option<ResponseMode>,
    /// Routing reason from the requesting bot.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    /// How this routing was triggered.
    pub request_source: RequestSource,
    /// Which selector matched this bot (only for structured routing).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub matched_by: Option<RouteSelectorWire>,
}

/// Payload for `event: "chat"` frames.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatEventPayload {
    pub run_id: String,
    pub bcs_group_id: String,
    pub state: ChatEventState,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<MessageContent>,
    /// Incremental text for a single streaming delta frame (NOT cumulative).
    /// BCS accumulates these per segment itself rather than trusting the
    /// engine's cumulative `message.content`, so history rows carry only the
    /// text new to each chat segment. Absent on non-delta frames.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub delta_text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub usage: Option<UsageInfo>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stop_reason: Option<String>,
    #[serde(rename = "errorMessage", skip_serializing_if = "Option::is_none")]
    pub error_message: Option<String>,
    #[serde(rename = "errorKind", skip_serializing_if = "Option::is_none")]
    pub error_kind: Option<String>,
    /// Tool call ID (for tool_call_start/tool_call_end states).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
    /// Tool name (for tool_call_start/tool_call_end states).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_name: Option<String>,
    /// Tool arguments (for tool_call_start state).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub args: Option<Value>,
    /// Tool result (for tool_call_end state).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    /// Whether the tool call resulted in an error (for tool_call_end).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub is_error: Option<bool>,
    /// Whether the tool call succeeded (for tool_call_end).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub success: Option<bool>,
    /// Structured routing metadata (only processed when state=final).
    /// When present, BCS uses this instead of parsing @mentions from message text.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub routing: Option<ChatEventRouting>,
}

// ---------------------------------------------------------------------------
// Frame-building helpers (shared by bcs-message-flow, bcs-system-message, etc.)
// ---------------------------------------------------------------------------

/// Prefix prepended to auto-generated group IDs so downstream engines can
/// recognize a session as BCS-originated. Must match the value used when
/// generating new group IDs in the group service.
pub const GROUP_ID_PREFIX: &str = "bcs_grp_";

/// Build the short `session_key` (`group:<8 chars>`) sent to bots.
///
/// The 8-char window keeps the key compact, but a BCS-originated `wire_id`
/// (e.g. `bcs_grp_demo-group-alpha`) carries an 8-char prefix that would otherwise
/// consume the entire window and collapse every group to `group:bcs_grp_`.
/// When the prefix is present we keep it and take 8 chars from the id *after*
/// it, yielding `group:bcs_grp_demo-gro`. Legacy ids without the prefix are
/// truncated exactly as before (`group:legacy-g`), so existing groups are
/// unaffected.
///
/// Slicing uses `char_indices` to stay on UTF-8 boundaries; group ids are
/// ASCII today, but this avoids a latent panic if that ever changes.
pub fn build_session_key(wire_id: &str) -> String {
    let (prefix, rest) = match wire_id.strip_prefix(GROUP_ID_PREFIX) {
        Some(rest) => (GROUP_ID_PREFIX, rest),
        None => ("", wire_id),
    };
    let take = match rest.char_indices().nth(8) {
        Some((idx, _)) => &rest[..idx],
        None => rest,
    };
    format!("group:{}{}", prefix, take)
}

/// Current time in milliseconds since Unix epoch.
pub fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

/// Build a `chat.inject` event frame for silent message delivery.
///
/// `chat.inject` tells the receiving bot to observe the message for context
/// but NOT to respond. Used when a bot was not @mentioned and is not the
/// originator, yet still needs the message for situational awareness.
pub fn build_chat_inject_frame(
    run_id: &str,
    session_id: &str,
    group: &GroupContextInput,
    content: &str,
    from_bot_id: &str,
    from_bot_name: &str,
    mentions: &[String],
    target_bot: &str,
    is_self: bool,
    protocol_version: u32,
    from_bot_owner: Option<String>,
    group_type: Option<String>,
    bcs_session_id: Option<&str>,
) -> BcsFrame {
    let (group_context, text) = build_message_payload(
        group,
        content,
        from_bot_id,
        from_bot_name,
        mentions,
        target_bot,
        is_self,
        protocol_version,
        from_bot_owner,
        GroupContextDeliveryType::Inject,
        group_type,
    );
    let supports_session_field = protocol_version >= 3;
    let wire_id = legacy_aware_wire_group_id(session_id, bcs_session_id, supports_session_field);
    let session_key = build_session_key(wire_id);
    let inject = ChatInjectParams {
        session_key,
        bcs_group_id: wire_id.to_string(),
        message: MessageContent {
            role: "user".to_string(),
            content: vec![ContentBlock::text(text)],
            timestamp: now_ms(),
        },
        channel: ChannelInfo {
            source: ChannelSource::Api,
            user_id: Some(from_bot_name.to_string()),
            actor_id: Some(from_bot_id.to_string()),
            actor_name: Some(from_bot_name.to_string()),
            thread_id: Some(session_id.to_string()),
        },
        session_context: group_context,
        bcs_session_id: if supports_session_field {
            bcs_session_id.map(str::to_string)
        } else {
            None
        },
    };
    let mut payload = serde_json::to_value(inject).unwrap_or_else(|error| {
        tracing::warn!(%error, "failed to serialize chat.inject params");
        Value::Null
    });
    if let Some(obj) = payload.as_object_mut() {
        obj.insert("from".to_string(), Value::String(from_bot_id.to_string()));
        obj.insert("deliver".to_string(), Value::Bool(false));
    }
    BcsFrame::Request(RequestFrame::new(
        run_id.to_string(),
        "chat.inject",
        Some(payload),
    ))
}

/// Build a `chat.send` request frame for active message delivery.
///
/// `chat.send` tells the receiving bot to process the message and respond.
/// Unlike `chat.inject`, this frame includes `deliver: true` and is sent as a
/// `RequestFrame` so the bot treats it as a message requiring action.
pub fn build_chat_send_frame(
    run_id: &str,
    session_id: &str,
    group: &GroupContextInput,
    content: &str,
    from_bot_id: &str,
    from_bot_name: &str,
    mentions: &[String],
    target_bot: &str,
    attachments: &Option<Vec<Value>>,
    thinking: &Option<String>,
    is_self: bool,
    protocol_version: u32,
    from_bot_owner: Option<String>,
    group_type: Option<String>,
    bcs_session_id: Option<&str>,
) -> BcsFrame {
    let (group_context, text) = build_message_payload(
        group,
        content,
        from_bot_id,
        from_bot_name,
        mentions,
        target_bot,
        is_self,
        protocol_version,
        from_bot_owner,
        GroupContextDeliveryType::Send,
        group_type,
    );
    // Protocol version >= 3: bcs_group_id stays the real group id,
    // bcs_session_id is set explicitly on the params.
    // Protocol version < 3: substitute session_id wire format into
    // bcs_group_id for backward compat (old BCN plugins key on it).
    let supports_session_field = protocol_version >= 3;
    let wire_id = legacy_aware_wire_group_id(session_id, bcs_session_id, supports_session_field);
    let session_key = build_session_key(wire_id);
    let send = ChatSendParams {
        session_key,
        bcs_group_id: wire_id.to_string(),
        message: MessageContent {
            role: "user".to_string(),
            content: vec![ContentBlock::text(text)],
            timestamp: now_ms(),
        },
        channel: ChannelInfo {
            source: ChannelSource::Api,
            user_id: Some(from_bot_name.to_string()),
            actor_id: Some(from_bot_id.to_string()),
            actor_name: Some(from_bot_name.to_string()),
            thread_id: Some(session_id.to_string()),
        },
        session_context: group_context,
        timeout_ms: None,
        idempotency_key: Some(run_id.to_string()),
        bcs_session_id: if supports_session_field {
            bcs_session_id.map(str::to_string)
        } else {
            None
        },
        tags: Vec::new(),
    };
    let mut params = serde_json::to_value(send).unwrap_or_else(|error| {
        tracing::warn!(%error, "failed to serialize chat.send params");
        Value::Null
    });
    if let Some(obj) = params.as_object_mut() {
        obj.insert("deliver".to_string(), Value::Bool(true));
        if let Some(attachments) = attachments {
            obj.insert(
                "attachments".to_string(),
                Value::Array(attachments.clone()),
            );
        }
        if let Some(thinking) = thinking {
            obj.insert("thinking".to_string(), Value::String(thinking.clone()));
        }
    }
    BcsFrame::Request(RequestFrame::new(
        run_id.to_string(),
        "chat.send",
        Some(params),
    ))
}

fn legacy_aware_wire_group_id<'a>(
    group_id: &'a str,
    bcs_session_id: Option<&'a str>,
    supports_session_field: bool,
) -> &'a str {
    match (bcs_session_id, supports_session_field) {
        (Some(sid), false) if !sid.ends_with(":00000000") => sid,
        _ => group_id,
    }
}

/// Shared helper: build the `GroupContext` and formatted text for a chat frame.
///
/// Returns `(group_context, text)` where `text` is the final message content
/// including the BCS v2 group-context header when `protocol_version >= 2`.
fn build_message_payload(
    group: &GroupContextInput,
    content: &str,
    from_bot_id: &str,
    from_bot_name: &str,
    mentions: &[String],
    target_bot: &str,
    is_self: bool,
    protocol_version: u32,
    from_bot_owner: Option<String>,
    delivery_type: GroupContextDeliveryType,
    group_type: Option<String>,
) -> (GroupContext, String) {
    let raw_text = if is_self {
        content.to_string()
    } else {
        format!("[from:{}]{}", from_bot_name, content)
    };
    let mut group_context = build_recipient_group_context(
        group,
        target_bot,
        from_bot_id,
        content,
        mentions,
        delivery_type,
        Some(response_directive_for_delivery(
            delivery_type,
            RequestSource::LegacyMention,
        )),
        None,
        group_type,
        from_bot_owner,
    );
    apply_sender_display_name(&mut group_context, from_bot_id, from_bot_name);
    let text = if protocol_version >= 2 {
        format!(
            "{}\n\n[消息内容]\n{}",
            group_context.format_header(),
            raw_text
        )
    } else {
        raw_text
    };
    (group_context, text)
}

/// Overwrite `group_context.from` with a display-name-annotated form when the
/// display name differs from the raw actor id.
pub fn apply_sender_display_name(
    group_context: &mut GroupContext,
    from_actor_id: &str,
    from_display_name: &str,
) {
    let display = from_display_name.trim();
    if !display.is_empty() && display != from_actor_id {
        group_context.from = format!("{display}({from_actor_id})");
    }
}

/// Build a `ResponseDirective` based on delivery type and request source.
///
/// - Send → Respond (Required)
/// - Inject → Observe
pub fn response_directive_for_delivery(
    delivery_type: GroupContextDeliveryType,
    request_source: RequestSource,
) -> ResponseDirective {
    let action = match delivery_type {
        GroupContextDeliveryType::Send => DirectiveAction::Respond,
        GroupContextDeliveryType::Inject => DirectiveAction::Observe,
    };

    ResponseDirective {
        action,
        mode: if action == DirectiveAction::Respond {
            Some(ResponseMode::Required)
        } else {
            None
        },
        reason: None,
        request_source,
        matched_by: None,
    }
}

/// Build a `GroupContext` for a specific recipient in a group.
///
/// Constructs the full session context that tells the receiving bot who it is,
/// who sent the message, whether it was mentioned, and how it should behave.
pub fn build_recipient_group_context(
    group: &GroupContextInput,
    target_bot: &str,
    from: &str,
    message: &str,
    mentions: &[String],
    delivery_type: GroupContextDeliveryType,
    response_directive: Option<ResponseDirective>,
    routing_mode: Option<String>,
    group_type: Option<String>,
    from_bot_owner: Option<String>,
) -> GroupContext {
    let target = group.participants.iter().find(|p| p.id == target_bot);
    let recipient_name = target.and_then(|p| p.name.clone());
    let recipient_role = target.and_then(|p| p.role.clone()).or_else(|| {
        if target_bot == group.driver_bot {
            Some("driver".to_string())
        } else {
            None
        }
    });

    GroupContext {
        session_id: group.session_id.clone(),
        participants: group
            .participants
            .iter()
            .filter(|p| p.is_bot)
            .map(display_participant)
            .collect(),
        recipient: Some(target_bot.to_string()),
        recipient_name,
        recipient_role,
        delivery_type: Some(delivery_slug(delivery_type).to_string()),
        originator: group
            .participants
            .iter()
            .find(|p| p.id == group.originator)
            .map(display_participant)
            .unwrap_or_else(|| group.originator.clone()),
        from: group
            .participants
            .iter()
            .find(|p| p.id == from)
            .map(display_participant)
            .unwrap_or_else(|| from.to_string()),
        from_bot_id: Some(from.to_string()),
        from_bot_owner,
        you_are_mentioned: mentions.iter().any(|m| m == target_bot),
        is_sender: from == target_bot,
        mentions: mentions.to_vec(),
        response_directive,
        message: message.to_string(),
        routing_mode,
        group_type,
    }
}

// -- Private helpers for group-context formatting --

fn display_participant(participant: &GroupContextParticipant) -> String {
    display_bot(&participant.id, participant.name.as_deref())
}

fn display_bot(bot_uuid: &str, bot_name: Option<&str>) -> String {
    match bot_name {
        Some(name) if !name.is_empty() && name != bot_uuid => {
            format!("{}({})", name, bot_uuid)
        }
        _ => bot_uuid.to_string(),
    }
}

fn delivery_slug(delivery_type: GroupContextDeliveryType) -> &'static str {
    match delivery_type {
        GroupContextDeliveryType::Send => "send",
        GroupContextDeliveryType::Inject => "inject",
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_session_key_legacy_unprefixed_unchanged() {
        // Existing groups have a bare legacy id; must keep producing the exact
        // same `group:<first 8>` as the old truncation did.
        assert_eq!(
            build_session_key("legacy-group-alpha"),
            "group:legacy-g"
        );
    }

    #[test]
    fn build_session_key_prefixed_keeps_prefix_and_distinguishes() {
        // bcs_grp_ prefix is preserved, then 8 chars of the real id follow,
        // so different groups stay distinguishable instead of collapsing to
        // `group:bcs_grp_`.
        assert_eq!(
            build_session_key("bcs_grp_demo-group-alpha"),
            "group:bcs_grp_demo-gro"
        );
        assert_ne!(
            build_session_key("bcs_grp_demo-group-alpha"),
            build_session_key("bcs_grp_other-group-beta"),
        );
    }

    #[test]
    fn build_session_key_prefixed_session_id_form() {
        // session_id wire form (`{group_id}:{hex}`) also carries the prefix.
        assert_eq!(
            build_session_key("bcs_grp_demo-group-alpha:demo-run-marker"),
            "group:bcs_grp_demo-gro"
        );
    }

    #[test]
    fn build_session_key_short_ids_do_not_panic() {
        assert_eq!(build_session_key("abc"), "group:abc");
        assert_eq!(build_session_key("bcs_grp_"), "group:bcs_grp_");
        assert_eq!(build_session_key("bcs_grp_ab"), "group:bcs_grp_ab");
    }

    #[test]
    fn test_request_frame_serialization() {
        let req = RequestFrame::new("req-001", "bot.connect", Some(serde_json::json!({"token": null})));
        let frame = BcsFrame::Request(req);
        let json = serde_json::to_string(&frame).unwrap();
        assert!(json.contains(r#""type":"req""#));
        assert!(json.contains(r#""id":"req-001""#));
        assert!(json.contains(r#""method":"bot.connect""#));
    }

    #[test]
    fn test_request_frame_deserialization() {
        let json = r#"{"type":"req","id":"r1","method":"chat.send","params":{}}"#;
        let frame: BcsFrame = serde_json::from_str(json).unwrap();
        match frame {
            BcsFrame::Request(req) => {
                assert_eq!(req.id, "r1");
                assert_eq!(req.method, "chat.send");
            }
            _ => panic!("Expected RequestFrame"),
        }
    }

    #[test]
    fn test_response_frame_ok() {
        let res = ResponseFrame::ok("r1", serde_json::json!({"run_id": "run-001"}));
        assert_eq!(res.frame_type, "res");
        assert!(res.ok);
        assert!(res.payload.is_some());
        assert!(res.error.is_none());
    }

    #[test]
    fn test_response_frame_error() {
        let res = ResponseFrame::err("r1", ErrorShape {
            code: "bot_not_found".to_string(),
            message: "Bot not found".to_string(),
            details: None,
            retryable: false,
            retry_after_ms: None,
        });
        assert!(!res.ok);
        assert!(res.error.is_some());
    }

    #[test]
    fn test_event_frame_new() {
        let event = EventFrame::new("agent", Some(serde_json::json!({"data": "test"})), Some(1));
        assert_eq!(event.frame_type, "event");
        assert_eq!(event.event, "agent");
        assert_eq!(event.seq, Some(1));
        assert_eq!(event.state_version, None);
    }

    #[test]
    fn test_content_block_text() {
        let block = ContentBlock::text("Hello World");
        assert_eq!(block.block_type, "text");
        assert_eq!(block.text, Some("Hello World".to_string()));
        assert_eq!(block.url, None);
    }

    #[test]
    fn test_content_block_image() {
        let block = ContentBlock::image("http://example.com/img.png");
        assert_eq!(block.block_type, "image");
        assert_eq!(block.url, Some("http://example.com/img.png".to_string()));
        assert_eq!(block.text, None);
    }

    #[test]
    fn test_channel_source_serialization() {
        assert_eq!(serde_json::to_string(&ChannelSource::WebUi).unwrap(), r#""webui""#);
        assert_eq!(serde_json::to_string(&ChannelSource::DingTalk).unwrap(), r#""dingtalk""#);
        assert_eq!(serde_json::to_string(&ChannelSource::Api).unwrap(), r#""api""#);
    }

    #[test]
    fn test_chat_event_state_serialization() {
        assert_eq!(serde_json::to_string(&ChatEventState::Delta).unwrap(), r#""delta""#);
        assert_eq!(serde_json::to_string(&ChatEventState::Final).unwrap(), r#""final""#);
        assert_eq!(serde_json::to_string(&ChatEventState::Aborted).unwrap(), r#""aborted""#);
        assert_eq!(serde_json::to_string(&ChatEventState::Error).unwrap(), r#""error""#);
        assert_eq!(serde_json::to_string(&ChatEventState::ToolCallStart).unwrap(), r#""tool_call_start""#);
        assert_eq!(serde_json::to_string(&ChatEventState::ToolCallEnd).unwrap(), r#""tool_call_end""#);
    }

    mod tool_event {
        use super::*;

        #[test]
        fn parses_start_phase() {
            let json = r#"{
                "args": {"query": "*"},
                "name": "memory_search",
                "phase": "start",
                "toolCallId": "fc-start"
            }"#;

            let data: ToolEventData = serde_json::from_str(json).unwrap();

            assert_eq!(data.name, "memory_search");
            assert_eq!(data.phase, ToolPhase::Start);
            assert_eq!(data.tool_call_id, "fc-start");
            assert!(data.result.is_none());
        }

        #[test]
        fn parses_result_phase_and_extracts_text() {
            let json = r#"{
                "isError": false,
                "name": "memory_search",
                "phase": "result",
                "result": {"content": [{"text": "hello-output", "type": "text"}]},
                "toolCallId": "fc-result"
            }"#;

            let data: ToolEventData = serde_json::from_str(json).unwrap();

            assert_eq!(data.phase, ToolPhase::Result);
            assert_eq!(data.is_error, Some(false));
            assert_eq!(data.result_text(), Some("hello-output".to_string()));
        }

        #[test]
        fn result_text_joins_multiple_text_blocks() {
            let json = r#"{
                "isError": false,
                "name": "memory_search",
                "phase": "result",
                "result": {"content": [
                    {"text": "a", "type": "text"},
                    {"text": "b", "type": "text"}
                ]},
                "toolCallId": "fc-result"
            }"#;

            let data: ToolEventData = serde_json::from_str(json).unwrap();

            assert_eq!(data.result_text(), Some("ab".to_string()));
        }
    }

    #[test]
    fn test_bot_status_serialization() {
        assert_eq!(serde_json::to_string(&BotStatus::Idle).unwrap(), r#""idle""#);
        assert_eq!(serde_json::to_string(&BotStatus::Busy).unwrap(), r#""busy""#);
        assert_eq!(serde_json::to_string(&BotStatus::Error).unwrap(), r#""error""#);
    }

    #[test]
    fn test_bcs_frame_variants() {
        let req_json = r#"{"type":"req","id":"1","method":"test","params":{}}"#;
        let res_json = r#"{"type":"res","id":"1","ok":true}"#;
        let event_json = r#"{"type":"event","event":"test","payload":{},"seq":1}"#;

        assert!(matches!(serde_json::from_str::<BcsFrame>(req_json).unwrap(), BcsFrame::Request(_)));
        assert!(matches!(serde_json::from_str::<BcsFrame>(res_json).unwrap(), BcsFrame::Response(_)));
        assert!(matches!(serde_json::from_str::<BcsFrame>(event_json).unwrap(), BcsFrame::Event(_)));
    }

    // =========================================================================
    // GroupContext Tests (BCS.md: Session Context Injection)
    // =========================================================================

    /// Test GroupContext serialization with all fields
    /// BCS.md: Session context injection
    #[test]
    fn test_group_context_serialization() {
        let ctx = GroupContext {
            session_id: "grp-001".to_string(),
            participants: vec!["zhangsan".to_string(), "dba".to_string()],
            originator: "zhangsan".to_string(),
            from: "user".to_string(),
            you_are_mentioned: true,
            is_sender: false,
            mentions: vec!["dba".to_string()],
            response_directive: None,
            message: "@dba 请帮我排查数据库死锁".to_string(),
            recipient: None,
            recipient_name: None,
            recipient_role: None,
            delivery_type: None,
            routing_mode: None,
            group_type: None,
            from_bot_id: None,
            from_bot_owner: None,
        };

        let json = serde_json::to_string(&ctx).unwrap();
        assert!(json.contains(r#""session_id":"grp-001""#));
        assert!(json.contains(r#""originator":"zhangsan""#));
        assert!(json.contains(r#""from":"user""#));
        assert!(json.contains(r#""you_are_mentioned":true"#));
        assert!(json.contains(r#""is_sender":false"#));
        assert!(json.contains(r#""message":"@dba 请帮我排查数据库死锁""#));
    }

    /// Test GroupContext deserialization
    #[test]
    fn test_group_context_deserialization() {
        let json = r#"{
            "session_id": "grp-002",
            "participants": ["pm", "dev", "security"],
            "originator": "pm",
            "from": "dev",
            "you_are_mentioned": false,
            "is_sender": false,
            "mentions": [],
            "message": "这个方案从安全角度可行吗？"
        }"#;

        let ctx: GroupContext = serde_json::from_str(json).unwrap();
        assert_eq!(ctx.session_id, "grp-002");
        assert_eq!(ctx.participants, vec!["pm", "dev", "security"]);
        assert_eq!(ctx.originator, "pm");
        assert_eq!(ctx.from, "dev");
        assert!(!ctx.you_are_mentioned);
        assert!(!ctx.is_sender);
        assert!(ctx.mentions.is_empty());
        assert_eq!(ctx.message, "这个方案从安全角度可行吗？");
    }

    /// Test GroupContext with default values for optional fields
    #[test]
    fn test_group_context_defaults() {
        let json = r#"{
            "session_id": "grp-003",
            "originator": "bot1",
            "from": "bot2",
            "message": "Hello"
        }"#;

        let ctx: GroupContext = serde_json::from_str(json).unwrap();
        assert_eq!(ctx.session_id, "grp-003");
        assert!(ctx.participants.is_empty()); // default
        assert!(!ctx.you_are_mentioned); // default false
        assert!(!ctx.is_sender); // default false
        assert!(ctx.mentions.is_empty()); // default empty
        assert_eq!(ctx.message, "Hello");
    }

    /// Test GroupContext for broadcast message (no mentions)
    /// BCS.md: "无 @mention → 广播给所有参与者"
    #[test]
    fn test_group_context_broadcast_scenario() {
        let ctx = GroupContext {
            session_id: "grp-001".to_string(),
            participants: vec!["zhangsan".to_string(), "dba".to_string()],
            originator: "zhangsan".to_string(),
            from: "user".to_string(),
            you_are_mentioned: false,  // Not mentioned - broadcast
            is_sender: false,
            mentions: vec![],  // No mentions
            response_directive: None,
            message: "进度怎么样了？".to_string(),
            recipient: None,
            recipient_name: None,
            recipient_role: None,
            delivery_type: None,
            routing_mode: None,
            group_type: None,
            from_bot_id: None,
            from_bot_owner: None,
        };

        // For broadcast: if bot is originator, should respond
        assert!(!ctx.you_are_mentioned);
        assert!(ctx.mentions.is_empty());
    }

    /// Test GroupContext for @mention message
    /// BCS.md: "@mention 消息仍广播给所有参与者，但被提及的 Bot 应响应"
    #[test]
    fn test_group_context_mention_scenario() {
        let ctx = GroupContext {
            session_id: "grp-001".to_string(),
            participants: vec!["zhangsan".to_string(), "dba".to_string()],
            originator: "zhangsan".to_string(),
            from: "zhangsan".to_string(),
            you_are_mentioned: true,  // Mentioned - must respond
            is_sender: false,
            mentions: vec!["dba".to_string()],
            response_directive: None,
            message: "@dba 请分析死锁根因".to_string(),
            recipient: None,
            recipient_name: None,
            recipient_role: None,
            delivery_type: None,
            routing_mode: None,
            group_type: None,
            from_bot_id: None,
            from_bot_owner: None,
        };

        // When mentioned, bot must respond
        assert!(ctx.you_are_mentioned);
        assert_eq!(ctx.mentions, vec!["dba"]);
    }

    /// Test GroupContext for sender (is_sender=true)
    /// BCS.md: "我是发送者吗？→ 是: 不要响应"
    #[test]
    fn test_group_context_sender_scenario() {
        let ctx = GroupContext {
            session_id: "grp-001".to_string(),
            participants: vec!["zhangsan".to_string(), "dba".to_string()],
            originator: "zhangsan".to_string(),
            from: "dba".to_string(),
            you_are_mentioned: false,
            is_sender: true,  // This is my own message
            mentions: vec![],
            response_directive: None,
            message: "排查结果：根因是...".to_string(),
            recipient: None,
            recipient_name: None,
            recipient_role: None,
            delivery_type: None,
            routing_mode: None,
            group_type: None,
            from_bot_id: None,
            from_bot_owner: None,
        };

        // When is_sender is true, bot should NOT respond
        assert!(ctx.is_sender);
        assert!(!ctx.you_are_mentioned);
    }

    /// Test GroupContext for multi-participant coordination
    #[test]
    fn test_group_context_multi_participant() {
        let ctx = GroupContext {
            session_id: "grp-002".to_string(),
            participants: vec!["dev".to_string(), "pm".to_string(), "security".to_string()],
            originator: "dev".to_string(),
            from: "user".to_string(),
            you_are_mentioned: false,
            is_sender: false,
            mentions: vec![],
            response_directive: None,
            message: "代码和PRD有冲突，帮我协调".to_string(),
            recipient: None,
            recipient_name: None,
            recipient_role: None,
            delivery_type: None,
            routing_mode: None,
            group_type: None,
            from_bot_id: None,
            from_bot_owner: None,
        };

        assert_eq!(ctx.participants.len(), 3);
    }

    /// Test GroupContext serialization with routing_mode present
    #[test]
    fn test_group_context_with_routing_mode() {
        let ctx = GroupContext {
            session_id: "grp-004".to_string(),
            participants: vec!["a".to_string(), "b".to_string()],
            originator: "a".to_string(),
            from: "a".to_string(),
            you_are_mentioned: false,
            is_sender: false,
            mentions: vec![],
            response_directive: None,
            message: "hello".to_string(),
            recipient: None,
            recipient_name: None,
            recipient_role: None,
            delivery_type: None,
            routing_mode: Some("mention".to_string()),
            group_type: None,
            from_bot_id: None,
            from_bot_owner: None,
        };
        let json = serde_json::to_string(&ctx).unwrap();
        assert!(json.contains(r#""routing_mode":"mention""#));

        let parsed: GroupContext = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.routing_mode, Some("mention".to_string()));
    }

    /// Test GroupContext deserialization omits routing_mode when absent (backward compat)
    #[test]
    fn test_group_context_routing_mode_absent_defaults_none() {
        let json = r#"{
            "session_id": "grp-005",
            "originator": "bot1",
            "from": "bot2",
            "message": "Hello"
        }"#;
        let ctx: GroupContext = serde_json::from_str(json).unwrap();
        assert_eq!(ctx.routing_mode, None);
    }

    /// Test GroupContext formats recipient-view fields for LLM identity anchoring.
    #[test]
    fn test_group_context_format_header_includes_recipient_view() {
        let json = r#"{
            "session_id": "grp-006",
            "participants": ["Driver(bot-a)", "DBA(bot-b)"],
            "originator": "bot-a",
            "from": "system",
            "recipient": "bot-b",
            "recipient_name": "DBA",
            "recipient_role": "consultant",
            "delivery_type": "inject",
            "message": "initial group context"
        }"#;

        let ctx: GroupContext = serde_json::from_str(json).unwrap();
        let header = ctx.format_header();

        assert!(header.contains("- 你是: DBA(bot-b)"));
        assert!(header.contains("- 你的角色: consultant"));
        assert!(header.contains("- 当前投递: chat.inject"));
        assert!(header.contains("静默观察"));
    }

    /// Test RequestSource::SenderRoutes serialization
    #[test]
    fn test_request_source_sender_routes_serde() {
        let source = RequestSource::SenderRoutes;
        let json = serde_json::to_string(&source).unwrap();
        assert_eq!(json, r#""sender_routes""#);
        let parsed: RequestSource = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed, RequestSource::SenderRoutes);
    }

    /// Test BotConnectParams with preconfigured bot_id
    #[test]
    fn test_bot_connect_params_with_bot_id() {
        let params = BotConnectParams {
            token: Some("placeholder-existing-session".to_string()),
            bot_id: Some("example_bot:example_owner".to_string()),
            protocol_version: None,
            client_kind: None,
        };
        let json = serde_json::to_string(&params).unwrap();
        assert!(json.contains(r#""token":"placeholder-existing-session""#));
        assert!(json.contains(r#""bot_id":"example_bot:example_owner""#));
    }

    /// Test BotConnectParams serialization with optional fields
    #[test]
    fn test_bot_connect_params_optional_fields() {
        // Only token
        let params = BotConnectParams {
            token: Some("placeholder-session-marker".to_string()),
            bot_id: None,
            protocol_version: None,
            client_kind: None,
        };
        let json = serde_json::to_string(&params).unwrap();
        assert!(json.contains(r#""token":"placeholder-session-marker""#));
        // bot_id is None, so it serializes as null (serde default behavior)
        assert!(json.contains(r#""bot_id":null"#));

        // Only bot_id
        let params = BotConnectParams {
            token: None,
            bot_id: Some("bot:owner".to_string()),
            protocol_version: None,
            client_kind: None,
        };
        let json = serde_json::to_string(&params).unwrap();
        assert!(json.contains(r#""bot_id":"bot:owner""#));

        // Neither (defaults)
        let params = BotConnectParams {
            token: None,
            bot_id: None,
            protocol_version: None,
            client_kind: None,
        };
        let json = serde_json::to_string(&params).unwrap();
        // Both should be null with #[serde(default)]
        assert!(json.contains(r#""token":null"#));
        assert!(json.contains(r#""bot_id":null"#));
    }

    /// Test BotConnectParams deserialization
    #[test]
    fn test_bot_connect_params_deserialization() {
        // With both fields
        let json = r#"{"token":"placeholder-session-marker","bot_id":"example_bot:example_owner"}"#;
        let params: BotConnectParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.token, Some("placeholder-session-marker".to_string()));
        assert_eq!(params.bot_id, Some("example_bot:example_owner".to_string()));

        // With only token
        let json = r#"{"token":"placeholder-session-only"}"#;
        let params: BotConnectParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.token, Some("placeholder-session-only".to_string()));
        assert_eq!(params.bot_id, None);

        // Empty object (defaults)
        let json = r#"{}"#;
        let params: BotConnectParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.token, None);
        assert_eq!(params.bot_id, None);
    }

    // -------------------------------------------------------------
    // Fix 5: bcs_session_id wiring in chat.send / chat.inject frames
    // -------------------------------------------------------------

    fn test_group_context_input() -> GroupContextInput {
        GroupContextInput {
            session_id: "grp-test".to_string(),
            driver_bot: "driver".to_string(),
            originator: "driver".to_string(),
            participants: vec![
                GroupContextParticipant {
                    id: "driver".to_string(),
                    name: Some("Driver".to_string()),
                    role: Some("driver".to_string()),
                    is_bot: true,
                },
            ],
            bcs_session_id: None,
        }
    }

    #[test]
    fn build_chat_send_frame_with_bcs_session_id_and_high_version() {
        let ctx = test_group_context_input();
        let frame = build_chat_send_frame(
            "r1", "grp-test", &ctx, "hello", "b1", "Bot1", &[], "target",
            &None, &None, false, 3, None, None, Some("grp-test:abc12345"),
        );
        match frame {
            BcsFrame::Request(req) => {
                let p: ChatSendParams = serde_json::from_value(req.params.unwrap()).unwrap();
                // v>=3: bcs_group_id stays the real group id
                assert_eq!(p.bcs_group_id, "grp-test");
                // bcs_session_id is set explicitly
                assert_eq!(p.bcs_session_id.as_deref(), Some("grp-test:abc12345"));
                assert_eq!(p.channel.user_id.as_deref(), Some("Bot1"));
                assert_eq!(p.channel.actor_id.as_deref(), Some("b1"));
                assert_eq!(p.channel.actor_name.as_deref(), Some("Bot1"));
            }
            _ => panic!("expected Request frame"),
        }
    }

    #[test]
    fn build_chat_send_frame_with_bcs_session_id_and_low_version() {
        let ctx = test_group_context_input();
        let frame = build_chat_send_frame(
            "r1", "grp-test", &ctx, "hello", "b1", "Bot1", &[], "target",
            &None, &None, false, 2, None, None, Some("grp-test:abc12345"),
        );
        match frame {
            BcsFrame::Request(req) => {
                let p: ChatSendParams = serde_json::from_value(req.params.unwrap()).unwrap();
                // v<3: bcs_session_id is substituted into bcs_group_id
                assert_eq!(p.bcs_group_id, "grp-test:abc12345");
                // bcs_session_id is NOT set (None → not serialized due to skip_serializing_if)
                assert_eq!(p.bcs_session_id, None);
            }
            _ => panic!("expected Request frame"),
        }
    }

    #[test]
    fn build_chat_send_frame_with_legacy_session_id_and_low_version_keeps_group_id() {
        let ctx = test_group_context_input();
        let frame = build_chat_send_frame(
            "r1", "grp-test", &ctx, "hello", "b1", "Bot1", &[], "target",
            &None, &None, false, 2, None, None, Some("grp-test:00000000"),
        );
        match frame {
            BcsFrame::Request(req) => {
                let p: ChatSendParams = serde_json::from_value(req.params.unwrap()).unwrap();
                assert_eq!(p.bcs_group_id, "grp-test");
                assert_eq!(p.bcs_session_id, None);
            }
            _ => panic!("expected Request frame"),
        }
    }

    #[test]
    fn build_chat_send_frame_without_bcs_session_id() {
        let ctx = test_group_context_input();
        let frame = build_chat_send_frame(
            "r1", "grp-test", &ctx, "hello", "b1", "Bot1", &[], "target",
            &None, &None, false, 3, None, None, None,
        );
        match frame {
            BcsFrame::Request(req) => {
                let p: ChatSendParams = serde_json::from_value(req.params.unwrap()).unwrap();
                assert_eq!(p.bcs_group_id, "grp-test");
                assert_eq!(p.bcs_session_id, None);
            }
            _ => panic!("expected Request frame"),
        }
    }

    #[test]
    fn build_chat_inject_frame_with_legacy_session_id_and_low_version_keeps_group_id() {
        let ctx = test_group_context_input();
        let frame = build_chat_inject_frame(
            "r1", "grp-test", &ctx, "hello", "b1", "Bot1", &[], "target",
            false, 2, None, None, Some("grp-test:00000000"),
        );
        match frame {
            BcsFrame::Request(req) => {
                let p: ChatInjectParams = serde_json::from_value(req.params.unwrap()).unwrap();
                assert_eq!(p.bcs_group_id, "grp-test");
                assert_eq!(p.bcs_session_id, None);
            }
            _ => panic!("expected Request frame"),
        }
    }

    #[test]
    fn build_chat_inject_frame_with_bcs_session_id() {
        let ctx = test_group_context_input();
        let frame = build_chat_inject_frame(
            "r1", "grp-test", &ctx, "hello", "b1", "Bot1", &[], "target",
            false, 3, None, None, Some("grp-test:abc12345"),
        );
        match frame {
            BcsFrame::Request(req) => {
                let p: ChatInjectParams = serde_json::from_value(req.params.unwrap()).unwrap();
                assert_eq!(p.bcs_session_id.as_deref(), Some("grp-test:abc12345"));
                assert_eq!(p.channel.user_id.as_deref(), Some("Bot1"));
                assert_eq!(p.channel.actor_id.as_deref(), Some("b1"));
                assert_eq!(p.channel.actor_name.as_deref(), Some("Bot1"));
            }
            _ => panic!("expected Request frame"),
        }
    }
}
