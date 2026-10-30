use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

/// Provider-owned collaboration boundary, independent of visibility and friendship.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderCollaborationScope {
    #[default]
    Standard,
    SameOwner,
}

impl ProviderCollaborationScope {
    /// Missing configuration preserves legacy behavior; malformed policy fails closed.
    pub fn from_config(config: &str) -> Result<Self, serde_json::Error> {
        let value: serde_json::Map<String, serde_json::Value> = serde_json::from_str(config)?;
        match value.get("collaboration_scope") {
            None => Ok(Self::Standard),
            Some(scope) => serde_json::from_value(scope.clone()),
        }
    }
}

fn default_worker_send_task_message_enabled() -> bool {
    true
}

fn is_true(value: &bool) -> bool {
    *value
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderAuthMode {
    StaticBearer,
    #[serde(rename = "agentpass")]
    AgentPass,
    #[serde(rename = "agentpass-subbots")]
    AgentPassSubbots,
    ProviderAdmin,
}

/// How a provider-registered bot connects to BCS. `Gateway` (default) writes a
/// provider_binding row (HTTP webhook downlink); `Plugin` skips the binding so
/// the bot connects over WebSocket through a BCN plugin or bridge.
/// Shared by Provider administration, registration tokens and Bot persistence.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum ProviderBotConnectionMode {
    #[default]
    Gateway,
    Plugin,
}

impl ProviderBotConnectionMode {
    /// Canonical wire and database value; independent of entry-point defaults.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Gateway => "gateway",
            Self::Plugin => "plugin",
        }
    }
}

impl std::str::FromStr for ProviderBotConnectionMode {
    type Err = &'static str;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "gateway" => Ok(Self::Gateway),
            "plugin" => Ok(Self::Plugin),
            _ => Err("invalid Bot connection mode"),
        }
    }
}

/// Provider downlink protocol selecting the A2A v1.0 JSON-RPC binding: BCS
/// calls the bot as a standard A2A agent instead of the BCN webhook.
pub const PROVIDER_PROTOCOL_A2A: &str = "a2a/1.0";

/// Canonical downlink `protocol_version`: `1.0` (default), `2.0` or
/// [`PROVIDER_PROTOCOL_A2A`] (`a2a` is accepted as an alias).
pub fn normalize_provider_protocol_version(value: Option<&str>) -> Option<&'static str> {
    match value.map(str::trim) {
        None | Some("") | Some("1.0") => Some("1.0"),
        Some("2.0") => Some("2.0"),
        Some("a2a") | Some(PROVIDER_PROTOCOL_A2A) => Some(PROVIDER_PROTOCOL_A2A),
        Some(_) => None,
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderRecord {
    pub provider_id: String,
    /// Optional environment-unique discovery key; legacy records have no slug.
    #[serde(default)]
    pub slug: Option<String>,
    pub name: String,
    /// JSON string stored in MEDIUMTEXT. Core service owns schema validation.
    pub config: String,
    pub created_by: String,
    /// JSON list string of provider owners.
    pub owners: String,
    pub disabled: bool,
    pub created_at: u64,
    pub updated_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderCredential {
    pub provider_id: String,
    pub credential_kind: String,
    pub secret_value: String,
    pub disabled: bool,
    pub created_at: u64,
    pub updated_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderBotBinding {
    pub bot_uuid: String,
    /// None inherits the Provider default; this is routing configuration, not a capability.
    #[serde(default)]
    pub webhook_url: Option<String>,
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub disabled: bool,
    pub created_at: u64,
    pub updated_at: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CoordinationMode {
    McporterMcp,
    NativeMcp,
    NativeTool,
    Disabled,
    LegacyUpstream,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderCoordinationConfig {
    pub mode: CoordinationMode,
    /// Whether manager-worker worker contexts should explain the optional
    /// `bcs_send_task_message` tool.
    #[serde(
        default = "default_worker_send_task_message_enabled",
        skip_serializing_if = "is_true"
    )]
    pub worker_send_task_message_enabled: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mcp_server: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mcporter_command: Option<String>,
    /// Exact provider-emitted MCP tool name to canonical BCS coordination tool.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub tool_name_mapping: BTreeMap<String, String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderOrganizationManagementConfig {
    #[serde(default)]
    pub authorized_manager_provider_ids: Vec<String>,
}

impl ProviderOrganizationManagementConfig {
    pub fn from_provider_config(config: &str) -> Result<Self, serde_json::Error> {
        let value: serde_json::Value = serde_json::from_str(config)?;
        match value.get("organization_management") {
            Some(raw) => serde_json::from_value(raw.clone()),
            None => Ok(Self::default()),
        }
    }
}

impl ProviderCoordinationConfig {
    pub fn disabled() -> Self {
        Self {
            mode: CoordinationMode::Disabled,
            worker_send_task_message_enabled: true,
            mcp_server: None,
            mcporter_command: None,
            tool_name_mapping: BTreeMap::new(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CoordinationSurface {
    pub mode: CoordinationMode,
    /// Effective Provider setting for the optional worker send-message tool
    /// guidance. Non-Provider surfaces use the backward-compatible default.
    #[serde(
        default = "default_worker_send_task_message_enabled",
        skip_serializing_if = "is_true"
    )]
    pub worker_send_task_message_enabled: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mcp_server: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mcporter_command: Option<String>,
    /// Exact provider-emitted MCP tool name to canonical BCS coordination tool.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub tool_name_mapping: BTreeMap<String, String>,
}

impl CoordinationSurface {
    pub fn legacy_upstream() -> Self {
        Self {
            mode: CoordinationMode::LegacyUpstream,
            worker_send_task_message_enabled: true,
            mcp_server: None,
            mcporter_command: None,
            tool_name_mapping: BTreeMap::new(),
        }
    }

    pub fn native_tool() -> Self {
        Self {
            mode: CoordinationMode::NativeTool,
            worker_send_task_message_enabled: true,
            mcp_server: None,
            mcporter_command: None,
            tool_name_mapping: BTreeMap::new(),
        }
    }

    /// Built-in shell MCP adapter. Command and server are code-owned, not wire input.
    pub fn mcporter_mcp_bcs() -> Self {
        Self {
            mode: CoordinationMode::McporterMcp,
            worker_send_task_message_enabled: true,
            mcp_server: Some("bcs".to_string()),
            mcporter_command: Some("mcporter".to_string()),
            tool_name_mapping: BTreeMap::new(),
        }
    }

    /// Built-in, server-owned profile for Bot WebSocket V3 runtimes that mount
    /// the canonical `bcs` MCP server. Exact names are intentionally fixed;
    /// clients cannot expand this allowlist during `bot.connect`.
    pub fn native_mcp_bcs() -> Self {
        Self {
            mode: CoordinationMode::NativeMcp,
            worker_send_task_message_enabled: true,
            mcp_server: Some("bcs".to_string()),
            mcporter_command: None,
            tool_name_mapping: BTreeMap::from([
                (
                    "mcp__bcs__bcs_assign_task".to_string(),
                    "bcs_assign_task".to_string(),
                ),
                (
                    "mcp__bcs__bcs_send_task_message".to_string(),
                    "bcs_send_task_message".to_string(),
                ),
                (
                    "mcp__bcs__bcs_task_complete".to_string(),
                    "bcs_task_complete".to_string(),
                ),
            ]),
        }
    }
}

impl From<ProviderCoordinationConfig> for CoordinationSurface {
    fn from(config: ProviderCoordinationConfig) -> Self {
        Self {
            mode: config.mode,
            worker_send_task_message_enabled: config.worker_send_task_message_enabled,
            mcp_server: config.mcp_server,
            mcporter_command: config.mcporter_command,
            tool_name_mapping: config.tool_name_mapping,
        }
    }
}

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RedactedToken(String);

impl RedactedToken {
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    pub fn expose_secret(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Debug for RedactedToken {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("RedactedToken(***)")
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum BotDeliveryTarget {
    WebSocket {
        bot_id: String,
    },
    HttpProvider {
        bot_id: String,
        provider_id: String,
        provider_bot_ref: String,
        webhook_url: String,
        bcs_to_provider_token: RedactedToken,
        protocol_version: String,
    },
}

impl BotDeliveryTarget {
    pub fn bot_id(&self) -> &str {
        match self {
            Self::WebSocket { bot_id } => bot_id,
            Self::HttpProvider { bot_id, .. } => bot_id,
        }
    }

    pub fn is_http_provider(&self) -> bool {
        matches!(self, Self::HttpProvider { .. })
    }

    /// HTTP Provider whose downlink speaks A2A instead of BCN.
    pub fn is_a2a_provider(&self) -> bool {
        matches!(self, Self::HttpProvider { protocol_version, .. } if protocol_version == PROVIDER_PROTOCOL_A2A)
    }
}

#[cfg(test)]
mod protocol_tests {
    use super::*;

    #[test]
    fn downlink_protocol_versions_normalize_and_select_a2a() {
        for (input, expected) in [(None, Some("1.0")), (Some(" "), Some("1.0")), (Some("1.0"), Some("1.0")),
            (Some("2.0"), Some("2.0")), (Some("a2a"), Some(PROVIDER_PROTOCOL_A2A)), (Some(" a2a/1.0 "), Some(PROVIDER_PROTOCOL_A2A)),
            (Some("3.0"), None), (Some("a2a/0.3"), None)] {
            assert_eq!(normalize_provider_protocol_version(input), expected, "{input:?}");
        }
        let target = |protocol_version: &str| BotDeliveryTarget::HttpProvider { bot_id: "b".into(), provider_id: "p".into(),
            provider_bot_ref: "r".into(), webhook_url: "https://x".into(), bcs_to_provider_token: RedactedToken::new("t"),
            protocol_version: protocol_version.into() };
        assert!(target(PROVIDER_PROTOCOL_A2A).is_a2a_provider());
        assert!(!target("2.0").is_a2a_provider());
        assert!(!BotDeliveryTarget::WebSocket { bot_id: "b".into() }.is_a2a_provider());
    }
}
