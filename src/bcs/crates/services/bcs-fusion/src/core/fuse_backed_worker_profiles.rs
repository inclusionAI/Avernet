//! Worker profile service implementation backed by bcsfuse.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_fuse_client::{FuseClient, RecommendWorkersRequest};
use bcs_service_api::{
    ServiceError, ServiceResult, WorkerProfile, WorkerProfileService, WorkerRecommendCommand,
    WorkerRecommendResult, WorkerRecommendation,
};

/// `WorkerProfileService` implementation backed by the bcsfuse HTTP client.
pub struct FuseWorkerProfileService {
    client: Arc<FuseClient>,
}

impl FuseWorkerProfileService {
    pub fn new(client: Arc<FuseClient>) -> Self {
        Self { client }
    }
}

#[async_trait]
impl WorkerProfileService for FuseWorkerProfileService {
    async fn recommend_workers(
        &self,
        command: WorkerRecommendCommand,
    ) -> ServiceResult<WorkerRecommendResult> {
        let (response, raw_response) = self
            .client
            .recommend_workers(RecommendWorkersRequest {
                question: command.query,
                top_k: command.top_k,
                min_score: command.min_score,
            })
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("bcsfuse recommend error: {}", error))
            })?;

        Ok(WorkerRecommendResult {
            recommendations: response
                .recommendations
                .into_iter()
                .map(|recommendation| WorkerRecommendation {
                    worker_id: recommendation.worker_id,
                    score: recommendation.score,
                    short_profile: if recommendation.short_profile.is_empty() {
                        None
                    } else {
                        Some(recommendation.short_profile)
                    },
                })
                .collect(),
            raw_response,
        })
    }

    async fn batch_query_worker_profiles(
        &self,
        worker_ids: &[String],
    ) -> ServiceResult<Vec<WorkerProfile>> {
        let response = self
            .client
            .batch_query_workers(worker_ids)
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("bcsfuse batch query error: {}", error))
            })?;

        Ok(response
            .data
            .into_iter()
            .map(|(worker_id, info)| WorkerProfile {
                worker_id,
                tags: info
                    .profile_tags
                    .into_iter()
                    .map(|(key, value)| (key, serde_json::Value::String(value)))
                    .collect(),
            })
            .collect())
    }
}
