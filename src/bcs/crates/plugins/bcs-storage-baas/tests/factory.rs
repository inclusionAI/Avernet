use bcs_storage_api::factory::{StorageBackendConfig, StoragePluginError, StoragePluginFactory};
use bcs_storage_baas::BaasStoragePluginFactory;
use serde_json::{json, Map};

fn cfg(backend: Map<String, serde_json::Value>) -> StorageBackendConfig {
    StorageBackendConfig {
        env: "prod".into(), max_file_size: 5_000_000_000, multipart_threshold: 100,
        share_link_ttl: 3600, bcs_base_url: "http://bcs".into(), bots_base_dir: "/x".into(),
        backend,
    }
}

#[tokio::test]
async fn builds_from_endpoint_tenant() {
    let mut m = Map::new();
    m.insert("endpoint".into(), json!("http://baas:8080"));
    m.insert("tenant".into(), json!("teamclaw"));
    let p = BaasStoragePluginFactory.build(&cfg(m)).await.unwrap();
    assert_eq!(p.backend_name(), "baas");
    assert_eq!(p.capabilities().supports_presign_put, true);
    assert_eq!(p.capabilities().max_object_size, 5_000_000_000);
}

#[tokio::test]
async fn errors_when_endpoint_missing() {
    let mut m = Map::new();
    m.insert("tenant".into(), json!("teamclaw"));
    let result = BaasStoragePluginFactory.build(&cfg(m)).await;
    assert!(matches!(result, Err(StoragePluginError::Build(_))));
}

#[tokio::test]
async fn errors_when_tenant_missing() {
    let mut m = Map::new();
    m.insert("endpoint".into(), json!("http://baas:8080"));
    let result = BaasStoragePluginFactory.build(&cfg(m)).await;
    assert!(matches!(result, Err(StoragePluginError::Build(_))));
}