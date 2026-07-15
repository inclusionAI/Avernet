use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::{Attachment, Skill, deserialize_skills};

pub const BCN_PROTOCOL_VERSION_HEADER: &str = "X-BCN-Protocol-Version";
pub const BCN_TRANSPORT_HEADER: &str = "X-BCN-Transport";
pub const BCN_MESSAGE_ID_HEADER: &str = "X-BCN-Message-Id";
pub const BCN_TIMESTAMP_HEADER: &str = "X-BCN-Timestamp";
pub const BCN_PROVIDER_ID_HEADER: &str = "X-BCN-Provider-Id";
pub const BCN_EVENT_ID_HEADER: &str = "X-BCN-Event-Id";
pub const BCN_PROVIDER_BOT_REF_HEADER: &str = "X-BCN-Provider-Bot-Ref";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ProviderAuthModeDto {
    StaticBearer,
    #[serde(rename = "agentpass")]
    AgentPass,
    ProviderAdmin,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderAuthDto {
    pub mode: ProviderAuthModeDto,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ProviderCoordinationModeDto {
    McporterMcp,
    NativeMcp,
    NativeTool,
    Disabled,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ProviderCoordinationConfigDto {
    pub mode: ProviderCoordinationModeDto,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mcp_server: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mcporter_command: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct ProviderOrganizationManagementConfigDto {
    #[serde(default)]
    pub authorized_manager_provider_ids: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ProviderCoordinationEventKindDto {
    ToolResult,
    CoordinationIntent,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderCoordinationIntentDto {
    pub v: u64,
    pub tool: String,
    #[serde(default)]
    pub arguments: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderCoordinationEventRequest {
    pub run_id: String,
    pub tool_call_id: String,
    pub kind: ProviderCoordinationEventKindDto,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub result_text: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mcp_server: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub intent: Option<ProviderCoordinationIntentDto>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegisterProviderRequest {
    pub name: String,
    pub webhook_url: String,
    /// Optional provider-level endpoint for terminal organization-admin run
    /// notifications. This is intentionally separate from the bot downlink
    /// webhook URL.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub admin_callback_url: Option<String>,
    pub auth: ProviderAuthDto,
    /// Downlink protocol version: "1.0" (callback, default) or "2.0" (streaming/SSE).
    /// Omitted = "1.0" for backward compatibility.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub protocol_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coordination: Option<ProviderCoordinationConfigDto>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegisterProviderResponse {
    pub provider_id: String,
    pub provider_admin_token: String,
    pub bcs_to_provider_token: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderInfoResponse {
    pub provider_id: String,
    pub name: String,
    pub webhook_url: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub admin_callback_url: Option<String>,
    pub auth_mode: ProviderAuthModeDto,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coordination: Option<ProviderCoordinationConfigDto>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub organization_management: Option<ProviderOrganizationManagementConfigDto>,
    pub disabled: bool,
    pub created_at: u64,
    pub updated_at: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct PatchProviderRequest {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub webhook_url: Option<String>,
    #[serde(default)]
    pub admin_callback_url: Option<String>,
    /// Downlink protocol version ("1.0" | "2.0"). When present, updates the
    /// stored downlink config; controls whether SSE streaming is eligible.
    #[serde(default)]
    pub protocol_version: Option<String>,
    #[serde(default)]
    pub coordination: Option<ProviderCoordinationConfigDto>,
    #[serde(default)]
    pub organization_management: Option<ProviderOrganizationManagementConfigDto>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegisterProviderBotRequest {
    pub name: String,
    #[serde(default)]
    pub summary: Option<String>,
    #[serde(default)]
    pub owners: Vec<String>,
    pub provider_bot_ref: String,
    /// Domains this bot covers (same semantics as `POST /bots/onboard`).
    #[serde(default)]
    pub domains: Vec<String>,
    /// Skills this bot has (same semantics as `POST /bots/onboard`).
    #[serde(default, deserialize_with = "deserialize_skills")]
    pub skills: Vec<Skill>,
    /// Access scopes this bot has (same semantics as `POST /bots/onboard`).
    #[serde(default)]
    pub scopes: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegisterProviderBotResponse {
    pub bot_uuid: String,
    pub provider_id: String,
    pub provider_bot_ref: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bot_runtime_token: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct PatchProviderBotRequest {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub summary: Option<String>,
    #[serde(default)]
    pub domains: Option<Vec<String>>,
    #[serde(default)]
    pub skills: Option<Vec<String>>,
    #[serde(default)]
    pub scopes: Option<Vec<String>>,
    #[serde(default)]
    pub visibility: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderWebhookBotRef {
    pub provider_id: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub provider_bot_ref: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tags: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderWebhookSender {
    pub kind: String,
    pub name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub actor_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderWebhookRequest {
    #[serde(rename = "type")]
    pub frame_type: String,
    pub id: String,
    pub method: String,
    pub session_id: String,
    pub bcn_group_id: String,
    pub to_bot: ProviderWebhookBotRef,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub from: Option<ProviderWebhookSender>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub message: Option<Value>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub attachments: Vec<Attachment>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub before: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub after: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub limit: Option<u64>,
    pub timeout_ms: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub extensions: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderAckResponse {
    pub ok: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub retryable: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderHistoryResponse {
    pub ok: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
    pub messages: Vec<Value>,
    pub has_more: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub next_before: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub next_after: Option<u64>,
}
