use std::sync::Arc;

use async_trait::async_trait;
use bcs_fuse_client::{CardSearchRequest, FuseClient};
use bcs_service_api::core::{
    WorkerProfile, WorkerProfileCoreService, WorkerRecommendCommand, WorkerRecommendResult,
    WorkerRecommendation,
};
use bcs_service_api::{ServiceError, ServiceResult};

/// Adapt Fuse Card discovery to the existing BCS worker-profile contract.
pub struct FuseCardWorkerProfileService {
    client: Arc<FuseClient>,
    profile_id: String,
}

impl FuseCardWorkerProfileService {
    pub fn new(client: Arc<FuseClient>, profile_id: String) -> Self { Self { client, profile_id } }
}

#[async_trait]
impl WorkerProfileCoreService for FuseCardWorkerProfileService {
    async fn recommend_workers(
        &self, command: WorkerRecommendCommand,
    ) -> ServiceResult<WorkerRecommendResult> {
        let response = self.client.search_cards(&CardSearchRequest {
            query: command.query,
            limit: command.top_k.max(1),
            filters: [("resource_type".into(), vec!["agent".into()])].into(),
        }).await.map_err(|error| ServiceError::InternalError(format!("bcsfuse search error: {error}")))?;
        if response.status == "degraded" {
            tracing::warn!(reasons = ?response.reasons, "BCSFuse Card search degraded");
        }
        let recommendations: Vec<_> = response.items.into_iter()
            // Rank-fusion scores have a different scale from reranker relevance.
            .filter(|item| item.score_source != "reranker" || item.score >= command.min_score)
            .map(|item| WorkerRecommendation {
                worker_id: item.id,
                score: item.score,
                short_profile: (!item.summary.is_empty()).then_some(item.summary),
            }).collect();
        // The legacy BCS response exposes this value. Keep its existing shape.
        let raw_response = serde_json::json!({
            "driver_bot_id": null,
            "recommendations": recommendations.iter().map(|item| serde_json::json!({
                "profile_key": super::build_participant_id(&item.worker_id, &self.profile_id),
                "worker_id": item.worker_id,
                "score": item.score,
                "reasons": [],
                "short_profile": item.short_profile.as_deref().unwrap_or_default(),
            })).collect::<Vec<_>>()
        });
        Ok(WorkerRecommendResult { recommendations, raw_response })
    }

    async fn batch_query_worker_profiles(&self, _ids: &[String]) -> ServiceResult<Vec<WorkerProfile>> {
        // Card discovery has no Worker/Profile store. BCS fills absent tags as before.
        Ok(Vec::new())
    }
}
