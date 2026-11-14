//! Exercise HTTP admission with the real A2aChat, not a validation-copying fake.
use super::*;
use bcs_message_flow::{MemoryBotRunContextStore, a2a_chat::{A2aChat, ChatRunStore}};
use bcs_service_api::{BotRunContextPort, DirectChatRunSnapshotPort, application::a2a_bot::*};
use std::sync::atomic::{AtomicUsize, Ordering::SeqCst};

struct NamedTarget;
#[async_trait::async_trait]
impl A2aBotManagementService for NamedTarget {
    async fn resolve_named_target(&self, provider: &str, bot: &str) -> ServiceResult<String> {
        assert_eq!((provider, bot), ("provider", "target"));
        Ok("target-bot".into())
    }
    async fn get(&self, _: &str, _: &str, _: &str) -> ServiceResult<Option<bcs_service_api::types::a2a_bot::A2aBotRecord>> { unreachable!() }
    async fn configure(&self, _: ConfigureA2aBotCommand) -> ServiceResult<bcs_service_api::types::a2a_bot::A2aBotRecord> { unreachable!() }
    async fn refresh(&self, _: &str, _: &str, _: &str, _: i64) -> ServiceResult<bcs_service_api::types::a2a_bot::A2aBotRecord> { unreachable!() }
    async fn refresh_due(&self, _: i64) -> ServiceResult<usize> { unreachable!() }
}

#[derive(Default)]
struct Delivery {
    sends: AtomicUsize,
    messages: Mutex<Vec<String>>,
}
#[async_trait::async_trait]
impl BotDeliveryPort for Delivery {
    async fn is_available(&self, _: &BotDeliveryTarget) -> bool { true }
    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        self.sends.fetch_add(1, SeqCst);
        if let bcs_protocol::BcsFrame::Request(frame) = &cmd.frame {
            self.messages.lock().await.push(frame.params.as_ref().unwrap()["message"]["content"][0]["text"].as_str().unwrap().into());
        }
        Ok(BotDeliveryResult { target_bot_id: cmd.target_bot_id().into(), delivered: true, error: None })
    }
}

struct RealChatApp {
    app: axum::Router,
    direct: Arc<A2aChat>,
    events: Arc<RecordingChatRunEvents>,
    contexts: Arc<MemoryBotRunContextStore>,
    delivery: Arc<Delivery>,
    _dir: TempDir,
}

async fn real_chat_app() -> RealChatApp {
    let dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(dir.path().into()));
    for bot in ["caller-bot", "target-bot"] {
        registry.register(bot.into(), BotCapabilities { visibility: "public".into(), ..Default::default() }).await.unwrap();
    }
    registry.store_token_mapping("caller-token".into(), "caller-bot".into()).await;
    registry.save_created_by("caller-bot", "123", true).await.unwrap();
    let events = Arc::new(RecordingChatRunEvents { events: vec![], keep_open_after_send: true,
        registrations: Mutex::new(vec![]), unregistered: Mutex::new(vec![]), open_senders: RwLock::new(HashMap::new()) });
    let contexts = Arc::new(MemoryBotRunContextStore::new());
    let delivery = Arc::new(Delivery::default());
    let authority = bcs_test_support::lazy_authority_chain();
    authority.hook.seed_owner("caller-bot", "123");
    let direct = Arc::new(A2aChat::new_with_run_ports(delivery.clone(), Arc::new(ChatRunStore::new()), 60_000,
        registry.clone(), Arc::new(bcs_test_support::NoopFriendCoreService), events.clone(),
        Arc::new(bcs_http::state::NoopChatRunCleanupPort)).with_bot_run_context(contexts.clone()).with_bot_authority_hook(Some(authority.hook)));
    let services = Services::builder().registry(registry).a2a_chat(direct.clone()).a2a_chat_runs(direct.clone())
        .a2a_bot_management(Arc::new(NamedTarget)).bot_delivery(delivery.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services)
        .with_user_identity(Arc::new(ChainUserIdentityPort::new(static_auth_chain("123", "Owner"))))
        .with_chat_run_events(events.clone()));
    RealChatApp { app, direct, events, contexts, delivery, _dir: dir }
}

fn request(path: &str, text: &str, authenticated: bool) -> Request<Body> {
    let mut builder = Request::builder().method("POST").uri(path).header("content-type", "application/json");
    if authenticated { builder = builder.header("authorization", "Bearer caller-token"); }
    builder.body(Body::from(serde_json::json!({"message":text,"session_id":"input-session"}).to_string())).unwrap()
}
const PATHS: [&str; 2] = ["/bots/target-bot/chat-async", "/a2a/providers/provider/bots/target/chat-async"];

#[tokio::test]
async fn real_service_rejects_empty_message_with_400_and_no_admission() {
    for path in PATHS {
        let h = real_chat_app().await;
        let response = h.app.oneshot(request(path, "", true)).await.unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        let body: Value = serde_json::from_slice(&to_bytes(response.into_body(), 65536).await.unwrap()).unwrap();
        assert_eq!(body["status"], 400);
        assert_eq!(body["error"], if path == PATHS[0] { "message must not be empty" } else { "Chat admission failed" });
        assert!(body.get("run_id").is_none());
        assert!(h.events.registrations.lock().await.is_empty());
        assert!(h.events.unregistered.lock().await.is_empty());
        assert_eq!(h.delivery.sends.load(SeqCst), 0);
        assert!(h.direct.direct_chat_run_counts().await.unwrap().is_empty());
        assert!(h.contexts.list_active_runs(&bcs_service_api::BotRunScope { group_id: "input-session".into(), session_id: "input-session".into(), bot_id: "target-bot".into() }).await.unwrap().is_empty());
    }
}

#[tokio::test]
async fn empty_message_does_not_bypass_http_caller_authentication() {
    for path in PATHS {
        let h = real_chat_app().await;
        let response = h.app.oneshot(request(path, "", false)).await.unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        assert!(h.events.registrations.lock().await.is_empty());
        assert_eq!(h.delivery.sends.load(SeqCst), 0);
    }
}

#[tokio::test]
async fn real_service_preserves_nonempty_message_bytes_through_http() {
    for path in PATHS {
        for text in ["  hello\n世界  ", " \t\n"] {
            let h = real_chat_app().await;
            let response = h.app.oneshot(request(path, text, true)).await.unwrap();
            assert_eq!(response.status(), StatusCode::ACCEPTED);
            assert_eq!(*h.delivery.messages.lock().await, vec![text.to_string()]);
            assert_eq!(h.events.registrations.lock().await.len(), 1);
        }
    }
}

#[tokio::test]
async fn empty_message_digest_records_rejection_not_acceptance() {
    let logs = capture_tracing_logs(async {
        let h = real_chat_app().await;
        let response = h.app.oneshot(request(PATHS[0], "", true)).await.unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    }).await;
    let digest = logs.lines().find(|line| line.contains("bcs_chat_digest")).expect("chat digest");
    assert!(digest.contains("message_len=0"), "{digest}");
    assert!(digest.contains("status_code=400"), "{digest}");
    assert!(digest.contains("success=false"), "{digest}");
}
