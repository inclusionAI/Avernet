use serde::{Deserialize, Serialize};

/// Summary of a bot's context for fusion.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotContextSummary {
    /// Bot unique identifier (UUID).
    pub bot_uuid: String,

    /// Bot display name (from IDENTITY.md).
    #[serde(default)]
    pub name: Option<String>,

    /// Bot emoji (from IDENTITY.md).
    #[serde(default)]
    pub emoji: Option<String>,

    /// Identity content (IDENTITY.md).
    #[serde(default)]
    pub identity: Option<String>,

    /// Soul content (SOUL.md).
    #[serde(default)]
    pub soul: Option<String>,

    /// Rules content (RULES.md).
    #[serde(default)]
    pub rules: Option<String>,

    /// Memory content (MEMORY.md).
    #[serde(default)]
    pub memory: Option<String>,
}

impl BotContextSummary {
    /// Get display name with fallback to bot_id.
    pub fn display_name(&self) -> &str {
        self.name.as_deref().unwrap_or(&self.bot_uuid)
    }

    /// Get emoji with fallback.
    pub fn display_emoji(&self) -> &str {
        self.emoji.as_deref().unwrap_or("🤖")
    }
}

/// Request to fuse contexts from multiple bots.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct FusionRequest {
    /// The question or task to focus the fusion on.
    pub question: String,

    /// Bot IDs whose contexts should be fused.
    pub participants: Vec<String>,

    /// Optional focus area (e.g., "security risks", "timeline conflicts").
    #[serde(default)]
    pub focus: Option<String>,

    /// Session ID for context (optional).
    #[serde(default)]
    pub session_id: Option<String>,

    /// Fusion mode: "agent" (G1), "conflict_alignment" (G2), "expert_diagnosis" (G5).
    /// Defaults to "agent" if not specified.
    #[serde(default)]
    pub fusion_mode: Option<String>,
}

/// Response from context fusion.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct FusionResponse {
    /// Perspectives from each participant.
    pub perspectives: Vec<ParticipantPerspective>,

    /// Identified conflicts between participants.
    #[serde(default)]
    pub conflicts: Vec<Conflict>,

    /// Points of alignment/agreement.
    #[serde(default)]
    pub alignment_points: Vec<String>,

    /// Overall recommendation.
    #[serde(default)]
    pub recommendation: Option<String>,

    /// Key insights (brief summary points).
    #[serde(default)]
    pub key_insights: Vec<String>,

    /// Extra data from fusion providers (e.g., G5-specific fields from bcsfuse).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub extra: Option<serde_json::Value>,
}

/// A participant's perspective in the fusion.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ParticipantPerspective {
    /// Bot UUID.
    pub bot_uuid: String,

    /// Display name.
    pub name: String,

    /// Emoji.
    pub emoji: String,

    /// Summary of this participant's relevant context.
    pub summary: String,

    /// Key points from this participant.
    #[serde(default)]
    pub key_points: Vec<String>,

    /// Concerns or constraints from this participant.
    #[serde(default)]
    pub concerns: Vec<String>,

    /// Role in the fusion (e.g., "driver", "consultant", "observer", "expert").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub role: Option<String>,

    /// Confidence score (0.0 - 1.0).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence: Option<f64>,

    /// Perspective status (e.g., "completed", "timed_out", "failed", "skipped").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,

    /// Participant type (e.g., "bot", "human", "system").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub participant_type: Option<String>,

    /// Evidence supporting the perspective summary.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub evidence: Option<Vec<String>>,
}

/// A conflict between participants.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Conflict {
    /// The conflicting parties.
    pub parties: Vec<String>,

    /// Description of the conflict.
    pub issue: String,

    /// Details of each party's position.
    pub positions: Vec<ConflictPosition>,

    /// Severity level (e.g., "low", "medium", "high", "critical").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub severity: Option<String>,
}

/// A party's position in a conflict.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConflictPosition {
    /// Bot UUID.
    pub bot_uuid: String,

    /// Their view/stance.
    pub view: String,
}
