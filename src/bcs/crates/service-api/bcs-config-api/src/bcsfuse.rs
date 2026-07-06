//! bcsfuse integration configuration.

use serde::{Deserialize, Serialize};

/// bcsfuse integration configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BcsFuseConfig {
    /// Enable bcsfuse integration.
    #[serde(default)]
    pub enabled: bool,

    /// bcsfuse service URL (e.g., "http://127.0.0.1:8765").
    #[serde(default = "default_url")]
    pub url: String,

    /// Timeout for fusion API calls in milliseconds (LLM-backed, needs longer timeout).
    #[serde(default = "default_fusion_timeout")]
    pub fusion_timeout_ms: u64,

    /// Timeout for sync/offline API calls in milliseconds.
    #[serde(default = "default_sync_timeout")]
    pub sync_timeout_ms: u64,

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
            url: default_url(),
            fusion_timeout_ms: default_fusion_timeout(),
            sync_timeout_ms: default_sync_timeout(),
            profile_id: default_profile_id(),
            recommend_top_k: default_recommend_top_k(),
            recommend_min_score: default_recommend_min_score(),
        }
    }
}
