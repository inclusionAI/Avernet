use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

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
    ProviderAdmin,
}

/// How a provider-registered bot connects to BCS. `Gateway` (default) writes a
/// provider_binding row (HTTP webhook downlink); `Plugin` skips the binding so
/// the bot connects over WebSocket through a BCN plugin.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum ProviderBotConnectionMode {
    #[default]
    Gateway,
    Plugin,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderRecord {
    pub provider_id: String,
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
}
