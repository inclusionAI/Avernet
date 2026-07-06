//! Context fusion request / response pure domain types.

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Summary of a bot's local context for core fusion implementations.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextBotSummary {
    pub bot_uuid: String,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub emoji: Option<String>,
    #[serde(default)]
    pub identity: Option<String>,
    #[serde(default)]
    pub soul: Option<String>,
    #[serde(default)]
    pub rules: Option<String>,
    #[serde(default)]
    pub memory: Option<String>,
}

impl ContextBotSummary {
    pub fn display_name(&self) -> &str {
        self.name.as_deref().unwrap_or(&self.bot_uuid)
    }

    pub fn display_emoji(&self) -> &str {
        self.emoji.as_deref().unwrap_or("🤖")
    }
}

/// Core request for fusing multiple bot contexts.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ContextFusionRequest {
    pub question: String,
    pub participants: Vec<String>,
    #[serde(default)]
    pub focus: Option<String>,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub fusion_mode: Option<String>,
}

/// Core response from a context fusion implementation.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ContextFusionResponse {
    pub perspectives: Vec<ContextParticipantPerspective>,
    #[serde(default)]
    pub conflicts: Vec<ContextConflict>,
    #[serde(default)]
    pub alignment_points: Vec<String>,
    #[serde(default)]
    pub recommendation: Option<String>,
    #[serde(default)]
    pub key_insights: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub extra: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextParticipantPerspective {
    pub bot_uuid: String,
    pub name: String,
    pub emoji: String,
    pub summary: String,
    #[serde(default)]
    pub key_points: Vec<String>,
    #[serde(default)]
    pub concerns: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub role: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub participant_type: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub evidence: Option<Vec<String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextConflict {
    pub parties: Vec<String>,
    pub issue: String,
    pub positions: Vec<ContextConflictPosition>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub severity: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextConflictPosition {
    pub bot_uuid: String,
    pub view: String,
}
