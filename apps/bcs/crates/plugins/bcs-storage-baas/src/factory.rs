//! `StoragePluginFactory` for baas: parse endpoint/tenant/... from
//! `StorageBackendConfig.backend`, build `BaasStoragePlugin`.

use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use bcs_storage_api::factory::{StorageBackendConfig, StoragePluginError, StoragePluginFactory};
use bcs_storage_api::StoragePlugin;
use serde_json::Value;

use crate::{config::BaasConfig, BaasStoragePlugin};

pub struct BaasStoragePluginFactory;

fn get_str(backend: &serde_json::Map<String, Value>, key: &str) -> Result<String, StoragePluginError> {
    backend.get(key)
        .and_then(|v| v.as_str().map(String::from))
        .filter(|s| !s.is_empty())
        .ok_or_else(|| StoragePluginError::Build(format!("baas: missing required config '{}'", key)))
}

#[async_trait]
impl StoragePluginFactory for BaasStoragePluginFactory {
    fn backend_name(&self) -> &'static str { "baas" }

    async fn build(&self, cfg: &StorageBackendConfig)
        -> Result<Arc<dyn StoragePlugin>, StoragePluginError>
    {
        let endpoint = get_str(&cfg.backend, "endpoint")?;
        let tenant = get_str(&cfg.backend, "tenant")?;
        let health_probe_path = cfg.backend.get("health_probe_path")
            .and_then(|v| v.as_str().map(String::from)).unwrap_or_default();
        // auth headers: read a `headers` sub-table ([session_files.backend.headers] key=val)
        let mut auth_headers = Vec::new();
        if let Some(Value::Object(h)) = cfg.backend.get("headers") {
            for (k, v) in h {
                if let Some(s) = v.as_str() {
                    auth_headers.push((k.clone(), s.to_string()));
                }
            }
        }
        let baas_cfg = BaasConfig {
            endpoint, tenant,
            share_link_ttl: cfg.share_link_ttl,
            health_probe_path,
            auth_headers,
            http_timeout: Duration::from_secs(30),
        };
        // max_object_size = cfg.max_file_size (baas has no hard cap; capabilities cheap/IO-free, no probe)
        Ok(Arc::new(BaasStoragePlugin::new(baas_cfg, cfg.max_file_size)))
    }
}