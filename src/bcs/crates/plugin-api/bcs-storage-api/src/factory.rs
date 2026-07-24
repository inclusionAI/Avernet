//! Backend-agnostic storage plugin assembly. Each backend crate ships a
//! `StoragePluginFactory` that parses its own backend-specific config keys;
//! the composition root selects one by `storage_backend` and is otherwise
//! ignorant of the backend roster. See
//! `docs/superpowers/specs/2026-07-20-bcs-session-workspace-design-baas-plugin.md`
//! §「落地前置改造」.

use std::sync::Arc;

use async_trait::async_trait;
use serde_json::Map;

use crate::StoragePlugin;

/// Backend-agnostic assembly input: values every backend needs + an opaque
/// pass-through container for backend-specific keys (`endpoint`/`tenant` for
/// baas, `data_dir` for local, …). Factories read their own keys from
/// `backend` and self-validate.
#[derive(Debug, Clone)]
pub struct StorageBackendConfig {
    pub env: String,
    pub max_file_size: u64,
    pub multipart_threshold: u64,
    pub share_link_ttl: u64,
    pub bcs_base_url: String,
    pub bots_base_dir: String,
    /// Backend-specific keys, passed through verbatim from TOML
    /// `[session_files.backend]` (or top-level `[session_files]` leftovers).
    pub backend: Map<String, serde_json::Value>,
}

/// Why a factory failed to build its plugin. Carries a reason for BCS logs;
/// must NOT leak to clients.
#[derive(Debug, thiserror::Error)]
pub enum StoragePluginError {
    #[error("storage backend config error: {0}")]
    Build(String),
}

/// Each storage backend crate implements this: turn the backend-agnostic
/// `StorageBackendConfig` into a concrete `StoragePlugin`. `backend_name`
/// is what the composition root matches against `session_files.storage_backend`.
#[async_trait]
pub trait StoragePluginFactory: Send + Sync {
    fn backend_name(&self) -> &'static str;
    async fn build(&self, cfg: &StorageBackendConfig)
        -> Result<Arc<dyn StoragePlugin>, StoragePluginError>;
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn backend_config_carries_backend_map() {
        let cfg = StorageBackendConfig {
            env: "prod".into(),
            max_file_size: 1024,
            multipart_threshold: 100,
            share_link_ttl: 3600,
            bcs_base_url: "http://bcs".into(),
            bots_base_dir: "/data/bots".into(),
            backend: {
                let mut m = Map::new();
                m.insert("endpoint".into(), json!("http://baas:8080"));
                m
            },
        };
        assert_eq!(cfg.backend["endpoint"], json!("http://baas:8080"));
        assert_eq!(cfg.share_link_ttl, 3600);
    }

    #[test]
    fn plugin_error_build_message() {
        let e = StoragePluginError::Build("missing endpoint".into());
        assert_eq!(e.to_string(), "storage backend config error: missing endpoint");
    }
}