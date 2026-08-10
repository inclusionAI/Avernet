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
    /// Shorter timeout for sync/offline CRUD (default: 10s).
    sync_client: reqwest::Client,
}

impl std::fmt::Debug for FuseClient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("FuseClient")
            .field("base_url", &url_for_log(&self.base_url))
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
        let logged_url = url_for_log(&url);

        tracing::info!(
            url = %logged_url,
            question_len = request.question.len(),
            top_k = request.top_k,
            min_score = request.min_score,
            "recommend_workers: sending request"
        );

        let resp = self.sync_client.post(&url).json(&request).send().await?;

        let status = resp.status();
        let raw_body = resp.text().await?;

        tracing::debug!(
            url = %logged_url,
            status = %status,
            response_body_len = raw_body.len(),
            "recommend_workers: received response"
        );

        if !status.is_success() {
            return Err(FuseClientError::HttpError(format!(
                "HTTP {status}; body_len={}",
                raw_body.len()
            )));
        }

        let response: RecommendWorkersResponse = serde_json::from_str(&raw_body).map_err(|e| {
            FuseClientError::InvalidResponse(format!(
                "Failed to parse recommend response: {e}; body_len={}",
                raw_body.len()
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
                "HTTP {status}; body_len={}",
                raw_body.len()
            )));
        }

        let response: BatchWorkersResponse = serde_json::from_str(&raw_body).map_err(|e| {
            FuseClientError::InvalidResponse(format!(
                "Failed to parse batch workers response: {e}; body_len={}",
                raw_body.len()
            ))
        })?;

        Ok(response)
    }
}

fn url_for_log(raw_url: &str) -> String {
    let Ok(mut url) = reqwest::Url::parse(raw_url) else {
        return "<invalid URL>".to_string();
    };
    let _ = url.set_username("");
    let _ = url.set_password(None);
    url.set_query(None);
    url.set_fragment(None);
    url.to_string()
}

#[cfg(test)]
mod tests {
    use super::url_for_log;

    #[test]
    fn diagnostic_url_removes_credentials_query_and_fragment() {
        let logged = url_for_log("https://user:secret@example.com/fuse?token=value#fragment");
        assert_eq!(logged, "https://example.com/fuse");
        assert!(!logged.contains("secret"));
        assert!(!logged.contains("token"));
    }
}
