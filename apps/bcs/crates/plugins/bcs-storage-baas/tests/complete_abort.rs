use bcs_storage_api::{StoragePlugin, UploadHandle};
use bcs_storage_baas::{config::BaasConfig, BaasPendingHandle, BaasStoragePlugin};
use serde_json::json;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn plugin(url: String) -> BaasStoragePlugin {
    BaasStoragePlugin::new(BaasConfig { endpoint: url, tenant: "t".into(), ..Default::default() }, 5_000_000_000)
}
fn pending_handle(key: &str, tid: &str) -> UploadHandle {
    UploadHandle {
        backend: "baas".into(), key: key.into(),
        backend_handle: serde_json::to_value(BaasPendingHandle {
            transfer_id: tid.into(), transfer_type: "SINGLE".into(), expires_at: 3600,
        }).unwrap(),
        expires_at: 3600,
    }
}

#[tokio::test]
async fn complete_returns_meta_sync_done() {
    let server = MockServer::start().await;
    Mock::given(method("POST")).and(path("/api/v1/sessions/t/sid/files/upload-url/tid/complete"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"code":0,"data":{"transfer_id":"tid","status":"DONE"}})))
        .mount(&server).await;
    let p = plugin(server.uri());
    // key session-files/prod/sid/... → session_id "sid"
    let h = pending_handle("session-files/prod/sid/fid/f", "tid");
    let meta = p.complete_upload(&h).await.unwrap();
    assert_eq!(meta.size, 0); // baas complete response carries no size, meta.size=0 (service layer covers with prepare size)
}

#[tokio::test]
async fn abort_deletes_upload_url_transfer() {
    let server = MockServer::start().await;
    Mock::given(method("DELETE")).and(path("/api/v1/sessions/t/sid/files/upload-url/tid"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"code":0,"data":{"transfer_id":"tid","status":"CANCELLED"}})))
        .mount(&server).await;
    let p = plugin(server.uri());
    let h = pending_handle("session-files/prod/sid/fid/f", "tid");
    p.abort_upload(&h).await.unwrap();
}

#[tokio::test]
async fn complete_oss_object_not_found_maps_to_conflict() {
    let server = MockServer::start().await;
    Mock::given(method("POST")).and(path("/api/v1/sessions/t/sid/files/upload-url/tid/complete"))
        .respond_with(ResponseTemplate::new(409).set_body_json(json!({"detail":{"error":"OSS_OBJECT_NOT_FOUND","message":"no object"}})))
        .mount(&server).await;
    let p = plugin(server.uri());
    let h = pending_handle("session-files/prod/sid/fid/f", "tid");
    let err = p.complete_upload(&h).await.unwrap_err();
    assert!(matches!(err, bcs_storage_api::StorageError::Conflict(_)));
}

#[tokio::test]
async fn abort_treats_transfer_state_conflict_as_idempotent_ok() {
    // baas returns 409 TRANSFER_STATE_CONFLICT when the transfer is already
    // terminal (e.g. already DONE/CANCELLED). abort must treat this as Ok
    // (idempotent), NOT surface Conflict — this is the special-case that
    // distinguishes baas_data_or_conflict_ok from the general baas_data path.
    let server = MockServer::start().await;
    Mock::given(method("DELETE")).and(path("/api/v1/sessions/t/sid/files/upload-url/tid"))
        .respond_with(ResponseTemplate::new(409).set_body_json(json!({"detail":{"error":"TRANSFER_STATE_CONFLICT","message":"already terminal"}})))
        .mount(&server).await;
    let p = plugin(server.uri());
    let h = pending_handle("session-files/prod/sid/fid/f", "tid");
    p.abort_upload(&h).await.unwrap(); // 409 CONFLICT -> Ok (idempotent), not Err
}