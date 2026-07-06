//! HTTP contract tests for POST /providers/{provider_id}/delivery/switch-bot.

use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_bot::{Bot, BotCore, ProviderCore, ProviderManagement};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_service_api::{
    BotConnectionControlPort, BotDeliveryCommand, BotDeliveryPort, BotDeliveryResult,
    BotDeliveryTarget, BotRegistryCoreService, KickReason, ProviderBotBindingRepoPort,
    ProviderCoreService, ProviderCredentialRepoPort, ProviderRepoPort,
};
use bcs_test_support::NoopRelationCoreService;
use bcs_services_container::Services;
use serde_json::{json, Value};
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

#[derive(Default)]
struct RecordingKickPort {
    calls: Mutex<Vec<(String, String)>>,
}

impl RecordingKickPort {
    fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }
}

#[async_trait::async_trait]
impl BotConnectionControlPort for RecordingKickPort {
    async fn kick(&self, bot_id: &str, reason: KickReason) -> bool {
        self.calls
            .lock()
            .await
            .push((bot_id.to_string(), reason.as_str().to_string()));
        true
    }
}

#[derive(Default)]
struct RecordingDeliveryPort;

#[async_trait::async_trait]
impl BotDeliveryPort for RecordingDeliveryPort {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        true
    }

    async fn deliver(
        &self,
        _cmd: BotDeliveryCommand,
    ) -> bcs_service_api::ServiceResult<BotDeliveryResult> {
        Ok(BotDeliveryResult {
            target_bot_id: "test".to_string(),
            delivered: true,
            error: None,
        })
    }
}

/// Builder-style fixture: stores + services first, app built after via `build_app`.
struct Fixture {
    bot_core: Arc<BotCore>,
    provider: ProviderCore,
    kick: Arc<RecordingKickPort>,
    services: Services,
    _temp_dir: TempDir,
}

impl Fixture {
    fn new() -> Self {
        let temp_dir = tempfile::tempdir().expect("temp dir");
        let provider_store = Arc::new(MemoryProviderStore::new());
        let provider_repo: Arc<dyn ProviderRepoPort> = provider_store.clone();
        let provider_credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
        let provider_bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
        let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
        let bot_core = Arc::new(BotCore::with_provider_repos(
            bot_repo,
            provider_repo.clone(),
            provider_credentials.clone(),
            provider_bindings.clone(),
        ));
        let provider = ProviderCore::new(
            provider_repo.clone(),
            provider_credentials,
            provider_bindings.clone(),
            bot_core.clone(),
        );

        let registry_service: Arc<dyn BotRegistryCoreService> = bot_core.clone();
        let relation = Arc::new(NoopRelationCoreService);
        let kick = RecordingKickPort::new();
        let delivery: Arc<dyn BotDeliveryPort> = Arc::new(RecordingDeliveryPort);

        let bot = Bot::new(bot_core.clone() as Arc<dyn BotRegistryCoreService>)
            .with_bot_core(bot_core.clone())
            .with_relation(relation.clone())
            .with_connection_control(kick.clone());

        let services = Services::builder()
            .registry(registry_service.clone())
            .bot_query(Arc::new(bot.clone()))
            .bot_management(Arc::new(bot))
            .provider_core(Arc::new(provider.clone()))
            .provider_bot_core(Arc::new(provider.clone()))
            .provider_management(Arc::new(ProviderManagement::new(
                Arc::new(provider.clone()),
                Arc::new(provider.clone()),
                registry_service.clone(),
                relation,
            )))
            .bot_delivery(delivery)
            .build_for_test();

        Self {
            bot_core,
            provider,
            kick,
            services,
            _temp_dir: temp_dir,
        }
    }

    fn build_app(&self, allowed_provider_ids: Vec<String>) -> axum::Router {
        let state = HttpAppState::new(self.services.clone())
            .with_allowed_switch_provider_ids(allowed_provider_ids);
        build_router(state)
    }

    async fn register_bot(&self, bot_id: &str, staff_no: &str) {
        self.bot_core
            .register_with_owner_and_token(
                bot_id.to_string(),
                bcs_service_api::BotCapabilities::default(),
                staff_no,
                "token-irrelevant",
            )
            .await
            .expect("register bot");
    }

    async fn register_provider(&self, owner: &str) -> (String, String) {
        let outcome = self
            .provider
            .register_provider(
                "TestProvider".to_string(),
                "https://provider.example.com/webhook".to_string(),
                bcs_service_api::ProviderAuthMode::StaticBearer,
                owner.to_string(),
                None,
                None,
            )
            .await
            .expect("register provider");
        (outcome.provider.provider_id, outcome.provider_admin_token)
    }
}

async fn post_switch(
    app: &axum::Router,
    provider_id: &str,
    admin_token: &str,
    bot_id: &str,
    provider_bot_ref: &str,
) -> (StatusCode, Value) {
    let req = Request::builder()
        .method("POST")
        .uri(format!("/providers/{}/delivery/switch-bot", provider_id))
        .header("content-type", "application/json")
        .header("authorization", format!("Bearer {}", admin_token))
        .body(Body::from(
            json!({
                "bot_id": bot_id,
                "provider_bot_ref": provider_bot_ref,
            })
            .to_string(),
        ))
        .unwrap();
    let response = app.clone().oneshot(req).await.unwrap();
    let status = response.status();
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap_or_else(|_| json!({}));
    (status, json)
}

async fn post_switch_with_metadata(
    app: &axum::Router,
    provider_id: &str,
    admin_token: &str,
    bot_id: &str,
    provider_bot_ref: &str,
    name: &str,
    summary: &str,
) -> (StatusCode, Value) {
    let req = Request::builder()
        .method("POST")
        .uri(format!("/providers/{}/delivery/switch-bot", provider_id))
        .header("content-type", "application/json")
        .header("authorization", format!("Bearer {}", admin_token))
        .body(Body::from(
            json!({
                "bot_id": bot_id,
                "provider_bot_ref": provider_bot_ref,
                "name": name,
                "summary": summary,
            })
            .to_string(),
        ))
        .unwrap();
    let response = app.clone().oneshot(req).await.unwrap();
    let status = response.status();
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap_or_else(|_| json!({}));
    (status, json)
}

async fn post_switch_no_token(
    app: &axum::Router,
    provider_id: &str,
    bot_id: &str,
    provider_bot_ref: &str,
) -> (StatusCode, Value) {
    let req = Request::builder()
        .method("POST")
        .uri(format!("/providers/{}/delivery/switch-bot", provider_id))
        .header("content-type", "application/json")
        .body(Body::from(
            json!({
                "bot_id": bot_id,
                "provider_bot_ref": provider_bot_ref,
            })
            .to_string(),
        ))
        .unwrap();
    let response = app.clone().oneshot(req).await.unwrap();
    let status = response.status();
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap_or_else(|_| json!({}));
    (status, json)
}

// ---- Auth failure tests ----

#[tokio::test]
async fn test_401_when_no_bearer_token() {
    let fixture = Fixture::new();
    fixture.register_bot("bot-1", "alice").await;
    let (provider_id, _) = fixture.register_provider("alice").await;
    let app = fixture.build_app(vec![provider_id.clone()]);

    let (status, _) = post_switch_no_token(&app, &provider_id, "bot-1", "ref-1:alice").await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn test_401_when_invalid_admin_token() {
    let fixture = Fixture::new();
    fixture.register_bot("bot-1", "alice").await;
    let (provider_id, _) = fixture.register_provider("alice").await;
    let app = fixture.build_app(vec![provider_id.clone()]);

    let (status, _) = post_switch(&app, &provider_id, "invalid-token", "bot-1", "ref-1:alice").await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn test_403_when_token_belongs_to_different_provider() {
    let fixture = Fixture::new();
    fixture.register_bot("bot-1", "alice").await;
    let (_, other_token) = fixture.register_provider("bob").await;
    let (target_provider_id, _) = fixture.register_provider("alice").await;
    let app = fixture.build_app(vec![target_provider_id.clone()]);

    // bob's admin token used against alice's provider path
    let (status, json) =
        post_switch(&app, &target_provider_id, &other_token, "bot-1", "ref-1:alice").await;

    assert_eq!(status, StatusCode::FORBIDDEN);
    assert!(json["error"]
        .as_str()
        .unwrap()
        .contains("provider_id_mismatch"));
}

#[tokio::test]
async fn test_403_when_provider_not_in_whitelist() {
    let fixture = Fixture::new();
    fixture.register_bot("bot-1", "alice").await;
    let (provider_id, admin_token) = fixture.register_provider("alice").await;
    // Whitelist empty
    let app = fixture.build_app(vec![]);

    let (status, json) = post_switch(&app, &provider_id, &admin_token, "bot-1", "ref-1:alice").await;

    assert_eq!(status, StatusCode::FORBIDDEN);
    assert!(json["error"].as_str().unwrap().contains("not allowed"));
}

// ---- Validation tests ----

#[tokio::test]
async fn test_400_when_provider_bot_ref_empty() {
    let fixture = Fixture::new();
    fixture.register_bot("bot-1", "alice").await;
    let (provider_id, admin_token) = fixture.register_provider("alice").await;
    let app = fixture.build_app(vec![provider_id.clone()]);

    let (status, json) = post_switch(&app, &provider_id, &admin_token, "bot-1", "   ").await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(
        json["error"].as_str().unwrap().contains("empty")
            || json["error"].as_str().unwrap().contains("INVALID")
    );
}

#[tokio::test]
async fn test_400_when_provider_bot_ref_missing_owner_suffix() {
    let fixture = Fixture::new();
    fixture.register_bot("bot-1", "alice").await;
    let (provider_id, admin_token) = fixture.register_provider("alice").await;
    let app = fixture.build_app(vec![provider_id.clone()]);

    let (status, json) =
        post_switch(&app, &provider_id, &admin_token, "bot-1", "ref-without-owner").await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(json["error"].as_str().unwrap().contains("owner staff_no"));
}

#[tokio::test]
async fn test_400_when_bot_id_empty() {
    let fixture = Fixture::new();
    let (provider_id, admin_token) = fixture.register_provider("alice").await;
    let app = fixture.build_app(vec![provider_id.clone()]);

    let (status, json) = post_switch(&app, &provider_id, &admin_token, "   ", "ref-1:alice").await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(json["error"].as_str().unwrap().contains("bot_id"));
}

// ---- Happy path tests ----

#[tokio::test]
async fn test_200_happy_path_fresh_binding() {
    let fixture = Fixture::new();
    fixture.register_bot("bot-1", "alice").await;
    let (provider_id, admin_token) = fixture.register_provider("alice").await;
    let app = fixture.build_app(vec![provider_id.clone()]);

    let (status, json) = post_switch(&app, &provider_id, &admin_token, "bot-1", "ref-1:alice").await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["bot_id"], "bot-1");
    assert_eq!(json["data"]["provider_id"], provider_id);
    assert_eq!(json["data"]["provider_bot_ref"], "ref-1:alice");
    assert_eq!(json["data"]["idempotent_replay"], false);
    assert_eq!(json["data"]["websocket_kicked"], true);
    assert!(json["data"]["binding_created_at"].as_u64().unwrap() > 0);

    let kicks = fixture.kick.calls.lock().await;
    assert_eq!(kicks.len(), 1);
    assert_eq!(kicks[0].0, "bot-1");
}

#[tokio::test]
async fn test_200_idempotent_replay_same_binding() {
    let fixture = Fixture::new();
    fixture.register_bot("bot-1", "alice").await;
    let (provider_id, admin_token) = fixture.register_provider("alice").await;
    let app = fixture.build_app(vec![provider_id.clone()]);

    let (status, json) = post_switch(&app, &provider_id, &admin_token, "bot-1", "ref-1:alice").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(json["data"]["idempotent_replay"], false);

    let (status, json) = post_switch(&app, &provider_id, &admin_token, "bot-1", "ref-1:alice").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["idempotent_replay"], true);
    assert_eq!(json["data"]["bot_id"], "bot-1");
}

#[tokio::test]
async fn test_409_when_binding_exists_and_disagrees() {
    let fixture = Fixture::new();
    fixture.register_bot("bot-1", "alice").await;
    let (provider_id, admin_token) = fixture.register_provider("alice").await;
    let app = fixture.build_app(vec![provider_id.clone()]);

    let (status, _) = post_switch(&app, &provider_id, &admin_token, "bot-1", "ref-1:alice").await;
    assert_eq!(status, StatusCode::OK);

    let (status, json) = post_switch(&app, &provider_id, &admin_token, "bot-1", "ref-2:alice").await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert!(json["error"].as_str().unwrap().contains("already bound"));
}

#[tokio::test]
async fn test_200_when_bot_missing_auto_onboards() {
    let fixture = Fixture::new();
    let (provider_id, admin_token) = fixture.register_provider("alice").await;
    let app = fixture.build_app(vec![provider_id.clone()]);

    let (status, json) = post_switch_with_metadata(
        &app,
        &provider_id,
        &admin_token,
        "teamclaw-bot:alice",
        "teamclaw-bot:alice",
        "Teamclaw Bot",
        "Handles Teamclaw tasks",
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["bot_id"], "teamclaw-bot:alice");
    assert_eq!(json["data"]["provider_id"], provider_id);
    assert_eq!(json["data"]["provider_bot_ref"], "teamclaw-bot:alice");

    assert!(fixture.bot_core.has_been_onboarded("teamclaw-bot:alice").await);
    let caps = fixture
        .bot_core
        .load_from_storage("teamclaw-bot:alice")
        .await
        .expect("auto onboarded capabilities should be persisted");
    assert_eq!(caps.name.as_deref(), Some("Teamclaw Bot"));
    assert_eq!(caps.summary.as_deref(), Some("Handles Teamclaw tasks"));
    assert!(fixture
        .bot_core
        .load_token("teamclaw-bot:alice")
        .await
        .as_deref()
        .is_some_and(|token| !token.is_empty()));
}
