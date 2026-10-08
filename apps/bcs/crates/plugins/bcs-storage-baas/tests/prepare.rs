use bcs_domain::{ActorKind, ActorRef};
use bcs_storage_api::{ClientUploadTarget, StoragePlugin, UploadMode, UploadPrepareRequest};
use bcs_storage_baas::{config::BaasConfig, BaasStoragePlugin};
use serde_json::json;
use wiremock::matchers::{body_partial_json, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn plugin(url: String) -> BaasStoragePlugin {
    BaasStoragePlugin::new(
        BaasConfig {
            endpoint: url,
            tenant: "teamclaw".into(),
            ..Default::default()
        },
        5_000_000_000,
    )
}

fn req(size: u64) -> UploadPrepareRequest {
    UploadPrepareRequest {
        key: "session-files/prod/sid/fid/f".into(),
        file_name: "f".into(),
        mime_type: "application/octet-stream".into(),
        size,
        ttl_secs: 300,
    }
}

#[tokio::test]
async fn prepare_single_returns_direct_url_and_transfer_id() {
    let server = MockServer::start().await;
    // session_id "sid" encoded; path = /api/v1/sessions/teamclaw/sid/files/upload-url
    Mock::given(method("POST"))
        .and(path("/api/v1/sessions/teamclaw/sid/files/upload-url"))
        .and(body_partial_json(
            json!({"filename":"f","file_size":5,"content_type":"application/octet-stream","staging_subdir":null}),
        ))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "code":0,"message":"success","data":{
                "upload_url":"https://oss/...?sig=X","transfer_id":"t-single",
                "http_method":"PUT","expires_at":"2026-07-23T12:00:00Z","type":"SINGLE"
            }
        })))
        .mount(&server)
        .await;

    let p = plugin(server.uri());
    let r = p.prepare_upload(req(5), None).await.unwrap();
    assert_eq!(r.handle.backend, "baas");
    // backend_handle only durable locator (no upload_url persisted)
    assert_eq!(r.handle.backend_handle["transfer_id"], "t-single");
    assert_eq!(r.handle.backend_handle["type"], "SINGLE");
    assert!(r.handle.backend_handle.get("upload_url").is_none(), "must NOT persist upload_url");
    match r.client_target {
        ClientUploadTarget::Direct {
            mode: UploadMode::Single,
            url,
            ..
        } => {
            assert_eq!(url.as_deref(), Some("https://oss/...?sig=X"));
        }
        other => panic!("expected Direct Single, got {other:?}"),
    }
}

#[tokio::test]
async fn prepare_multipart_returns_parts_and_null_top_url() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/v1/sessions/teamclaw/sid/files/upload-url"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "code":0,"message":"success","data":{
                "upload_url":null,"transfer_id":"t-multi","http_method":"PUT","expires_at":null,"type":"MULTIPART",
                "upload_session_id":"OSS-1","part_size":10485760,"part_count":2,
                "parts":[{"part_number":1,"upload_url":"https://oss/p1","http_method":"PUT","expires_at":"x"},
                         {"part_number":2,"upload_url":"https://oss/p2","http_method":"PUT","expires_at":"x"}]
            }
        })))
        .mount(&server)
        .await;

    let p = plugin(server.uri());
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let r = p.prepare_upload(req(20 * 1024 * 1024), None).await.unwrap();
    assert_eq!(r.handle.backend_handle["transfer_id"], "t-multi");
    assert_eq!(r.handle.backend_handle["type"], "MULTIPART");
    // Fixture sends expires_at: null → fallback must be an absolute future
    // timestamp (now + ttl_secs), NOT the raw relative ttl value (300).
    assert!(
        r.expires_at >= now,
        "expires_at {} should be >= now {}",
        r.expires_at, now,
    );
    assert!(
        r.expires_at < now + 305, // 300s ttl + 5s slop window
        "expires_at {} should be <= now+300+5 = {}",
        r.expires_at, now + 305,
    );
    assert_eq!(r.handle.expires_at, r.expires_at);
    match r.client_target {
        ClientUploadTarget::Direct {
            mode: UploadMode::Multipart,
            url,
            parts,
            part_size,
            part_count,
        } => {
            assert!(url.is_none());
            assert_eq!(part_count, Some(2));
            assert_eq!(part_size, Some(10485760));
            let parts = parts.unwrap();
            assert_eq!(parts.len(), 2);
            assert_eq!(parts[0].part_number, 1);
            assert_eq!(parts[0].url, "https://oss/p1");
        }
        other => panic!("expected Direct Multipart, got {other:?}"),
    }
    // per-part URLs NOT persisted in backend_handle
    assert!(r.handle.backend_handle.get("parts").is_none());
    assert!(r.handle.backend_handle.get("upload_session_id").is_none());
}

#[tokio::test]
async fn prepare_session_id_with_colon_is_percent_encoded() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path(
            "/api/v1/sessions/teamclaw/bcs_grp_abc%3Acdf28232/files/upload-url",
        ))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "code":0,"data":{"upload_url":"https://oss","transfer_id":"t","http_method":"PUT","expires_at":"x","type":"SINGLE"}
        })))
        .mount(&server)
        .await;
    let p = plugin(server.uri());
    let mut r = req(5);
    r.key = "session-files/prod/bcs_grp_abc:cdf28232/fid/f".into();
    // session_id_from_key takes 3rd segment "bcs_grp_abc:cdf28232";
    // percent_encode_path encodes ':'→"%3A" but keeps '_', 'a-z' →
    // "bcs_grp_abc%3Acdf28232" (matching the mock path).
    let res = p.prepare_upload(r, None).await.unwrap();
    // Only assert the request path was correct (mock match enforces it; 404 otherwise).
    assert_eq!(res.handle.backend_handle["transfer_id"], "t");
}

#[tokio::test]
async fn prepare_passes_caller_as_operator() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/v1/sessions/teamclaw/sid/files/upload-url"))
        .and(body_partial_json(
            json!({"operator": "human:human_123", "content_type": "application/octet-stream"}),
        ))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "code":0,"message":"success","data":{
                "upload_url":"https://oss/x","transfer_id":"t-operator",
                "http_method":"PUT","expires_at":"2026-07-23T12:00:00Z","type":"SINGLE"
            }
        })))
        .mount(&server)
        .await;

    let p = plugin(server.uri());
    let caller = Some(&ActorRef {
        actor_kind: ActorKind::Human,
        actor_id: "human_123".into(),
    });
    let r = p.prepare_upload(req(5), caller).await.unwrap();
    assert_eq!(r.handle.backend_handle["transfer_id"], "t-operator");
}

#[tokio::test]
async fn prepare_passes_request_mime_type_as_content_type() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/v1/sessions/teamclaw/sid/files/upload-url"))
        .and(body_partial_json(json!({"content_type": "image/png"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "code":0,"data":{"upload_url":"https://oss/img","transfer_id":"t-png","http_method":"PUT","expires_at":"2026-07-23T12:00:00Z","type":"SINGLE"}
        })))
        .mount(&server)
        .await;

    let p = plugin(server.uri());
    let mut r = req(5);
    r.mime_type = "image/png".into();
    let res = p.prepare_upload(r, None).await.unwrap();
    assert_eq!(res.handle.backend_handle["transfer_id"], "t-png");
}
