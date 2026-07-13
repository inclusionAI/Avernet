use serde::{Deserialize, Serialize};

use crate::{BindingChannels, Skill, deserialize_skills};

/// Engine type for the bot.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EngineType {
    /// BCS plugin engine.
    BcsPlugin,
    /// Moltis engine.
    Moltis,
    /// OpenClaw engine.
    OpenClaw,
}

/// Bot capability information for discovery.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct BotCapabilities {
    /// Bot display name.
    #[serde(default)]
    pub name: Option<String>,

    /// Brief description of what this bot does (static, from config).
    #[serde(default)]
    pub summary: Option<String>,

    /// Domain/specialty tags (e.g., ["database", "mysql", "dba"]).
    #[serde(default)]
    pub domains: Vec<String>,

    /// Skills this bot has (e.g., code_review, sql_analysis).
    /// Supports both legacy string format and new structured format via [`deserialize_skills`].
    #[serde(default, deserialize_with = "deserialize_skills")]
    pub skills: Vec<Skill>,

    /// Access scopes this bot has (e.g., ["production_db", "logs"]).
    #[serde(default)]
    pub scopes: Vec<String>,

    /// Channel bindings for message routing.
    /// Key: channel name (e.g., "antding", "wechat")
    /// Used to route external channel messages to this bot.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub binding_channels: Option<BindingChannels>,

    /// DEPRECATED: Use `visibility` field instead. Hidden filtering has been removed in Rev-4.
    /// This field is retained only for backward compatibility with old serialized data.
    #[serde(default)]
    pub hidden: bool,

    /// Bot visibility for collaboration access control.
    /// "public" = open collaboration, "protected" = friends only, "private" = no collaboration.
    /// Defaults to "protected" when not specified.
    #[serde(default = "default_visibility")]
    pub visibility: String,
}

fn default_visibility() -> String {
    "protected".to_string()
}

/// Dynamic bot status for real-time discovery.
/// This is updated periodically (less frequently than heartbeat).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct BotDynamicStatus {
    /// Current status (e.g., "idle", "busy", "offline").
    #[serde(default)]
    pub status: String,

    /// Dynamic summary of what the bot is currently doing or can help with.
    /// Updated periodically by the bot itself.
    #[serde(default)]
    pub dynamic_summary: Option<String>,

    /// Current load/capacity (0.0 = idle, 1.0 = fully loaded).
    #[serde(default)]
    pub load: Option<f32>,

    /// Timestamp of the last status update.
    #[serde(default)]
    pub updated_at: Option<u64>,
}

/// Response DTO for the effective online state used in
/// `/actors/search`, `/actors/list`, `/bots/my`, `/bots/query`, and `GET /bots/{id}` responses.
/// Contains only the computed runtime status ("active" or "offline"),
/// distinct from `BotDynamicStatus` which is the full heartbeat payload.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct DynamicStatusResponse {
    /// Effective online state: "active" (WS connected + ActorStatus::Online)
    /// or "offline" (WS disconnected or ActorStatus::Hidden).
    #[serde(default)]
    pub status: String,
}

/// Bot info from BCS.
/// All communication must go through BCS endpoints.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct BotInfo {
    /// Bot UUID (unique identifier assigned by BCS).
    pub bot_uuid: String,
    /// Bot display name (from streaming registration).
    #[serde(default)]
    pub bot_name: Option<String>,
    /// Engine type for this bot (BcsPlugin, Moltis, OpenClaw).
    #[serde(default)]
    pub engine_type: Option<EngineType>,
    #[serde(default)]
    pub capabilities: BotCapabilities,
}

/// Request to join the BCS network.
#[derive(Debug, Serialize, Deserialize)]
pub struct JoinRequest {
    pub bot_id: String,
    pub bot_name: Option<String>,
    pub engine_type: Option<EngineType>,
    pub capabilities: Option<BotCapabilities>,
}

/// Response from bot join.
#[derive(Debug, Deserialize)]
pub struct JoinResponse {
    pub joined: bool,
    pub bot_id: String,
    /// Bot display name (from registration).
    #[serde(default)]
    pub bot_name: Option<String>,
    /// Engine type for this bot.
    #[serde(default)]
    pub engine_type: Option<EngineType>,
    pub capabilities: BotCapabilities,
}

/// Request to update bot status.
#[derive(Debug, Serialize, Deserialize)]
pub struct UpdateStatusRequest {
    pub bot_uuid: String,
    pub status: BotDynamicStatus,
}

/// Response from status update.
#[derive(Debug, Deserialize)]
pub struct UpdateStatusResponse {
    pub updated: bool,
    pub bot_uuid: String,
    pub status: BotDynamicStatus,
}

/// Response from bot leave.
#[derive(Debug, Deserialize)]
pub struct LeaveResponse {
    pub left: bool,
    pub bot_uuid: String,
}

/// Response from bot discovery.
#[derive(Debug, Deserialize)]
pub struct DiscoverBotsResponse {
    pub bots: Vec<BotInfo>,
    pub count: usize,
}

/// Extended response from bot discovery with visibility and friendship info.
#[derive(Debug, Deserialize)]
pub struct DiscoverBotsExtendedResponse {
    pub bots: Vec<DiscoverBotEntry>,
    pub count: usize,
}

/// Extended bot entry in discover results with visibility and friendship info.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DiscoverBotEntry {
    /// Bot UUID.
    pub bot_uuid: String,
    /// Bot capabilities.
    pub capabilities: BotCapabilities,
    /// Bot visibility ("public" or "protected").
    pub visibility: String,
    /// Whether this bot is a friend of the requesting bot.
    /// None when `collaborate_bot` query parameter is not provided.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_friend: Option<bool>,
    /// AI security gateway agent_code, when available.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_code: Option<String>,
    /// Provider metadata for provider-managed bots.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provider_info: Option<DiscoverBotProviderInfo>,
    /// Organization membership metadata when discovery is organization-scoped.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub organization_member: Option<DiscoverBotOrganizationMember>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DiscoverBotOrganizationMember {
    pub organization_code: String,
    pub role: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DiscoverBotProviderInfo {
    pub provider_id: String,
    pub provider_name: String,
}

/// Request body for POST /bots/query — batch query bots by UUIDs.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QueryBotsRequest {
    /// List of bot UUIDs to query.
    pub bot_uuids: Vec<String>,
}

/// Bot entry in batch query results with visibility info.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QueryBotEntry {
    /// Bot UUID.
    pub bot_uuid: String,
    /// Bot capabilities.
    pub capabilities: BotCapabilities,
    /// Bot visibility ("public", "protected", or "private").
    pub visibility: String,
    /// Bot lifecycle status: `"online"` or `"hidden"` — the raw
    /// `ActorStatus` value, NOT the heartbeat-derived effective online
    /// state. Optional + `serde(default)` for backward compatibility with
    /// servers that pre-date D-E.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    /// Actor kind: `"bot"` or `"human"`. Added in Rev-1/D-H.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub actor_kind: Option<String>,
    /// Runtime effective status. Added in Rev-1/D-H.
    /// Contains `status` sub-field with value `"active"` or `"offline"`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dynamic_status: Option<DynamicStatusResponse>,
}

/// Request body for setting bot visibility.
#[derive(Debug, Serialize, Deserialize)]
pub struct SetVisibilityRequest {
    /// Visibility value: "public" or "protected".
    pub visibility: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bot_capabilities_wire_shape_excludes_agent_credentials() {
        let capabilities: BotCapabilities = serde_json::from_value(serde_json::json!({
            "name": "bot",
            "summary": "summary",
            "skills": ["sql"],
            "agent_code": "server-code",
            "agent_token": "server-token"
        }))
        .expect("capabilities should deserialize");

        let serialized = serde_json::to_value(capabilities).expect("capabilities should serialize");

        assert!(serialized.get("agent_code").is_none());
        assert!(serialized.get("agent_token").is_none());
    }
}
