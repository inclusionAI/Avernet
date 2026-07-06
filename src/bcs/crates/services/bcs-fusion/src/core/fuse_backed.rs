//! BCSFuse-backed fusion service implementation.

use std::path::PathBuf;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_config_api::BcsFuseConfig;
use bcs_fuse_client::{FuseClient, FuseClientError, FuseRequest, FuseResponse};
use bcs_service_api::{
    ContextBotSummary, ContextConflict, ContextConflictPosition, ContextFusionRequest,
    ContextFusionResponse, ContextParticipantPerspective, FusionCoreService, ServiceError,
    ServiceResult,
};

use super::local::load_bot_context;

/// `FusionCoreService` implementation that delegates fusion to bcsfuse via HTTP.
pub struct FuseBackedFusionService {
    client: Arc<FuseClient>,
    config: BcsFuseConfig,
    bots_base_dir: PathBuf,
}

impl FuseBackedFusionService {
    /// Create a new `FuseBackedFusionService`.
    pub fn new(
        config: &BcsFuseConfig,
        bots_base_dir: impl Into<PathBuf>,
    ) -> Result<Self, FuseClientError> {
        let client = Arc::new(FuseClient::new(config)?);
        Ok(Self {
            client,
            config: config.clone(),
            bots_base_dir: bots_base_dir.into(),
        })
    }

    /// Get a shared reference to the underlying `FuseClient`.
    pub fn client(&self) -> Arc<FuseClient> {
        Arc::clone(&self.client)
    }
}

#[async_trait]
impl FusionCoreService for FuseBackedFusionService {
    async fn fuse(&self, request: &ContextFusionRequest) -> ServiceResult<ContextFusionResponse> {
        let group_id = request
            .session_id
            .as_ref()
            .map(|sid| {
                if sid.starts_with("grp-") {
                    sid.clone()
                } else {
                    format!("grp-{}", sid)
                }
            })
            .unwrap_or_else(|| format!("grp-{}", uuid::Uuid::new_v4()));

        let profile_id = &self.config.profile_id;
        let participants: Vec<String> = request
            .participants
            .iter()
            .map(|bot_id| build_participant_id(bot_id, profile_id))
            .collect();

        let fuse_req = FuseRequest {
            question: request.question.clone(),
            participants,
            driver_bot_id: request
                .participants
                .first()
                .map(|bot_id| build_participant_id(bot_id, profile_id)),
            fusion_mode: request.fusion_mode.clone(),
        };

        let response = self
            .client
            .fuse(&group_id, fuse_req)
            .await
            .map_err(|e| ServiceError::InternalError(format!("bcsfuse error: {}", e)))?;

        Ok(transform_response(response))
    }

    fn load_bot_context(&self, bot_id: &str) -> ServiceResult<ContextBotSummary> {
        load_bot_context(&self.bots_base_dir, bot_id)
    }

    fn load_bot_contexts(&self, bot_ids: &[String]) -> Vec<ContextBotSummary> {
        bot_ids
            .iter()
            .filter_map(|id| load_bot_context(&self.bots_base_dir, id).ok())
            .collect()
    }
}

/// Backwards-compatible name used by older bootstrap code.
pub type FuseClientService = FuseBackedFusionService;

/// Normalize a bot/worker ID to bcsfuse's `wrk_` prefixed format.
pub fn normalize_worker_id(bot_id: &str) -> String {
    if bot_id.starts_with("wrk_") {
        bot_id.to_string()
    } else {
        format!("wrk_{}", bot_id)
    }
}

/// Build bcsfuse participant_id from bot_id and profile_id.
pub fn build_participant_id(bot_id: &str, profile_id: &str) -> String {
    format!("{}:{}", bot_id, profile_id)
}

fn extract_display_name(participant_id: &str) -> String {
    let without_profile = participant_id.split(':').next().unwrap_or(participant_id);
    without_profile
        .strip_prefix("wrk_")
        .unwrap_or(without_profile)
        .to_string()
}

fn transform_response(resp: FuseResponse) -> ContextFusionResponse {
    let mut extra = serde_json::Map::new();
    extra.insert(
        "fusion_mode".into(),
        serde_json::Value::String(resp.fusion_mode),
    );
    if let Some(ref fusion_id) = resp.fusion_id {
        extra.insert(
            "fusion_id".into(),
            serde_json::Value::String(fusion_id.clone()),
        );
    }
    if let Some(ref conclusion) = resp.conclusion {
        extra.insert("conclusion".into(), conclusion.clone());
    }
    if let Some(ref analysis) = resp.structured_conflict_analysis {
        extra.insert("structured_conflict_analysis".into(), analysis.clone());
    }
    if let Some(ref source) = resp.analysis_source {
        extra.insert(
            "analysis_source".into(),
            serde_json::Value::String(source.clone()),
        );
    }
    if let Some(ref risk) = resp.risk_assessment {
        if let Ok(val) = serde_json::to_value(risk) {
            extra.insert("risk_assessment".into(), val);
        }
    }
    if !resp.critical_issues.is_empty() {
        if let Ok(val) = serde_json::to_value(&resp.critical_issues) {
            extra.insert("critical_issues".into(), val);
        }
    }
    if !resp.recommendations.is_empty() {
        if let Ok(val) = serde_json::to_value(&resp.recommendations) {
            extra.insert("recommendations".into(), val);
        }
    }
    if !resp.go_live_conditions.is_empty() {
        if let Ok(val) = serde_json::to_value(&resp.go_live_conditions) {
            extra.insert("go_live_conditions".into(), val);
        }
    }
    if let Some(ref summary) = resp.summary {
        extra.insert("summary".into(), serde_json::Value::String(summary.clone()));
    }
    if let Some(ref structured_risk) = resp.structured_risk {
        extra.insert("structured_risk".into(), structured_risk.clone());
    }

    ContextFusionResponse {
        perspectives: resp
            .perspectives
            .into_iter()
            .map(|p| ContextParticipantPerspective {
                bot_uuid: p.participant_id.clone(),
                name: p
                    .name
                    .unwrap_or_else(|| extract_display_name(&p.participant_id)),
                emoji: p.emoji.unwrap_or_else(|| "🤖".to_string()),
                summary: p.summary,
                key_points: p.key_points.unwrap_or_default(),
                concerns: p.concerns.unwrap_or_default(),
                role: p.role,
                confidence: p.confidence,
                status: p.status,
                participant_type: p.participant_type,
                evidence: p.evidence,
            })
            .collect(),
        conflicts: resp
            .conflicts
            .into_iter()
            .map(|c| {
                let positions: Vec<ContextConflictPosition> = c
                    .parties
                    .iter()
                    .zip(c.positions.iter())
                    .map(|(party, pos)| ContextConflictPosition {
                        bot_uuid: party.clone(),
                        view: pos.clone(),
                    })
                    .collect();
                ContextConflict {
                    parties: c.parties,
                    issue: c.issue,
                    positions,
                    severity: c.severity,
                }
            })
            .collect(),
        alignment_points: resp
            .alignment_points
            .into_iter()
            .filter_map(|v| match v {
                serde_json::Value::String(s) => Some(s),
                serde_json::Value::Object(ref obj) => obj
                    .get("summary")
                    .and_then(|s| s.as_str())
                    .map(|s| s.to_string()),
                _ => None,
            })
            .collect(),
        recommendation: resp.recommendation.map(|r| r.summary),
        key_insights: resp.key_insights,
        extra: if extra.is_empty() {
            None
        } else {
            Some(serde_json::Value::Object(extra))
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_fuse_client::{FuseConflict, FusePerspective, FuseRecommendation};

    #[test]
    fn build_participant_id_preserves_existing_prefix() {
        assert_eq!(
            build_participant_id("wrk_bot_abc", "prod"),
            "wrk_bot_abc:prod"
        );
    }

    #[test]
    fn transform_response_uses_display_name_fallback() {
        let resp = FuseResponse {
            group_id: "grp-1".into(),
            fusion_mode: "agent".into(),
            fusion_id: Some("fus-1".into()),
            perspectives: vec![FusePerspective {
                participant_id: "wrk_bot1:default".into(),
                name: None,
                emoji: None,
                summary: "summary".into(),
                key_points: None,
                concerns: None,
                role: None,
                confidence: None,
                status: None,
                participant_type: None,
                evidence: None,
            }],
            conflicts: vec![],
            alignment_points: vec![serde_json::json!({"summary": "aligned"})],
            recommendation: Some(FuseRecommendation {
                summary: "go".into(),
                decision: None,
                risks: None,
                next_actions: None,
            }),
            key_insights: vec![],
            conclusion: None,
            structured_conflict_analysis: None,
            analysis_source: None,
            risk_assessment: None,
            critical_issues: vec![],
            recommendations: vec![],
            go_live_conditions: vec![],
            summary: None,
            structured_risk: None,
        };

        let result = transform_response(resp);
        assert_eq!(result.perspectives[0].name, "bot1");
        assert_eq!(result.alignment_points, vec!["aligned"]);
        assert_eq!(result.recommendation, Some("go".to_string()));
    }

    #[test]
    fn conflict_positions_are_preserved() {
        let resp = FuseResponse {
            group_id: "grp-1".into(),
            fusion_mode: "agent".into(),
            fusion_id: None,
            perspectives: vec![],
            conflicts: vec![FuseConflict {
                parties: vec!["a".into(), "b".into()],
                issue: "issue".into(),
                positions: vec!["left".into(), "right".into()],
                severity: Some("low".into()),
            }],
            alignment_points: vec![],
            recommendation: None,
            key_insights: vec![],
            conclusion: None,
            structured_conflict_analysis: None,
            analysis_source: None,
            risk_assessment: None,
            critical_issues: vec![],
            recommendations: vec![],
            go_live_conditions: vec![],
            summary: None,
            structured_risk: None,
        };

        let result = transform_response(resp);
        assert_eq!(result.conflicts[0].positions.len(), 2);
        assert_eq!(result.conflicts[0].positions[0].bot_uuid, "a");
    }
}
