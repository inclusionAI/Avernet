use serde::{Deserialize, Serialize};

/// Session file workspace configuration (Task 11 bootstrap wiring).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionFilesConfig {
    /// Storage backend tag. `"local"` selects `bcs_storage_local::LocalStoragePlugin`.
    /// Other backends are linked into the binary by future plugin registration.
    #[serde(default = "default_session_files_storage_backend")]
    pub storage_backend: String,

    /// Object size at or above which uploads switch from single-PUT to multipart.
    #[serde(default = "default_session_files_multipart_threshold")]
    pub multipart_threshold: u64,

    /// Hard cap on a single object's size in bytes; intersected with the
    /// backend's `capabilities().max_object_size` at service construction.
    #[serde(default = "default_session_files_max_file_size")]
    pub max_file_size: u64,

    /// In-session + share download share-link TTL (baas expire_seconds), seconds.
    #[serde(default = "default_session_files_share_link_ttl")]
    pub share_link_ttl: u64,

    /// Share-token configuration — independent of `invite.token_secret`
    /// so rotating one does not invalidate the other's outstanding tokens.
    #[serde(default)]
    pub share: SessionFilesShareConfig,

    /// Backend-specific config pass-through (local: data_dir; baas: endpoint/tenant/...).
    #[serde(default)]
    pub backend: toml::Table,
}

impl Default for SessionFilesConfig {
    fn default() -> Self {
        Self {
            storage_backend: default_session_files_storage_backend(),
            multipart_threshold: default_session_files_multipart_threshold(),
            max_file_size: default_session_files_max_file_size(),
            share_link_ttl: default_session_files_share_link_ttl(),
            share: SessionFilesShareConfig::default(),
            backend: toml::Table::new(),
        }
    }
}

fn default_session_files_storage_backend() -> String {
    "local".to_string()
}

fn default_session_files_multipart_threshold() -> u64 {
    104_857_600
}

fn default_session_files_max_file_size() -> u64 {
    5_368_709_120
}

fn default_session_files_share_link_ttl() -> u64 {
    3600
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionFilesShareConfig {
    /// HMAC secret for share-token mint/consume. If unset, bootstrap logs a
    /// warning and generates a random 32-byte secret that does NOT survive
    /// restart — production deployments must set this explicitly.
    #[serde(default)]
    pub token_secret: Option<String>,

    #[serde(default)]
    pub token_secret_secret: Option<String>,

    /// Default share-token TTL in seconds. Clamped to `[60, 604800]` at mint.
    #[serde(default = "default_session_files_share_ttl")]
    pub default_ttl_seconds: u64,

    /// Public base URL used to construct share links. When None, falls back
    /// to `bcs_endpoint` or `http://{bind}:{port}` in the service layer.
    #[serde(default)]
    pub share_base_url: Option<String>,

    /// TTL (seconds) for share URLs minted at history-read time for image
    /// echo. Clamped to [60, 604800]. Independent from `default_ttl_seconds`
    /// (the share-API default) so history echo can be tuned separately.
    #[serde(default = "default_history_attachment_ttl")]
    pub history_attachment_ttl_seconds: u64,
}

impl Default for SessionFilesShareConfig {
    fn default() -> Self {
        Self {
            token_secret: None,
            token_secret_secret: None,
            default_ttl_seconds: default_session_files_share_ttl(),
            share_base_url: None,
            history_attachment_ttl_seconds: default_history_attachment_ttl(),
        }
    }
}

fn default_session_files_share_ttl() -> u64 {
    86400
}

fn default_history_attachment_ttl() -> u64 {
    3600
}
