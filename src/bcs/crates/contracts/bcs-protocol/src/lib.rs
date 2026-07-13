//! BCS wire protocol and HTTP DTO types.
//!
//! This crate centralizes everything that crosses the bot / BCS boundary
//! or a BCS public HTTP / streaming boundary:
//!
//! - Streaming frame layer (`BcsFrame`, `RequestFrame`, `ResponseFrame`, `EventFrame`)
//! - Bot connect / status / onboard frame payloads
//! - Chat event wire types
//! - HTTP DTOs (bot capabilities, onboard, status, friend requests, group create, fusion, proposal)
//!
//! Previously split between `bcs-client/src/protocol.rs` and the top of
//! `bcs-client/src/lib.rs`. The retired `bcs-client` crate's shared DTOs now
//! live here.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

pub mod a2a;
pub mod delivery;
pub mod http;
pub mod principal;
pub mod ws;
pub mod stream;

pub use a2a::A2aRunStatus;
pub use delivery::{BotDeliveryKind, FrontendDeliveryKind, FrontendDeliveryTarget};
pub use http::chat_run;
pub use http::{
    BCN_EVENT_ID_HEADER, BCN_MESSAGE_ID_HEADER, BCN_PROTOCOL_VERSION_HEADER, BCN_TRANSPORT_HEADER,
    BCN_PROVIDER_BOT_REF_HEADER, BCN_PROVIDER_ID_HEADER, BCN_TIMESTAMP_HEADER,
    AdminOnboardRequest, BCS_CHAT_VERSION, BCS_CHAT_VERSION_HEADER, BotCapabilities,
    BotContextSummary, BotDynamicStatus, BotInfo, ChatRunCancelResponse, ChatRunResponseContent,
    ChatRunState, ChatRunStatusResponse, ChatRunSubmitResponse, ConfirmProposalResponse, Conflict, ConflictPosition,
    CreateFriendRequestBody, CreateGroupRequest, CreateGroupResponse, CreateOrganizationRequest,
    DiscoverBotEntry,
    DiscoverBotProviderInfo, DiscoverBotsExtendedResponse, DiscoverBotsResponse,
    DynamicStatusResponse, EngineType, EvaluateProposalRequest, FriendApiResponse,
    FriendEntry, FusionRequest, FusionResponse,
    JoinRequest, JoinResponse, LeaveResponse, ListFriendRequestsQuery, OnboardRequest,
    OnboardResponse, OrganizationCandidateBotListResponse, OrganizationCandidateBotResponse,
    OrganizationListResponse, OrganizationMemberListResponse, OrganizationMemberResponse,
    OrganizationResponse, ParticipantBindingInfo, ParticipantInfo, ParticipantPerspective,
    PatchOrganizationRequest, PatchProviderBotRequest, PatchProviderRequest, ProposalContext, ProposalResponse,
    ProviderAckResponse, ProviderAuthDto, ProviderAuthModeDto, ProviderCoordinationConfigDto,
    ProviderCoordinationEventKindDto, ProviderCoordinationEventRequest,
    ProviderCoordinationIntentDto, ProviderCoordinationModeDto, ProviderHistoryResponse,
    ProviderInfoResponse, ProviderOrganizationManagementConfigDto,
    ProviderWebhookBotRef, ProviderWebhookRequest, ProviderWebhookSender, QueryBotEntry,
    QueryBotsRequest, RegisterProviderBotRequest, RegisterProviderBotResponse,
    PutOrganizationMemberRequest, RegisterProviderRequest, RegisterProviderResponse,
    SetVisibilityRequest, UpdateStatusRequest,
    UpdateStatusResponse,
};
pub use principal::{AdminActor, BotActor, CallerContext, HumanActor, IntegrationClient};
pub use ws::protocol;
pub use ws::{
    AgentEventPayload, AgentStream, BCS_MIN_SUPPORTED_VERSION, BCS_PROTOCOL_VERSION, BcsFrame,
    BotConnectParams, BotConnectResponse, BotStatus, BotStatusParams, ChannelInfo, ChannelSource,
    ChatAbortParams, ChatEventPayload, ChatEventRouting, ChatEventState, ChatInjectParams,
    ChatSendParams, ChatSendResponse, ContentBlock, CoordinationCall, DirectiveAction, ErrorShape,
    EventFrame, GatewayFrame, GroupContext, GroupContextDeliveryType, GroupContextInput,
    GroupContextParticipant, MessageContent, OnboardRequestParams, OnboardResponsePayload,
    ProtocolDeprecation, RequestFrame, RequestSource, ResponseDirective, ResponseFrame,
    ResponseMode, RouteSelectorWire, ToolEventData, ToolPhase, ToolResult,
    ToolResultContent, UsageInfo, WsBotCapabilities, CONTRACT_VERSION,
    MAGIC_KEY, TOOL_ASSIGN_TASK, TOOL_SEND_TASK_MESSAGE, TOOL_TASK_COMPLETE,
    GROUP_ID_PREFIX, build_session_key,
    apply_sender_display_name, build_chat_inject_frame, build_chat_send_frame,
    build_direct_chat_inject_frame, build_direct_chat_send_frame,
    build_recipient_group_context,
    now_ms, response_directive_for_delivery,
    error_codes,
};

// ---------------------------------------------------------------------------
// Binding Channel Types
// ---------------------------------------------------------------------------

/// Single channel binding information.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct BindingChannel {
    /// Binding key (e.g., sender id for DingTalk).
    pub binding_key: String,
}

/// Bot's channel binding map.
/// Key: channel name (e.g., "antding", "wechat")
/// Value: binding info for that channel
pub type BindingChannels = HashMap<String, BindingChannel>;

// ---------------------------------------------------------------------------
// Skill Type
// ---------------------------------------------------------------------------

/// A structured skill with a name and optional description.
///
/// Replaces the previous `String` representation to allow richer metadata.
/// Backward-compatible: the custom [`deserialize_skills`] function accepts
/// both `["name"]` (legacy) and `[{"name":"...", "description":"..."}]` (new).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Skill {
    /// Skill identifier (e.g., "code_review", "sql_analysis").
    pub name: String,

    /// Human-readable description of what this skill does.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
}

impl Skill {
    /// Create a new skill with only a name.
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            description: None,
        }
    }

    /// Create a new skill with a name and description.
    pub fn with_description(name: impl Into<String>, description: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            description: Some(description.into()),
        }
    }
}

impl From<String> for Skill {
    fn from(name: String) -> Self {
        Self::new(name)
    }
}

impl From<&str> for Skill {
    fn from(name: &str) -> Self {
        Self::new(name)
    }
}

/// Custom deserializer for `Vec<Skill>` that accepts three input formats:
///
/// 1. **String array** (legacy): `["a", "b"]` → `[Skill{name:"a"}, Skill{name:"b"}]`
/// 2. **Object array** (new): `[{"name":"a","description":"..."}]`
/// 3. **Mixed array**: `["a", {"name":"b"}]`
///
/// This enables backward compatibility with existing data stored as `Vec<String>`.
pub fn deserialize_skills<'de, D>(deserializer: D) -> Result<Vec<Skill>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    use serde::de;

    struct SkillsVisitor;

    impl<'de> de::Visitor<'de> for SkillsVisitor {
        type Value = Vec<Skill>;

        fn expecting(&self, formatter: &mut std::fmt::Formatter) -> std::fmt::Result {
            formatter.write_str("a sequence of strings or skill objects")
        }

        fn visit_seq<A>(self, mut seq: A) -> Result<Self::Value, A::Error>
        where
            A: de::SeqAccess<'de>,
        {
            let mut skills = Vec::new();
            while let Some(value) = seq.next_element::<serde_json::Value>()? {
                match value {
                    serde_json::Value::String(s) => {
                        skills.push(Skill::new(s));
                    }
                    serde_json::Value::Object(_) => {
                        let skill: Skill = serde_json::from_value(value)
                            .map_err(de::Error::custom)?;
                        skills.push(skill);
                    }
                    other => {
                        return Err(de::Error::custom(format!(
                            "expected string or object in skills array, got {}",
                            other
                        )));
                    }
                }
            }
            Ok(skills)
        }
    }

    deserializer.deserialize_seq(SkillsVisitor)
}
