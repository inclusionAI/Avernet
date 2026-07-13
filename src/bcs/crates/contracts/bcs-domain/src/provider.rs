use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderAuthMode {
    StaticBearer,
    #[serde(rename = "agentpass")]
    AgentPass,
    ProviderAdmin,
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mcp_server: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mcporter_command: Option<String>,
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
            mcp_server: None,
            mcporter_command: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CoordinationSurface {
    pub mode: CoordinationMode,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mcp_server: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mcporter_command: Option<String>,
}

impl CoordinationSurface {
    pub fn legacy_upstream() -> Self {
        Self {
            mode: CoordinationMode::LegacyUpstream,
            mcp_server: None,
            mcporter_command: None,
        }
    }

    pub fn native_tool() -> Self {
        Self {
            mode: CoordinationMode::NativeTool,
            mcp_server: None,
            mcporter_command: None,
        }
    }
}

impl From<ProviderCoordinationConfig> for CoordinationSurface {
    fn from(config: ProviderCoordinationConfig) -> Self {
        Self {
            mode: config.mode,
            mcp_server: config.mcp_server,
            mcporter_command: config.mcporter_command,
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
