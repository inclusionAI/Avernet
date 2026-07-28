use bcs_domain::{ActorKind, ActorRef};
use bcs_storage_api::{PresignGetTicket, StorageHandle, StoragePlugin};
use bcs_storage_baas::{config::BaasConfig, BaasReadyHandle, BaasStoragePlugin};
use serde_json::json;
use wiremock::matchers::{body_partial_json, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn plugin(url: String) -> BaasStoragePlugin {
    BaasStoragePlugin::new(BaasConfig { endpoint: url, tenant: "t".into(), share_link_ttl: 3600, ..Default::default() }, 5_000_000_000)
}
fn ready_handle(key: &str, tid: &str) -> StorageHandle {
    StorageHandle {
        backend: "baas".into(), key: key.into(),
        backend_handle: serde_json::to_value(BaasReadyHandle { transfer_id: tid.into() }).unwrap(),
    }
}

#[tokio::test]
async fn presign_get_returns_share_url_with_iso_expires_at() {
    let server = MockServer::start().await;
    Mock::given(method("POST")).and(path("/api/v1/sessions/t/sid/files/transfers/tid/share-link"))
        .and(body_partial_json(json!({"expire_seconds":3600})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"code":0,"data":{
            "share_url":"https://oss/get?sig=Y","transfer_id":"tid","expires_at":"2026-07-23T12:00:00Z"
        }})))
        .mount(&server).await;
    let p = plugin(server.uri());
    let ticket: PresignGetTicket = p.presign_get(&ready_handle("session-files/prod/sid/fid/f", "tid"), 3600, None).await.unwrap();
    assert_eq!(ticket.download_url, "https://oss/get?sig=Y");
    assert!(ticket.expires_at > 0, "ISO8601 parsed to unix secs");
}

#[tokio::test]
async fn presign_get_not_ready_maps_to_conflict() {
    let server = MockServer::start().await;
    Mock::given(method("POST")).and(path("/api/v1/sessions/t/sid/files/transfers/tid/share-link"))
        .respond_with(ResponseTemplate::new(409).set_body_json(json!({"detail":{"error":"SOURCE_TRANSFER_NOT_READY","message":"UPLOADING","transfer_id":"tid","current_status":"UPLOADING"}})))
        .mount(&server).await;
    let p = plugin(server.uri());
    let err = p.presign_get(&ready_handle("session-files/prod/sid/fid/f", "tid"), 3600, None).await.unwrap_err();
    assert!(matches!(err, bcs_storage_api::StorageError::Conflict(_)));
}

#[tokio::test]
async fn presign_get_does_not_cache_each_call_re_posts() {
    let server = MockServer::start().await;
    Mock::given(method("POST")).and(path("/api/v1/sessions/t/sid/files/transfers/tid/share-link"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"code":0,"data":{"share_url":"https://oss/x","transfer_id":"tid","expires_at":"2026-07-23T12:00:00Z"}})))
        .expect(2) // 两次调用都 POST — 不缓存
        .mount(&server).await;
    let p = plugin(server.uri());
    let h = ready_handle("session-files/prod/sid/fid/f", "tid");
    p.presign_get(&h, 3600, None).await.unwrap();
    p.presign_get(&h, 3600, None).await.unwrap();
}

#[tokio::test]
async fn presign_passes_caller_as_operator() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/v1/sessions/t/sid/files/transfers/tid/share-link"))
        .and(body_partial_json(json!({"operator": "human:human_123"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "code":0,"data":{
                "share_url":"https://oss/get?sig=Y","transfer_id":"tid","expires_at":"2026-07-23T12:00:00Z"
            }
        })))
        .mount(&server).await;

    let p = plugin(server.uri());
    let h = ready_handle("session-files/prod/sid/fid/f", "tid");
    let caller = Some(&ActorRef {
        actor_kind: ActorKind::Human,
        actor_id: "human_123".into(),
    });
    let ticket = p.presign_get(&h, 3600, caller).await.unwrap();
    assert_eq!(ticket.download_url, "https://oss/get?sig=Y");
}