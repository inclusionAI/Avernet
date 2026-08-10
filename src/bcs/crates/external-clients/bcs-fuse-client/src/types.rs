//! Request/response types for the bcsfuse API.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

// ============================================================================
// Worker Sync Types
// ============================================================================

/// Skill set for worker profiles.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillSet {
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
}

/// Atomic sync request: create worker + set online + upsert profile in one call.
///
/// Sent to `POST /v1/workers/{id}/sync`.
#[derive(Debug, Clone, Serialize)]
pub struct SyncWorkerRequest {
    // Worker fields
    #[serde(rename = "type")]
    pub worker_type: String,
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(default)]
    pub responsibilities: Vec<String>,
    #[serde(default)]
    pub domains: Vec<String>,
    #[serde(default)]
    pub capabilities: Vec<serde_json::Value>,
    #[serde(default)]
    pub skills: Vec<serde_json::Value>,
    pub availability: String,
    pub trust_level: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub profile_key: Option<String>,

    // Profile fields (inlined)
    pub profile: SyncProfileData,
}

/// Profile data within a sync request.
#[derive(Debug, Clone, Serialize)]
pub struct SyncProfileData {
    pub profile_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub display_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub soul_md: Option<String>,
    #[serde(default)]
    pub contents: HashMap<String, String>,
    #[serde(default)]
    pub skill_sets: Vec<SkillSet>,
    #[serde(default)]
    pub activate: bool,
}

/// Response from the atomic sync endpoint `POST /v1/workers/{id}/sync`.
#[derive(Debug, Clone, Deserialize)]
pub struct SyncWorkerResponse {
    pub worker_id: String,
    /// Whether the worker was newly created (false = updated).
    #[serde(default)]
    pub created: bool,
    pub runtime_state: Option<String>,
    pub profile_id: Option<String>,
    /// Whether the profile was successfully activated.
    #[serde(default)]
    pub profile_activated: bool,
}

// ============================================================================
// Fusion Types
// ============================================================================

/// Fusion request sent to bcsfuse.
#[derive(Debug, Clone, Serialize)]
pub struct FuseRequest {
    pub question: String,
    /// Participant IDs in `{worker_id}:{profile_id}` format.
    pub participants: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub driver_bot_id: Option<String>,
    /// Fusion mode: "agent" (G1), "conflict_alignment" (G2), "expert_diagnosis" (G5).
    /// Defaults to "agent" if not specified.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fusion_mode: Option<String>,
}

/// Fusion response from bcsfuse.
#[derive(Debug, Clone, Deserialize)]
pub struct FuseResponse {
    pub group_id: String,
    pub fusion_mode: String,
    /// Fusion unique ID for tracking.
    pub fusion_id: Option<String>,
    #[serde(default)]
    pub perspectives: Vec<FusePerspective>,
    pub recommendation: Option<FuseRecommendation>,
    #[serde(default)]
    pub conflicts: Vec<FuseConflict>,
    /// BCSFuse returns this field as `alignments`.
    #[serde(default, alias = "alignments")]
    pub alignment_points: Vec<serde_json::Value>,
    #[serde(default)]
    pub key_insights: Vec<String>,
    // G2-specific fields
    /// G2 conflict conclusion.
    pub conclusion: Option<serde_json::Value>,
    /// G2 structured conflict analysis (V2).
    pub structured_conflict_analysis: Option<serde_json::Value>,
    /// G2 analysis source identifier: "llm", "v2", or "legacy".
    pub analysis_source: Option<String>,
    // G5-specific fields
    pub risk_assessment: Option<RiskAssessment>,
    #[serde(default)]
    pub critical_issues: Vec<CriticalIssue>,
    #[serde(default)]
    pub recommendations: Vec<ExpertRecommendation>,
    #[serde(default)]
    pub go_live_conditions: Vec<String>,
    pub summary: Option<String>,
    /// G5 structured risk assessment (V2).
    pub structured_risk: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct FusePerspective {
    pub participant_id: String,
    /// Display name — bcsfuse may not return this field.
    #[serde(default)]
    pub name: Option<String>,
    pub emoji: Option<String>,
    /// BCSFuse returns this field as `perspective`.
    #[serde(alias = "perspective")]
    pub summary: String,
    pub key_points: Option<Vec<String>>,
    pub concerns: Option<Vec<String>>,
    /// Role in the fusion (e.g., "driver", "consultant", "observer", "expert").
    pub role: Option<String>,
    /// Confidence score (0.0 - 1.0).
    pub confidence: Option<f64>,
    /// Perspective status (e.g., "completed", "timed_out", "failed", "skipped").
    pub status: Option<String>,
    /// Participant type (e.g., "bot", "human", "system").
    pub participant_type: Option<String>,
    /// Evidence supporting the perspective summary.
    #[serde(default)]
    pub evidence: Option<Vec<String>>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct FuseConflict {
    pub parties: Vec<String>,
    pub issue: String,
    pub positions: Vec<String>,
    pub severity: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct FuseRecommendation {
    pub summary: String,
    pub decision: Option<String>,
    pub risks: Option<Vec<String>>,
    pub next_actions: Option<Vec<String>>,
}

// ============================================================================
// G5-Specific Types
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RiskAssessment {
    pub overall_risk_level: String,
    pub risk_score: Option<f64>,
    #[serde(default)]
    pub risk_factors: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CriticalIssue {
    pub title: String,
    pub severity: String,
    pub description: String,
    pub raised_by: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExpertRecommendation {
    pub title: String,
    pub description: String,
    pub priority: Option<String>,
    pub recommended_by: Option<String>,
}

// ============================================================================
// Recommend Types
// ============================================================================

/// Recommend workers request sent to bcsfuse.
///
/// Sent to `POST /api/v1/recommend`.
#[derive(Debug, Clone, Serialize)]
pub struct RecommendWorkersRequest {
    pub question: String,
    /// Maximum number of candidates to return.
    #[serde(rename = "topK")]
    pub top_k: u32,
    /// Minimum relevance score threshold.
    pub min_score: f64,
}

/// Recommend workers response from bcsfuse.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecommendWorkersResponse {
    /// Suggested driver bot ID.
    #[serde(default)]
    pub driver_bot_id: Option<String>,
    /// Ranked list of recommended workers.
    #[serde(default)]
    pub recommendations: Vec<RecommendedWorker>,
}

/// A single recommended worker entry.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecommendedWorker {
    /// Profile key (e.g., "default:364836:default").
    pub profile_key: String,
    /// Worker ID — this is the bot_uuid, used directly without parsing.
    pub worker_id: String,
    /// Relevance score.
    pub score: f64,
    /// Structured reasons for the recommendation (fragments, scores, etc.).
    #[serde(default)]
    pub reasons: Vec<serde_json::Value>,
    /// Short profile description.
    #[serde(default)]
    pub short_profile: String,
}

// ============================================================================
// Batch Worker Query Types
// ============================================================================

/// Batch query workers request.
///
/// Sent to `POST /v1/workers/batch`.
#[derive(Debug, Clone, Serialize)]
pub struct BatchWorkersRequest {
    pub worker_ids: Vec<String>,
}

/// Response from batch query endpoint.
#[derive(Debug, Clone, Deserialize)]
pub struct BatchWorkersResponse {
    pub success: bool,
    #[serde(default)]
    pub data: HashMap<String, BatchWorkerInfo>,
    #[serde(default)]
    pub not_found_ids: Vec<String>,
}

/// Individual worker info in batch response.
#[derive(Debug, Clone, Deserialize)]
pub struct BatchWorkerInfo {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub runtime_state: Option<String>,
    #[serde(default)]
    pub availability: Option<String>,
    #[serde(default)]
    pub profile_tags: HashMap<String, String>,
}
