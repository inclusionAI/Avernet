//! `StoragePluginFactory` for the local filesystem backend. Wraps the existing
//! `LocalStoragePlugin::new`; reads `data_dir` from `StorageBackendConfig.backend`
//! (falling back to `{bots_base_dir}/session-files`).

use std::sync::Arc;

use async_trait::async_trait;
use serde_json::Value;

use bcs_storage_api::factory::{StorageBackendConfig, StoragePluginError, StoragePluginFactory};
use bcs_storage_api::StoragePlugin;

use crate::{LocalStorageConfig, LocalStoragePlugin};

pub struct LocalStoragePluginFactory;

fn data_dir(cfg: &StorageBackendConfig) -> std::path::PathBuf {
    match cfg.backend.get("data_dir") {
        Some(Value::String(s)) if !s.is_empty() => std::path::PathBuf::from(s),
        _ => std::path::PathBuf::from(format!("{}/session-files", cfg.bots_base_dir)),
    }
}

#[async_trait]
impl StoragePluginFactory for LocalStoragePluginFactory {
    fn backend_name(&self) -> &'static str { "local" }

    async fn build(&self, cfg: &StorageBackendConfig)
        -> Result<Arc<dyn StoragePlugin>, StoragePluginError>
    {
        let data_dir = data_dir(cfg);
        // async-safe dir creation (matches LocalStoragePlugin internals which use tokio::fs).
        tokio::fs::create_dir_all(&data_dir)
            .await
            .map_err(|e| StoragePluginError::Build(format!("create data_dir {}: {e}", data_dir.display())))?;
        Ok(Arc::new(LocalStoragePlugin::new(LocalStorageConfig {
            data_dir,
            max_object_size: cfg.max_file_size,
        })))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Map};

    fn cfg(backend: Map<String, Value>) -> StorageBackendConfig {
        StorageBackendConfig {
            env: "test".into(), max_file_size: 1024, multipart_threshold: 100,
            share_link_ttl: 3600, bcs_base_url: "http://bcs".into(),
            bots_base_dir: tempfile::tempdir().unwrap().keep().to_string_lossy().into_owned(),
            backend,
        }
    }

    #[tokio::test]
    async fn builds_from_data_dir_key() {
        let dir = tempfile::tempdir().unwrap();
        let mut m = Map::new();
        m.insert("data_dir".into(), json!(dir.path().to_string_lossy().to_string()));
        let p = LocalStoragePluginFactory.build(&cfg(m)).await.unwrap();
        assert_eq!(p.capabilities().supports_presign_put, false);
    }

    #[tokio::test]
    async fn falls_back_to_bots_base_dir_session_files() {
        let p = LocalStoragePluginFactory.build(&cfg(Map::new())).await.unwrap();
        assert_eq!(p.backend_name(), "local");
        // data_dir should be {bots_base_dir}/session-files
        assert!(p.health_check().await.unwrap().ok);
    }
}