//! bcsfuse integration configuration.

use secrecy::{ExposeSecret, Secret};
use serde::{Deserialize, Serialize};

/// The remote catalog protocol. Registration and discovery use the same version.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum BcsFuseApiVersion {
    #[default]
    V1,
    V2,
}

/// bcsfuse integration configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BcsFuseConfig {
    /// Enable bcsfuse integration.
    #[serde(default)]
    pub enabled: bool,

    /// Select the worker catalog (v1) or Card catalog (v2).
    #[serde(default)]
    pub api_version: BcsFuseApiVersion,

    /// bcsfuse service URL (e.g., "http://127.0.0.1:8765").
    #[serde(default = "default_url")]
    pub url: String,

    /// Secret-provider key containing the BCSFuse Bearer credential.
    #[serde(default)]
    pub authorization_ref: Option<String>,

    /// Bearer credential resolved by bootstrap from `authorization_ref`.
    #[serde(skip)]
    #[doc(hidden)]
    pub resolved_authorization: Option<Secret<String>>,

    /// Timeout for fusion API calls in milliseconds (LLM-backed, needs longer timeout).
    #[serde(default = "default_fusion_timeout")]
    pub fusion_timeout_ms: u64,

    /// Timeout for sync/offline API calls in milliseconds.
    #[serde(default = "default_sync_timeout")]
    pub sync_timeout_ms: u64,

    /// Base backoff between catalog sync retries, in milliseconds.
    /// Both versions attempt each change at most 10 times, with exponential
    /// backoff capped at eight seconds. A later change starts a new attempt budget.
    #[serde(default = "default_sync_retry_base")]
    pub sync_retry_base_ms: u64,

    /// Profile ID for worker profiles (default: "default").
    #[serde(default = "default_profile_id")]
    pub profile_id: String,

    /// Maximum candidates for recommend API.
    #[serde(default = "default_recommend_top_k")]
    pub recommend_top_k: u32,

    /// Minimum score threshold for recommend API.
    #[serde(default = "default_recommend_min_score")]
    pub recommend_min_score: f64,
}

fn default_url() -> String {
    "http://127.0.0.1:8765".to_string()
}

fn default_fusion_timeout() -> u64 {
    120_000 // 2 min — fusion involves LLM calls
}

fn default_sync_timeout() -> u64 {
    10_000 // 10s — simple CRUD
}

fn default_sync_retry_base() -> u64 {
    1_000 // 1s, doubling per attempt
}

fn default_profile_id() -> String {
    "default".to_string()
}

fn default_recommend_top_k() -> u32 {
    10
}

fn default_recommend_min_score() -> f64 {
    0.1
}

impl Default for BcsFuseConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            api_version: BcsFuseApiVersion::V1,
            url: default_url(),
            authorization_ref: None,
            resolved_authorization: None,
            fusion_timeout_ms: default_fusion_timeout(),
            sync_timeout_ms: default_sync_timeout(),
            sync_retry_base_ms: default_sync_retry_base(),
            profile_id: default_profile_id(),
            recommend_top_k: default_recommend_top_k(),
            recommend_min_score: default_recommend_min_score(),
        }
    }
}

impl BcsFuseConfig {
    /// Bearer credential used for service-to-service bcsfuse requests.
    pub fn auth_token(&self) -> Option<&str> {
        self.resolved_authorization
            .as_ref()
            .map(|token| token.expose_secret().as_str())
    }

    /// Store credential material resolved by bootstrap's secret provider.
    pub fn set_resolved_authorization(&mut self, value: String) {
        self.resolved_authorization = Some(value.into());
    }
}
