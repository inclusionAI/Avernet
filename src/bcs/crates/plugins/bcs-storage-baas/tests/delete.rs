use bcs_storage_api::{StorageError, StorageHandle, StoragePlugin};
use bcs_storage_baas::{config::BaasConfig, BaasReadyHandle, BaasStoragePlugin};
use serde_json::json;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn plugin(url: String) -> BaasStoragePlugin {
    BaasStoragePlugin::new(BaasConfig { endpoint: url, tenant: "t".into(), ..Default::default() }, 5_000_000_000)
}
fn h(key: &str, tid: &str) -> StorageHandle {
    StorageHandle { backend: "baas".into(), key: key.into(),
        backend_handle: serde_json::to_value(BaasReadyHandle { transfer_id: tid.into() }).unwrap() }
}

#[tokio::test]
async fn delete_returns_ok_on_deleted() {
    let server = MockServer::start().await;
    Mock::given(method("DELETE")).and(path("/api/v1/sessions/t/sid/files/transfers/tid"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"code":0,"data":{"transfer_id":"tid","previous_status":"DONE","new_status":"DELETED"}})))
        .mount(&server).await;
    plugin(server.uri()).delete(&h("session-files/prod/sid/f/f", "tid")).await.unwrap();
}

#[tokio::test]
async fn delete_idempotent_on_transfer_not_found() {
    let server = MockServer::start().await;
    Mock::given(method("DELETE")).and(path("/api/v1/sessions/t/sid/files/transfers/tid"))
        .respond_with(ResponseTemplate::new(404).set_body_json(json!({"detail":{"error":"TRANSFER_NOT_FOUND","message":"gone"}})))
        .mount(&server).await;
    plugin(server.uri()).delete(&h("session-files/prod/sid/f/f", "tid")).await.unwrap(); // 404 -> Ok idempotent
}

#[tokio::test]
async fn delete_not_terminal_maps_conflict() {
    let server = MockServer::start().await;
    Mock::given(method("DELETE")).and(path("/api/v1/sessions/t/sid/files/transfers/tid"))
        .respond_with(ResponseTemplate::new(409).set_body_json(json!({"detail":{"error":"TRANSFER_NOT_TERMINAL","message":"UPLOADING"}})))
        .mount(&server).await;
    let err = plugin(server.uri()).delete(&h("session-files/prod/sid/f/f", "tid")).await.unwrap_err();
    assert!(matches!(err, StorageError::Conflict(_)));
}