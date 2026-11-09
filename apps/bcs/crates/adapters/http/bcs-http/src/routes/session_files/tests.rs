use super::*;
use crate::router::build_api_router;
use crate::state::HttpAppState;
use axum::body::to_bytes;
use axum::http::{header, HeaderValue, Method, Request, StatusCode};
use bcs_bot::BotCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_domain::{
    ActorKind as DomainActorKind, FileStatus, SystemMessageEvent,
};
use bcs_group::GroupCore;
use bcs_group_store::MemoryGroupRepo;
use bcs_service_api::application::session_files::SessionFileService;
use bcs_service_api::port::repo::{
    GroupRepoPort, SessionFileRepoPort, SessionRepoPort,
};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, Group, GroupKind, GroupStatus,
    HumanMentionNotifyMode, Participant, ParticipantRole, ServiceResult, SessionKind,
    SystemMessageService,
};
use bcs_services_container::Services;
use bcs_session::SessionManagementServiceImpl;
use bcs_session_file_store::MemorySessionFileRepo;
use bcs_session_file::{SessionFileServiceConfig, SessionFileServiceImpl};
use bcs_session_store::MemorySessionRepo;
use bcs_storage_api::fake::FakeStoragePlugin;
use bcs_storage_api::StorageCapabilities;
use std::path::PathBuf;
use std::sync::Arc;
use tower::ServiceExt;
use async_trait::async_trait;
use tokio::sync::Mutex;

// ------------------- Helpers -------------------

#[derive(Default)]
struct RecordingSystemMessage {
    notifications: Mutex<Vec<SystemMessageEvent>>,
}

#[async_trait]
impl SystemMessageService for RecordingSystemMessage {
    async fn notify(
        &self,
        _group_id: &str,
        event: SystemMessageEvent,
        _session_id: &str,
        _session_participants: &[Participant],
    ) -> ServiceResult<usize> {
        self.notifications.lock().await.push(event);
        Ok(1)
    }
}

fn local_caps() -> StorageCapabilities {
    StorageCapabilities {
        supports_presign_put: false,
        supports_presign_download: false,
        supports_stream_put: true,
        supports_stream_get: true,
        supports_inline_view: true,
        max_object_size: 1024 * 1024 * 1024,
    }
}

fn empty_caps() -> BotCapabilities {
    BotCapabilities {
        name: Some("test-bot".into()),
        summary: None,
        domains: Vec::new(),
        skills: Vec::new(),
        scopes: Vec::new(),
        binding_channels: None,
        hidden: false,
        visibility: String::new(),
        agent_code: None,
        agent_token: None,
    }
}

/// Test no-auth shared-content URL projector used by the upload-completion
/// notification assertions.
struct CompletionShareProjector;
impl bcs_service_api::application::v1::SessionFileInternalContentUrlProjector
    for CompletionShareProjector
{
    fn shared_content_url(&self, token: &str) -> String {
        format!("http://test.local/sessions/shared-file/content?token={token}")
    }
}

fn group_participant(bot_uuid: &str) -> Participant {
    Participant {
        bot_uuid: bot_uuid.to_string(),
        bot_name: None,
        kind: None,
        role: ParticipantRole::default(),
        actor_kind: DomainActorKind::Bot,
        mode: None,
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    }
}

struct TestApp {
    state: HttpAppState,
    bot_a_token: String,
    bot_b_token: String,
    system_messages: Arc<RecordingSystemMessage>,
    #[allow(dead_code)]
    bot_a: String,
    #[allow(dead_code)]
    bot_b: String,
    sid: String,
}

async fn build_test_app() -> TestApp {
    build_test_app_with_max_size(1024 * 1024).await
}

async fn build_test_app_with_max_size(max_size: u64) -> TestApp {
    // Registry: two bots registered with stable tokens.
    let bot_dir = tempfile::tempdir().expect("temp dir for bot registry");
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(PathBuf::from(
        bot_dir.path(),
    )));
    let registry = Arc::new(BotCore::with_repo(bot_repo.clone()));
    registry
        .register_with_owner_and_token(
            "bot-a".into(),
            empty_caps(),
            "alice",
            "token-a",
        )
        .await
        .expect("register bot-a");
    registry
        .register_with_owner_and_token(
            "bot-b".into(),
            empty_caps(),
            "bob",
            "token-b",
        )
        .await
        .expect("register bot-b");

    // Group: g1 with bot_a and bot_b as participants, bot_a as driver.
    let group_repo = Arc::new(MemoryGroupRepo::new());
    let group_core = Arc::new(GroupCore::with_repo(group_repo.clone()));
    let group = Group {
        id: "g1".into(),
        label: None,
        status: GroupStatus::default(),
        driver_bot: "bot-a".into(),
        originator: Some("bot-a".into()),
        routing_policy: None,
        human_mention_notify_mode: HumanMentionNotifyMode::default(),
        context: None,
        opening_message: None,
        participants: vec![group_participant("bot-a"), group_participant("bot-b")],
        messages: Vec::new(),
        service_group_uuid: None,
        service_mode: None,
        created_at: 0,
        updated_at: 0,
        group_kind: GroupKind::default(),
        dm_pair_key: None,
        group_strategy: bcs_service_api::GroupStrategy::default(),
        version: 1,
        record_status: "active".to_string(),
        visibility: "protected".to_string(),
    };
    group_repo.upsert(group).await.expect("upsert group");

    // Session id MUST follow `{group_id}:{8_hex}` per
    // `bcs_service_api::core::session::validate_session_id`.
    let sid = "g1:abcd1234".to_string();

    // Session: g1:abcd1234 -> g1, participants [bot-a, bot-b], created_by
    // "human_alice". Seed via the repo directly to bypass application-layer
    // validation that would otherwise require a fully wired group store.
    let session_repo = Arc::new(MemorySessionRepo::new());
    session_repo
        .create(
            "g1",
            bcs_service_api::port::repo::NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: vec![group_participant("bot-a"), group_participant("bot-b")],
                group_version: Some(1),
                caller_id: Some("bot-a".into()),
                caller_principal: None,
                input: None,
                created_by: Some("human_alice".into()),
                session_title: None,
                id: Some(sid.clone()),
                meta: None,
                message_visibility_version: 1,
                inherited_contexts: Vec::new(),
            },
        )
        .await
        .expect("create session");

    let session_management = Arc::new(SessionManagementServiceImpl::new(
        session_repo.clone(),
        group_repo.clone(),
    ));

    // The CURRENT owner edges for the seeded
    // bots (bot-authority plan Task 6.3): the
    // file facade consults the authority hook;
    // the historic registry owner facts alone
    // no longer authorize.
    let authority = bcs_test_support::lazy_authority_chain();
    authority.hook.seed_owner("bot-a", "alice");
    authority.hook.seed_owner("bot-b", "bob");

    // SessionFileService with FakeStoragePlugin (local caps) + in-memory repo.
    let storage: Arc<dyn bcs_storage_api::StoragePlugin> =
        Arc::new(FakeStoragePlugin::new(local_caps()));
    let file_repo: Arc<dyn SessionFileRepoPort> = Arc::new(MemorySessionFileRepo::new());
    let session_repo_dyn: Arc<dyn SessionRepoPort> = session_repo.clone();
    let file_cfg = SessionFileServiceConfig {
        storage,
        repo: file_repo,
        session_repo: session_repo_dyn,
        env: "local".into(),
        max_size,
        // effectively never multipart for tests
        multipart_threshold: 1024 * 1024 * 1024,
        bcs_base_url: "http://test.local".into(),
        share_secret: vec![42; 32],
        share_default_ttl: 3600,
        share_link_ttl: 3600,
        share_base_url: Some("http://test.local".into()),
    };
    let session_files: Arc<dyn SessionFileService> =
        Arc::new(SessionFileServiceImpl::new(file_cfg));

    let system_messages = Arc::new(RecordingSystemMessage::default());
    let session_file_application = Arc::new(
        bcs_app_session::SessionFileApplicationServiceImpl::new(
            session_files.clone(),
            session_management.clone(),
            group_core.clone(),
            registry.clone(),
            system_messages.clone(),
            Arc::new(CompletionShareProjector),
        )
        .with_bot_authority_hook(authority.hook.clone()),
    );
    let services = Services::builder()
        .registry(registry)
        .group(group_core)
        .session_management(session_management)
        .session_files(session_files)
        .system_message(system_messages.clone())
        .build_for_test();

    let state = HttpAppState::new(services)
        .with_session_file_application(Some(session_file_application));

    // Sanity: the session exists and the bots resolve.
    assert!(
        state
            .services
            .session_management
            .get(&sid)
            .await
            .unwrap()
            .is_some(),
        "seed session not found"
    );

    TestApp {
        state,
        bot_a_token: "token-a".into(),
        bot_b_token: "token-b".into(),
        system_messages,
        bot_a: "bot-a".into(),
        bot_b: "bot-b".into(),
        sid,
    }
}

fn auth_request(
    method: Method,
    uri: &str,
    token: &str,
    body: Option<Vec<u8>>,
) -> Request<Body> {
    let builder = Request::builder()
        .method(method)
        .uri(uri)
        .header(header::AUTHORIZATION, format!("Bearer {token}"));
    if let Some(b) = body {
        builder
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(b))
            .unwrap()
    } else {
        builder.body(Body::empty()).unwrap()
    }
}

async fn send(
    app: &TestApp,
    req: Request<Body>,
) -> (StatusCode, serde_json::Value, Option<HeaderValue>) {
    let router = build_api_router(app.state.clone());
    let resp = router.oneshot(req).await.expect("router oneshot");
    let status = resp.status();
    let cd = resp
        .headers()
        .get(header::CONTENT_DISPOSITION)
        .cloned();
    let bytes = to_bytes(resp.into_body(), usize::MAX).await.expect("body bytes");
    let json: serde_json::Value = if bytes.is_empty() {
        serde_json::Value::Null
    } else {
        serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null)
    };
    (status, json, cd)
}

fn post_json(
    app: &TestApp,
    uri: &str,
    token: &str,
    body: serde_json::Value,
) -> Request<Body> {
    let _ = app;
    auth_request(
        Method::POST,
        uri,
        token,
        Some(serde_json::to_vec(&body).unwrap()),
    )
}

fn put_bytes(app: &TestApp, uri: &str, token: &str, body: Vec<u8>) -> Request<Body> {
    let _ = app;
    let builder = Request::builder()
        .method(Method::PUT)
        .uri(uri)
        .header(header::AUTHORIZATION, format!("Bearer {token}"))
        .header(header::CONTENT_TYPE, "application/octet-stream")
        .header(header::CONTENT_LENGTH, body.len().to_string());
    builder.body(Body::from(body)).unwrap()
}

// ------------------- Tests -------------------

#[tokio::test]
async fn three_stage_upload_complete_download_roundtrip() {
    let app = build_test_app().await;

    // 1. Prepare
    let prepare_uri = format!("/sessions/{}/files", app.sid);
    let req = post_json(
        &app,
        &prepare_uri,
        &app.bot_a_token,
        json!({
            "file_name": "hello.txt",
            "size": 5u64,
            "mime_type": "text/plain",
        }),
    );
    let (status, body, _) = send(&app, req).await;
    assert_eq!(status, StatusCode::CREATED, "prepare body: {body:?}");
    let file_id = body
        .get("file_id")
        .and_then(|v| v.as_str())
        .expect("file_id in prepare response")
        .to_string();
    assert!(!file_id.is_empty());

    // For the local proxy backend, FakeStoragePlugin returns ProxyViaBcs,
    // so the service synthesizes a single-part URL (mode=single).
    assert_eq!(
        body.get("mode").and_then(|v| v.as_str()),
        Some("single")
    );

    // 2. Upload (single part)
    let upload_uri = format!(
        "/sessions/{}/files/{}/content",
        app.sid, file_id
    );
    let req = put_bytes(&app, &upload_uri, &app.bot_a_token, b"hello".to_vec());
    let (status, body, _) = send(&app, req).await;
    assert_eq!(status, StatusCode::ACCEPTED, "upload body: {body:?}");

    // 3. Complete
    let complete_uri = format!(
        "/sessions/{}/files/{}/complete",
        app.sid, file_id
    );
    let req = post_json(&app, &complete_uri, &app.bot_a_token, json!({}));
    let (status, body, _) = send(&app, req).await;
    assert_eq!(status, StatusCode::OK, "complete body: {body:?}");
    assert_eq!(
        body.get("status").and_then(|v| v.as_str()),
        Some("Ready")
    );
    assert_eq!(
        body.get("file_id").and_then(|v| v.as_str()),
        Some(file_id.as_str())
    );
    assert_eq!(
        body.get("size").and_then(|v| v.as_u64()),
        Some(5)
    );

    // 4. Download — local backend streams via get_stream, no 302.
    let download_uri = format!(
        "/sessions/{}/files/{}/content",
        app.sid, file_id
    );
    let req = auth_request(
        Method::GET,
        &download_uri,
        &app.bot_a_token,
        None,
    );
    let (status, _body, cd) = send(&app, req).await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        cd.is_some(),
        "expected Content-Disposition on streamed download"
    );
    let cd = cd.unwrap().to_str().unwrap().to_string();
    assert!(cd.contains("attachment"), "cd: {cd}");
    assert!(cd.contains("hello.txt"), "cd: {cd}");
}

#[tokio::test]
async fn capabilities_route_not_shadowed_by_file_id() {
    // Build the router then call GET /sessions/s1/files/capabilities with a
    // known bot member. The static segment MUST resolve to the
    // `capabilities` handler returning a CapabilitiesView JSON body — not
    // be treated as `file_id = "capabilities"`.
    let app = build_test_app().await;
    let uri = format!("/sessions/{}/files/capabilities", app.sid);
    let req = auth_request(Method::GET, &uri, &app.bot_a_token, None);
    let (status, body, _) = send(&app, req).await;
    assert_eq!(status, StatusCode::OK, "capabilities body: {body:?}");
    assert!(
        body.get("storage").is_some(),
        "expected CapabilitiesView JSON: {body:?}"
    );
    assert_eq!(
        body.get("presign_upload").and_then(|v| v.as_bool()),
        Some(false)
    );
    assert_eq!(
        body.get("inline_view").and_then(|v| v.as_bool()),
        Some(true),
        "expected inline_view in capabilities: {body:?}"
    );
}

#[tokio::test]
async fn delete_ready_is_204_and_idempotent() {
    let app = build_test_app().await;

    // Prepare + upload + complete a file owned by bot-a.
    let (file_id, _) = upload_complete(&app, "del.txt", b"bye").await;

    // First delete -> 204.
    let uri = format!("/sessions/{}/files/{}", app.sid, file_id);
    let req = auth_request(Method::DELETE, &uri, &app.bot_a_token, None);
    let (status, body, _) = send(&app, req).await;
    assert_eq!(status, StatusCode::NO_CONTENT, "delete body: {body:?}");

    // Repeat delete (idempotent) -> 204. Even though the row is gone, the
    // service's `delete_file` returns Ok(()) for absent rows.
    let req = auth_request(Method::DELETE, &uri, &app.bot_a_token, None);
    let (status, body, _) = send(&app, req).await;
    assert_eq!(status, StatusCode::NO_CONTENT, "repeat delete body: {body:?}");
}

#[tokio::test]
async fn delete_someone_else_file_is_403() {
    // bot-a uploads, bot-b attempts to delete — bot-b is a participant but
    // does not own the file and is neither session_creator nor driver_bot.
    let app = build_test_app().await;
    let (file_id, _) = upload_complete(&app, "owned.txt", b"x").await;

    let uri = format!("/sessions/{}/files/{}", app.sid, file_id);
    let req = auth_request(Method::DELETE, &uri, &app.bot_b_token, None);
    let (status, body, _) = send(&app, req).await;
    assert_eq!(
        status,
        StatusCode::FORBIDDEN,
        "expected 403 forbidding bot-b delete, body: {body:?}"
    );
}

#[tokio::test]
async fn share_mint_then_consume() {
    let app = build_test_app().await;
    let (file_id, _) = upload_complete(&app, "share.txt", b"s").await;

    // Mint share token.
    let mint_uri = format!("/sessions/{}/files/{}/share", app.sid, file_id);
    let req = post_json(&app, &mint_uri, &app.bot_a_token, json!({}));
    let (status, body, _) = send(&app, req).await;
    assert_eq!(status, StatusCode::CREATED, "share mint body: {body:?}");
    let token = body
        .get("share_token")
        .and_then(|v| v.as_str())
        .expect("share_token in mint response")
        .to_string();
    assert!(!token.is_empty());

    // Consume via shared_file_meta — 200 with file DTO, no session_id in response.
    let meta_uri = format!("/sessions/shared-file?token={}", token);
    let req = auth_request(Method::GET, &meta_uri, &app.bot_a_token, None);
    let (status, body, _) = send(&app, req).await;
    assert_eq!(status, StatusCode::OK, "share consume body: {body:?}");
    assert_eq!(
        body.get("file_id").and_then(|v| v.as_str()),
        Some(file_id.as_str())
    );
    assert_eq!(
        body.get("status").and_then(|v| v.as_str()),
        Some("Ready")
    );
    // Meta response must NOT contain session_id.
    assert!(
        body.get("session_id").is_none(),
        "shared-file meta response must not expose session_id: {body:?}"
    );

    // Consume via shared_file_content — token-only (no sid, no member
    // auth), local backend streams with Content-Disposition.
    let content_uri = format!("/sessions/shared-file/content?token={}", token);
    let req = auth_request(Method::GET, &content_uri, &app.bot_a_token, None);
    let (status, _body, cd) = send(&app, req).await;
    assert_eq!(status, StatusCode::OK, "share content body: {_body:?}");
    let cd = cd.expect("content-disposition on shared-file content");
    let s = cd.to_str().unwrap();
    assert!(s.contains("attachment"), "cd: {s}");
    assert!(s.contains("share.txt"), "cd: {s}");
}

#[tokio::test]
async fn shared_file_show_true_uses_inline_disposition() {
    let app = build_test_app().await;
    let (file_id, _) = upload_complete(&app, "share.txt", b"s").await;

    // Mint share token (verbatim steps from share_mint_then_consume).
    let mint_uri = format!("/sessions/{}/files/{}/share", app.sid, file_id);
    let req = post_json(&app, &mint_uri, &app.bot_a_token, json!({}));
    let (status, body, _) = send(&app, req).await;
    assert_eq!(status, StatusCode::CREATED, "share mint body: {body:?}");
    let token = body
        .get("share_token")
        .and_then(|v| v.as_str())
        .expect("share_token in mint response")
        .to_string();

    // Consume shared-file content with show=true → inline disposition.
    let query_key = "token";
    let content_uri = format!("/sessions/shared-file/content?{query_key}={token}&show=true");
    let req = auth_request(Method::GET, &content_uri, &app.bot_a_token, None);
    let (status, _body, cd) = send(&app, req).await;
    assert_eq!(status, StatusCode::OK);
    let cd = cd.expect("content-disposition on shared-file content");
    let s = cd.to_str().unwrap();
    assert!(s.contains("inline"), "shared-file show=true must be inline, got: {s}");
    assert!(s.contains("share.txt"), "cd: {s}");
}

#[tokio::test]
async fn shared_file_meta_no_token_is_401() {
    let app = build_test_app().await;
    let uri = "/sessions/shared-file";
    let req = auth_request(Method::GET, uri, &app.bot_a_token, None);
    let (status, body, _) = send(&app, req).await;
    assert_eq!(
        status,
        StatusCode::UNAUTHORIZED,
        "expected 401 for missing token, body: {body:?}"
    );
    assert_eq!(
        body.get("error").and_then(|v| v.as_str()),
        Some("UNAUTHORIZED")
    );
}

#[tokio::test]
async fn shared_file_content_no_token_is_401() {
    // Guards the *content* static route wiring: no token ⇒ 401 from the
    // shared-file handler, not 404 from the `/sessions/{sid}` param route.
    let app = build_test_app().await;
    let uri = "/sessions/shared-file/content";
    let req = auth_request(Method::GET, uri, &app.bot_a_token, None);
    let (status, body, _) = send(&app, req).await;
    assert_eq!(
        status,
        StatusCode::UNAUTHORIZED,
        "expected 401 for missing token on content, body: {body:?}"
    );
}

#[tokio::test]
async fn share_consume_expired_token_is_404() {
    // Mint a valid token, then construct a clone with `exp` in the past via
    // the domain share encode function. Consume must return uniform 404 —
    // no 422/410 leakage that reveals the token was expired.
    let app = build_test_app().await;
    let (file_id, _) = upload_complete(&app, "share-exp.txt", b"e").await;

    let mint_uri = format!("/sessions/{}/files/{}/share", app.sid, file_id);
    let req = post_json(&app, &mint_uri, &app.bot_a_token, json!({}));
    let (_status, body, _) = send(&app, req).await;
    let valid_token = body
        .get("share_token")
        .and_then(|v| v.as_str())
        .unwrap()
        .to_string();

    // Re-encode an expired token with the same file_id using the app's share secret.
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let expired_token = bcs_domain::share_token_encode(
        &bcs_domain::ShareTokenPayload {
            v: 1,
            file_id: file_id.clone(),
            exp: now.saturating_sub(10),
        },
        b"test-secret-32-bytes-0123456789",
    );
    assert_ne!(valid_token, expired_token);

    let meta_uri = format!("/sessions/shared-file?token={}", expired_token);
    let req = auth_request(Method::GET, &meta_uri, &app.bot_a_token, None);
    let (status, body, _) = send(&app, req).await;
    assert_eq!(
        status,
        StatusCode::NOT_FOUND,
        "expected uniform 404 for expired share token, got {status}; body: {body:?}",
    );
    assert_eq!(
        body.get("error").and_then(|v| v.as_str()),
        Some("NOT_FOUND")
    );
}

#[tokio::test]
async fn share_consume_tampered_token_is_404() {
    // Mint a valid token, tamper one character, consume must return uniform
    // 404 — no 400/401 leakage that reveals the token was tampered.
    let app = build_test_app().await;
    let (file_id, _) = upload_complete(&app, "share-tamper.txt", b"t").await;

    let mint_uri = format!("/sessions/{}/files/{}/share", app.sid, file_id);
    let req = post_json(&app, &mint_uri, &app.bot_a_token, json!({}));
    let (_status, body, _) = send(&app, req).await;
    let token = body
        .get("share_token")
        .and_then(|v| v.as_str())
        .unwrap()
        .to_string();

    let mut chars: Vec<char> = token.chars().collect();
    let idx = chars.len() - 1;
    let last = chars[idx];
    chars[idx] = if last == 'a' { 'b' } else { 'a' };
    let tampered: String = chars.into_iter().collect();

    let meta_uri = format!("/sessions/shared-file?token={}", tampered);
    let req = auth_request(Method::GET, &meta_uri, &app.bot_a_token, None);
    let (status, body, _) = send(&app, req).await;
    assert_eq!(
        status,
        StatusCode::NOT_FOUND,
        "expected uniform 404 for tampered share token, got {status}; body: {body:?}",
    );
    assert_eq!(
        body.get("error").and_then(|v| v.as_str()),
        Some("NOT_FOUND")
    );
}

#[tokio::test]
async fn local_download_streams_with_content_disposition() {
    // Build a non-multipart upload, complete, and verify the GET response
    // carries Content-Disposition: attachment; filename="...".
    let app = build_test_app().await;
    let (file_id, _) = upload_complete(&app, "disp.txt", b"abc").await;

    let uri = format!(
        "/sessions/{}/files/{}/content",
        app.sid, file_id
    );
    let req = auth_request(Method::GET, &uri, &app.bot_a_token, None);
    let (status, _body, cd) = send(&app, req).await;
    assert_eq!(status, StatusCode::OK);
    let cd = cd.expect("content-disposition header");
    let s = cd.to_str().unwrap();
    assert!(s.contains("attachment"), "cd: {s}");
    assert!(s.contains("disp.txt"), "cd: {s}");
}

#[tokio::test]
async fn local_download_show_true_uses_inline_disposition() {
    let app = build_test_app().await;
    let (file_id, _) = upload_complete(&app, "view.txt", b"abc").await;
    let uri = format!("/sessions/{}/files/{}/content?show=true", app.sid, file_id);
    let req = auth_request(Method::GET, &uri, &app.bot_a_token, None);
    let (status, _body, cd) = send(&app, req).await;
    assert_eq!(status, StatusCode::OK);
    let cd = cd.expect("content-disposition header");
    let s = cd.to_str().unwrap();
    assert!(s.contains("inline"), "expected inline disposition for show=true, got: {s}");
    assert!(s.contains("view.txt"), "filename must still be present: {s}");
}

#[tokio::test]
async fn local_download_show_false_and_absent_still_attach() {
    let app = build_test_app().await;
    let (file_id, _) = upload_complete(&app, "dl.txt", b"abc").await;
    for q in &["?show=false", ""] {
        let uri = format!("/sessions/{}/files/{}/content{}", app.sid, file_id, q);
        let req = auth_request(Method::GET, &uri, &app.bot_a_token, None);
        let (status, _body, cd) = send(&app, req).await;
        assert_eq!(status, StatusCode::OK);
        let s = cd.expect("content-disposition").to_str().unwrap().to_string();
        assert!(s.contains("attachment"), "absent/show=false must attach: {s}");
        assert!(s.contains("dl.txt"), "cd: {s}");
    }
}

#[tokio::test]
async fn upload_accepts_large_chunked_body() {
    // Regression: the proxy `PUT .../content` route must NOT be subject to
    // axum's default 2 MiB body limit, and must accept a chunked upload
    // (no Content-Length header) — which is what the CLI sends via
    // `reqwest::Body::wrap_stream`. Before the fix this returned
    // 413 "Failed to buffer the request body: length limit exceeded".
    //
    // Use a 3 MiB body (>2 MiB default limit) and a max_size large enough
    // to prepare it. The body is sent as a stream with no Content-Length,
    // exercising the service's `content_length == 0` (unknown) path that
    // relies on the backend's per-chunk cap.
    let app = build_test_app_with_max_size(5 * 1024 * 1024).await;
    let size: u64 = 3 * 1024 * 1024;

    // 1. Prepare
    let prepare_uri = format!("/sessions/{}/files", app.sid);
    let req = post_json(
        &app,
        &prepare_uri,
        &app.bot_a_token,
        json!({ "file_name": "big.bin", "size": size, "mime_type": "application/octet-stream" }),
    );
    let (status, body, _) = send(&app, req).await;
    assert_eq!(status, StatusCode::CREATED, "prepare body: {body:?}");
    let file_id = body.get("file_id").and_then(|v| v.as_str()).unwrap().to_string();
    assert_eq!(body.get("mode").and_then(|v| v.as_str()), Some("single"));

    // 2. Upload — chunked (no Content-Length), 3 MiB body.
    let upload_uri = format!("/sessions/{}/files/{}/content", app.sid, file_id);
    let payload = Bytes::from(vec![0u8; size as usize]);
    let stream = futures::stream::once(async move { Ok::<Bytes, std::io::Error>(payload) });
    let req = Request::builder()
        .method(Method::PUT)
        .uri(&upload_uri)
        .header(header::AUTHORIZATION, format!("Bearer {}", app.bot_a_token))
        .header(header::CONTENT_TYPE, "application/octet-stream")
        // NOTE: deliberately NO Content-Length — chunked transfer.
        .body(Body::from_stream(stream))
        .unwrap();
    let (status, body, _) = send(&app, req).await;
    assert_eq!(status, StatusCode::ACCEPTED, "upload body: {body:?}");

    // 3. Complete — backend-reported size must equal the 3 MiB prepared.
    let complete_uri = format!("/sessions/{}/files/{}/complete", app.sid, file_id);
    let req = post_json(&app, &complete_uri, &app.bot_a_token, json!({}));
    let (status, body, _) = send(&app, req).await;
    assert_eq!(status, StatusCode::OK, "complete body: {body:?}");
    assert_eq!(body.get("status").and_then(|v| v.as_str()), Some("Ready"));
    assert_eq!(body.get("size").and_then(|v| v.as_u64()), Some(size));
}

#[tokio::test]
async fn complete_upload_fires_generic_notification_to_other_bots() {
    let app = build_test_app().await;
    // bot-a uploads; the session also has bot-b, so receivers must be [bot-b].
    let (file_id, _status) = upload_complete(&app, "hello.txt", b"hello").await;

    // The download link in the notification is a no-auth share link (15-day
    // TTL) instead of the authenticated content endpoint.
    let expected_prefix = format!(
        "Bot test-bot 上传了一个文件 hello.txt ({file_id}，5 B)，下载链接：",
    );
    let expected_share_prefix =
        "http://test.local/sessions/shared-file/content?token=";

    let notifications = app.system_messages.notifications.lock().await;
    assert_eq!(notifications.len(), 1, "expected exactly one system message");
    match &notifications[0] {
        SystemMessageEvent::GenericNotification {
            group_id,
            message,
            receivers,
        } => {
            assert_eq!(group_id, "g1");
            assert!(
                message.starts_with(&expected_prefix),
                "unexpected message: {message}",
            );
            assert!(
                message[expected_prefix.len()..].starts_with(expected_share_prefix),
                "expected a share link, got: {message}",
            );
            let receiver_ids: Vec<String> =
                receivers.iter().map(|p| p.bot_uuid.clone()).collect();
            assert_eq!(receiver_ids, vec!["bot-b".to_string()]);
        }
        other => panic!("expected GenericNotification, got {other:?}"),
    }
}

#[tokio::test]
async fn complete_upload_failure_does_not_notify() {
    let app = build_test_app().await;
    // Complete a non-existent file_id -> 404; no system message.
    let uri = format!("/sessions/{}/files/nope/complete", app.sid);
    let req = post_json(&app, &uri, &app.bot_a_token, json!({}));
    let (status, _body, _) = send(&app, req).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    let notifications = app.system_messages.notifications.lock().await;
    assert!(notifications.is_empty(), "no notify on failed complete");
}

// ------------------- Internal test helpers -------------------

/// Run the full prepare→upload→complete sequence and return the file_id
/// plus the file's status slug. Uploads as bot-a (the session's driver).
async fn upload_complete(app: &TestApp, name: &str, bytes: &[u8]) -> (String, FileStatus) {
    // Prepare
    let prepare_uri = format!("/sessions/{}/files", app.sid);
    let req = post_json(
        &app,
        &prepare_uri,
        &app.bot_a_token,
        json!({
            "file_name": name,
            "size": bytes.len() as u64,
            "mime_type": "text/plain",
        }),
    );
    let (status, body, _) = send(app, req).await;
    assert_eq!(status, StatusCode::CREATED, "prepare body: {body:?}");
    let file_id = body
        .get("file_id")
        .and_then(|v| v.as_str())
        .unwrap()
        .to_string();

    // Upload (single part)
    let upload_uri = format!(
        "/sessions/{}/files/{}/content",
        app.sid, file_id
    );
    let req = put_bytes(app, &upload_uri, &app.bot_a_token, bytes.to_vec());
    let (status, body, _) = send(app, req).await;
    assert_eq!(status, StatusCode::ACCEPTED, "upload body: {body:?}");

    // Complete
    let complete_uri = format!(
        "/sessions/{}/files/{}/complete",
        app.sid, file_id
    );
    let req = post_json(&app, &complete_uri, &app.bot_a_token, json!({}));
    let (status, body, _) = send(app, req).await;
    assert_eq!(status, StatusCode::OK, "complete body: {body:?}");

    let status_slug = body
        .get("status")
        .and_then(|v| v.as_str())
        .unwrap()
        .to_string();
    let file_status = match status_slug.as_str() {
        "Ready" => FileStatus::Ready,
        "Pending" => FileStatus::Pending,
        "Deleting" => FileStatus::Deleting,
        _ => FileStatus::Failed,
    };
    (file_id, file_status)
}
