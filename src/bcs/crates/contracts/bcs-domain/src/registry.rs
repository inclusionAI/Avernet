//! Registry / bot / capability pure domain types.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::actor::{ActorKind, ActorStatus};

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
/// Backward-compatible: a custom deserializer (kept in `service-api::core::registry`)
/// accepts both `["name"]` (legacy) and `[{"name":"...", "description":"..."}]` (new).
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
    /// Supports both legacy string format and new structured format via
    /// `crate::registry::deserialize_skills` (re-exported by
    /// `service-api::core::registry` for backward compatibility).
    #[serde(default, deserialize_with = "crate::registry::deserialize_skills")]
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

    /// AI安全网关agent_code，用于消息路由时的安全检查。
    /// 从HTTP Header `x-agentclaw-agent-code` 读取，可选字段。
    /// SECURITY: 此字段敏感，不应序列化到客户端，仅通过专用接口在服务端内部访问。
    #[serde(default, skip_serializing, skip_serializing_if = "Option::is_none")]
    pub agent_code: Option<String>,

    /// AI安全网关授权token，用于安全网关请求的`AUTHORIZATION` header。
    /// 从HTTP Header `AUTHORIZATION` 读取，可选字段。
    /// SECURITY: 此字段敏感，不应序列化到客户端，仅通过专用接口在服务端内部访问。
    #[serde(default, skip_serializing, skip_serializing_if = "Option::is_none")]
    pub agent_token: Option<String>,
}

fn default_visibility() -> String {
    "protected".to_string()
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
                        let skill: Skill =
                            serde_json::from_value(value).map_err(de::Error::custom)?;
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

/// A registered bot.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegisteredBot {
    /// Bot unique identifier (UUID assigned by BCS).
    pub bot_uuid: String,
    /// Bot capabilities for discovery.
    pub capabilities: BotCapabilities,
    /// Dynamic status updated periodically.
    pub dynamic_status: BotDynamicStatus,
    /// Server environment (prod, gray, pre, dev).
    pub env: Option<String>,
    /// User who created this bot (staff_no). Set during onboard, immutable.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_by: Option<String>,
    /// Actor kind (`Bot` or `Human`). Defaults to `Bot` for backward
    /// compatibility with existing serialized records (see Requirement 3.16).
    #[serde(default)]
    pub actor_kind: ActorKind,
    /// Actor-level status (`Online` or `Hidden`). Defaults to `Online`
    /// (see Requirement 3.16).
    #[serde(default)]
    pub status: ActorStatus,
}

/// Sensitive agent credentials for AI Security Gateway.
/// This is a separate structure to avoid exposing sensitive data in regular API responses.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AgentCredentials {
    /// AI Security Gateway agent_code for the bot.
    pub agent_code: Option<String>,
    /// AI Security Gateway agent_token (authorization token) for the bot.
    pub agent_token: Option<String>,
}

/// Core bot connection request.
#[derive(Debug, Clone, Default)]
pub struct BotConnectParams {
    pub token: Option<String>,
    pub bot_id: Option<String>,
    pub protocol_version: Option<u32>,
    pub client_kind: Option<String>,
}

/// Connection kind for bot registration.
#[derive(Debug, Clone)]
pub enum ConnectionKind {
    /// Long-lived streaming connection.
    Streaming,
    /// HTTP connection (no persistent channel).
    Http,
}

/// Result of bot connection operation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotConnectResult {
    /// Whether this is a new bot (needs onboarding) or reconnection.
    pub is_new: bool,
    /// Bot's unique identifier.
    pub bot_uuid: String,
    /// Session token for authentication.
    pub token: String,
}
