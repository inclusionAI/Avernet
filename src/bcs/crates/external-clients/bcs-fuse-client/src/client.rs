//! HTTP client for bcsfuse API.

use std::time::Duration;

use crate::FuseClientError;
use crate::types::*;
use bcs_config_api::BcsFuseConfig;

/// HTTP client for bcsfuse service.
///
/// Uses two `reqwest::Client` instances with different timeouts:
/// - `fusion_client`: longer timeout for LLM-backed fusion calls
/// - `sync_client`: shorter timeout for CRUD operations (sync, offline)
pub struct FuseClient {
    base_url: String,
    /// Longer timeout for LLM-backed fusion (default: 120s).
    fusion_client: reqwest::Client,
    /// Shorter timeout for sync/offline CRUD (default: 2s).
    sync_client: reqwest::Client,
}

impl std::fmt::Debug for FuseClient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("FuseClient")
            .field("base_url", &self.base_url)
            .finish()
    }
}

impl FuseClient {
    /// Create a new FuseClient from config.
    pub fn new(config: &BcsFuseConfig) -> Result<Self, FuseClientError> {
        let fusion_client = reqwest::Client::builder()
            .timeout(Duration::from_millis(config.fusion_timeout_ms))
            .build()
            .map_err(FuseClientError::HttpClient)?;
        let sync_client = reqwest::Client::builder()
            .timeout(Duration::from_millis(config.sync_timeout_ms))
            .build()
            .map_err(FuseClientError::HttpClient)?;

        Ok(Self {
            base_url: config.url.clone(),
            fusion_client,
            sync_client,
        })
    }

    /// Create a client for tests and conformance checks without external IO.
    pub fn for_test_with_url(url: impl Into<String>) -> Result<Self, FuseClientError> {
        let config = BcsFuseConfig {
            url: url.into(),
            ..BcsFuseConfig::default()
        };
        Self::new(&config)
    }

    /// Atomic sync: create/update worker + set online + upsert profile.
    ///
    /// Calls `POST /v1/workers/{id}/sync` — the atomic endpoint in bcsfuse.
    /// Returns `SyncWorkerResponse` with `profile_activated` status.
    pub async fn sync_worker(
        &self,
        worker_id: &str,
        request: SyncWorkerRequest,
    ) -> Result<SyncWorkerResponse, FuseClientError> {
        let url = format!("{}/v1/workers/{}/sync", self.base_url, worker_id);

        let response = self
            .sync_client
            .post(&url)
            .json(&request)
            .send()
            .await?
            .error_for_status()?
            .json::<SyncWorkerResponse>()
            .await?;

        Ok(response)
    }

    /// Set worker offline (best-effort, fire-and-forget).
    pub async fn set_worker_offline(&self, worker_id: &str) -> Result<(), FuseClientError> {
        let url = format!("{}/v1/workers/{}/offline", self.base_url, worker_id);

        self.sync_client
            .put(&url)
            .send()
            .await?
            .error_for_status()?;

        Ok(())
    }

    /// Call the fusion API (uses longer timeout — LLM-backed).
    pub async fn fuse(
        &self,
        group_id: &str,
        request: FuseRequest,
    ) -> Result<FuseResponse, FuseClientError> {
        let url = format!("{}/api/v1/groups/{}/fuse", self.base_url, group_id);

        let response = self
            .fusion_client
            .post(&url)
            .json(&request)
            .send()
            .await?
            .error_for_status()?
            .json::<FuseResponse>()
            .await?;

        Ok(response)
    }

    /// Recommend/discover workers by question.
    pub async fn recommend_workers(
        &self,
        request: RecommendWorkersRequest,
    ) -> Result<(RecommendWorkersResponse, serde_json::Value), FuseClientError> {
        let url = format!("{}/api/v1/recommend", self.base_url);

        tracing::info!(
            url = %url,
            request_body = %serde_json::to_string(&request).unwrap_or_default(),
            "recommend_workers: sending request"
        );

        let resp = self.sync_client.post(&url).json(&request).send().await?;

        let status = resp.status();
        let raw_body = resp.text().await?;

        tracing::debug!(
            url = %url,
            status = %status,
            raw_body = %raw_body,
            "recommend_workers: received response"
        );

        if !status.is_success() {
            return Err(FuseClientError::HttpError(format!(
                "HTTP {} — {}",
                status, raw_body
            )));
        }

        let response: RecommendWorkersResponse = serde_json::from_str(&raw_body).map_err(|e| {
            FuseClientError::InvalidResponse(format!(
                "Failed to parse recommend response: {} — body: {}",
                e, raw_body
            ))
        })?;

        let raw_value: serde_json::Value =
            serde_json::from_str(&raw_body).unwrap_or(serde_json::Value::Null);

        Ok((response, raw_value))
    }

    /// Batch query workers by IDs.
    ///
    /// Calls `POST /v1/workers/batch` — returns worker info including `profile_tag_list`.
    pub async fn batch_query_workers(
        &self,
        worker_ids: &[String],
    ) -> Result<BatchWorkersResponse, FuseClientError> {
        let url = format!("{}/v1/workers/batch", self.base_url);

        let request = BatchWorkersRequest {
            worker_ids: worker_ids.to_vec(),
        };

        let resp = self.sync_client.post(&url).json(&request).send().await?;

        let status = resp.status();
        let raw_body = resp.text().await?;

        if !status.is_success() {
            return Err(FuseClientError::HttpError(format!(
                "HTTP {} — {}",
                status, raw_body
            )));
        }

        let response: BatchWorkersResponse = serde_json::from_str(&raw_body).map_err(|e| {
            FuseClientError::InvalidResponse(format!(
                "Failed to parse batch workers response: {} — body: {}",
                e, raw_body
            ))
        })?;

        Ok(response)
    }
}
