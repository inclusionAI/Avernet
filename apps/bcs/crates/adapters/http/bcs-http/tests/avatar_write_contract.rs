//! HTTP route contract for bot avatar writes on the existing registration
//! and admin profile-update entry points (bot-avatar plan Task 5).
//!
//! Both entries accept the wire `avatar_url` field (omission keeps the
//! stored avatar, a value replaces it, the empty string clears it, `null`
//! and non-strings are rejected at the boundary) while their legacy
//! RESPONSE shapes gain no avatar field. The tests reuse the route-contract
//! fake-service/router construction: the admin route runs over the real
//! `BotOnboarding` application with the real memory registry; the provider
//! route runs over the real provider stack exactly as
//! `provider_routes_contract.rs` builds it.

use std::path::PathBuf;
use std::sync::Arc;

use async_trait::async_trait;
use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::{BotCore, BotOnboarding, ProviderCore, ProviderManagement};
use bcs_bot_store::provider::{MemoryBotProviderStore, MemoryProviderStore};
use bcs_bot_store::MemoryBotRepo;
use bcs_http::{
    router::build_router,
    state::{ChainUserIdentityPort, HttpAppState},
};
use bcs_service_api::{
    BotRegistryCoreService, ProviderBotBindingRepoPort, ProviderCoreService,
    ProviderCredentialRepoPort, ProviderRepoPort, RelationCoreService, ServiceResult,
};
use bcs_services_container::Services;
use serde_json::{Value, json};
use tempfile::TempDir;
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Shared fixtures (route_contract.rs shapes)
// ---------------------------------------------------------------------------

fn static_auth_chain(staff_no: &str, nick_name: &str) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        user_id: Some(staff_no.to_string()),
        user_name: Some(nick_name.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(
        StaticAuthPlugin::with_principal(principal),
    )]))
}

#[derive(Default)]
struct NoopRelation {
    owner_edges: std::sync::Mutex<Vec<String>>,
}

#[async_trait]
impl RelationCoreService for NoopRelation {
    async fn upsert_edge(&self, _edge: bcs_service_api::RelationEdge) -> ServiceResult<()> {
        Ok(())
    }

    async fn delete_edge(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn get_edge(
        &self,
        _a: &str,
        _b: &str,
        _env: &str,
    ) -> ServiceResult<Option<bcs_service_api::RelationEdge>> {
        Ok(None)
    }

    async fn ensure_owner_edges(&self, _human: &str, bot_id: &str, _env: &str) -> ServiceResult<()> {
        self.owner_edges.lock().unwrap().push(bot_id.to_string());
        Ok(())
    }

    async fn ensure_owner_edges_counted(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<bcs_service_api::EnsureOwnerEdgesResult> {
        self.ensure_owner_edges(human_id, bot_id, env).await?;
        Ok(bcs_service_api::EnsureOwnerEdgesResult::default())
    }

    async fn add_friend_edges(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_friend_edges(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_all_friend_edges(&self, _actor_id: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn add_relation_edge(&self, _caller: &str, _target: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn list_friends_via_relation(&self, _actor_id: &str, _env: &str) -> ServiceResult<Vec<String>> {
        Ok(Vec::new())
    }
}

fn bot_record_files(base_dir: &PathBuf) -> Vec<PathBuf> {
    let mut rows = Vec::new();
    let mut stack = vec![base_dir.clone()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
            } else if path.file_name().is_some_and(|name| name == "bot.json") {
                rows.push(path);
            }
        }
    }
    rows
}

fn admin_router(registry: Arc<BotCore>) -> axum::Router {
    let services = Services::builder()
        .registry(registry.clone())
        .bot_onboarding(Arc::new(BotOnboarding::new(
            registry.clone(),
            Arc::new(NoopRelation::default()),
            true,
            None,
        )))
        .build_for_test();
    build_router(
        HttpAppState::new(services).with_user_identity(Arc::new(ChainUserIdentityPort::new(
            static_auth_chain("11111111", "Admin"),
        ))),
    )
}

async fn admin_request(app: &axum::Router, body: Value) -> axum::response::Response {
    app.clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/admin/bots/onboard")
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap()
}

async fn response_json(response: axum::response::Response) -> Value {
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    serde_json::from_slice(&body).unwrap()
}

async fn stored_avatar(registry: &Arc<BotCore>, bot_id: &str) -> Option<Option<String>> {
    let avatars: Arc<dyn BotRegistryCoreService> = registry.clone();
    let map = avatars.avatar_urls(&[bot_id.to_string()]).await.unwrap();
    map.get(bot_id).cloned()
}

// ---------------------------------------------------------------------------
// Admin onboard route
// ---------------------------------------------------------------------------

#[tokio::test]
async fn admin_onboard_route_persists_an_explicit_avatar_without_echoing_it() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("avatar-bot".to_string(), bcs_service_api::BotCapabilities::default())
        .await
        .unwrap();
    let app = admin_router(registry.clone());

    let response = admin_request(
        &app,
        json!({
            "bot_id": "avatar-bot",
            "name": "Avatar Bot",
            "summary": "Carries an avatar",
            "avatar_url": "https://cdn.example.com/avatar.png"
        }),
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["onboarded"], true);
    assert_eq!(body["name"], "Avatar Bot");
    assert!(
        body.as_object().unwrap().get("avatar").is_none()
            && body.as_object().unwrap().get("avatar_url").is_none(),
        "the legacy response gains no avatar field: {body}"
    );

    assert_eq!(
        stored_avatar(&registry, "avatar-bot").await,
        Some(Some("https://cdn.example.com/avatar.png".to_string()))
    );
    let stored = registry.get("avatar-bot").await.unwrap();
    assert_eq!(stored.capabilities.name.as_deref(), Some("Avatar Bot"));
}

#[tokio::test]
async fn admin_onboard_route_keeps_the_stored_avatar_when_the_field_is_omitted() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("avatar-bot".to_string(), bcs_service_api::BotCapabilities::default())
        .await
        .unwrap();
    let writer: Arc<dyn BotRegistryCoreService> = registry.clone();
    // Preseed the stored avatar — the omitted field must preserve it.
    writer
        .update_avatar_existing(
            "avatar-bot",
            bcs_service_api::types::bot_profile::AvatarChange::Set(
                "https://cdn.example.com/old.png".to_string(),
            ),
        )
        .await
        .unwrap();
    let app = admin_router(registry.clone());

    let response = admin_request(
        &app,
        json!({
            "bot_id": "avatar-bot",
            "name": "Renamed Bot"
        }),
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["onboarded"], true);

    assert_eq!(
        stored_avatar(&registry, "avatar-bot").await,
        Some(Some("https://cdn.example.com/old.png".to_string())),
        "an omitted avatar_url must preserve the stored avatar"
    );
    let stored = registry.get("avatar-bot").await.unwrap();
    assert_eq!(
        stored.capabilities.name.as_deref(),
        Some("Renamed Bot"),
        "the same profile write still applies the merged capabilities"
    );
}

#[tokio::test]
async fn admin_onboard_route_clears_the_stored_avatar_with_an_empty_string() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("avatar-bot".to_string(), bcs_service_api::BotCapabilities::default())
        .await
        .unwrap();
    let writer: Arc<dyn BotRegistryCoreService> = registry.clone();
    writer
        .update_avatar_existing(
            "avatar-bot",
            bcs_service_api::types::bot_profile::AvatarChange::Set(
                "https://cdn.example.com/old.png".to_string(),
            ),
        )
        .await
        .unwrap();
    let app = admin_router(registry.clone());

    let response = admin_request(
        &app,
        json!({
            "bot_id": "avatar-bot",
            "name": "Avatar Bot",
            "avatar_url": ""
        }),
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["onboarded"], true);
    assert_eq!(
        stored_avatar(&registry, "avatar-bot").await,
        Some(None),
        "the explicit empty string clears the stored avatar"
    );
}

#[tokio::test]
async fn admin_onboard_route_rejects_null_and_non_string_avatar_values() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("avatar-bot".to_string(), bcs_service_api::BotCapabilities::default())
        .await
        .unwrap();
    let app = admin_router(registry.clone());

    for body in [
        json!({
            "bot_id": "avatar-bot",
            "name": "Avatar Bot",
            "avatar_url": null
        }),
        json!({
            "bot_id": "avatar-bot",
            "name": "Avatar Bot",
            "avatar_url": 7
        }),
    ] {
        let response = admin_request(&app, body).await;
        assert_ne!(
            response.status(),
            StatusCode::OK,
            "explicit null/number avatar values must not be accepted"
        );
        assert!(
            response.status().is_client_error(),
            "rejection is a 4xx parse error, not a panic: {}",
            response.status()
        );
    }

    assert_eq!(stored_avatar(&registry, "avatar-bot").await, Some(None));
}

#[tokio::test]
async fn admin_onboard_route_on_missing_bot_keeps_the_onboarded_false_row_free_response() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("existing-bot".to_string(), bcs_service_api::BotCapabilities::default())
        .await
        .unwrap();
    let app = admin_router(registry.clone());
    let rows_before = bot_record_files(&temp_dir.path().to_path_buf());

    let response = admin_request(
        &app,
        json!({
            "bot_id": "missing-bot",
            "name": "Ghost Bot",
            "summary": "Never registered",
            "avatar_url": "https://cdn.example.com/ghost.png"
        }),
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["onboarded"], false);
    assert_eq!(body["bot_uuid"], "missing-bot");
    assert!(
        body["message"].as_str().unwrap_or("").contains("未在协作网络注册"),
        "message keeps the legacy hint: {body}"
    );
    assert!(
        body.as_object().unwrap().get("avatar").is_none()
            && body.as_object().unwrap().get("avatar_url").is_none(),
        "missing-bot response gains no avatar field: {body}"
    );

    let rows_after = bot_record_files(&temp_dir.path().to_path_buf());
    assert_eq!(
        rows_after.len(),
        rows_before.len(),
        "admin onboarding of a missing bot must not create a durable bot row"
    );
    assert!(registry.get("missing-bot").await.is_none());
    assert_eq!(stored_avatar(&registry, "missing-bot").await, None);
}

// ---------------------------------------------------------------------------
// Provider bot registration route
// ---------------------------------------------------------------------------

struct ProviderApp {
    app: axum::Router,
    registry: Arc<BotCore>,
    _temp_dir: TempDir,
}

fn provider_app() -> ProviderApp {
    let temp_dir = TempDir::new().unwrap();
    let provider_store = Arc::new(MemoryProviderStore::new());
    let provider_repo: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let provider_credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
    let provider_bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    let bot_providers = Arc::new(MemoryBotProviderStore::new(
        bot_repo.clone(),
        provider_store.clone(),
    ));
    let registry = Arc::new(BotCore::with_provider_repos(
        bot_repo,
        provider_repo.clone(),
        provider_credentials.clone(),
        provider_bindings.clone(),
    ));
    let registry_service: Arc<dyn BotRegistryCoreService> = registry.clone();
    let provider_core_impl = Arc::new(
        ProviderCore::new(
            provider_repo,
            provider_credentials,
            provider_bindings,
            registry_service.clone(),
        )
        .with_bot_provider_repo(bot_providers),
    );
    let provider_core: Arc<dyn ProviderCoreService> = provider_core_impl.clone();
    let provider_bot_core: Arc<dyn bcs_service_api::ProviderBotCoreService> =
        provider_core_impl.clone();
    let provider_management = Arc::new(ProviderManagement::new(
        provider_core.clone(),
        provider_bot_core.clone(),
        registry_service,
        Arc::new(NoopRelation::default()),
    ));

    let services = Services::builder()
        .registry(registry.clone())
        .provider_core(provider_core)
        .provider_bot_core(provider_bot_core)
        .provider_management(provider_management)
        .build_for_test();

    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(static_auth_chain("11111111", "Admin")),
    )));
    ProviderApp {
        app,
        registry,
        _temp_dir: temp_dir,
    }
}

async fn register_provider(app: &axum::Router) -> (String, String) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "name": "Provider",
                        "webhook_url": "https://provider.example.com/bcs/webhook",
                        "auth": { "mode": "static_bearer" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    (
        body["provider_id"].as_str().unwrap().to_string(),
        body["provider_admin_token"].as_str().unwrap().to_string(),
    )
}

async fn register_provider_bot_request(
    app: &axum::Router,
    provider_id: &str,
    admin_token: &str,
    provider_bot_ref: &str,
) -> axum::response::Response {
    app.clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Code Reviewer",
                        "owners": ["11111111"],
                        "provider_bot_ref": provider_bot_ref
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap()
}

#[tokio::test]
async fn provider_register_route_persists_an_avatar_and_keeps_the_response_shape() {
    let app = provider_app();
    let (provider_id, admin_token) = register_provider(&app.app).await;

    let response = app
        .app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Code Reviewer",
                        "owners": ["11111111"],
                        "provider_bot_ref": "avatar-ref",
                        "avatar_url": "https://cdn.example.com/reviewer.png"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["provider_bot_ref"], "avatar-ref");
    assert!(
        body.as_object().unwrap().get("avatar").is_none()
            && body.as_object().unwrap().get("avatar_url").is_none(),
        "the registration response gains no avatar field: {body}"
    );

    let bot_uuid = body["bot_uuid"].as_str().unwrap();
    assert_eq!(
        stored_avatar(&app.registry, bot_uuid).await,
        Some(Some("https://cdn.example.com/reviewer.png".to_string()))
    );
}

#[tokio::test]
async fn provider_register_route_replay_applies_the_avatar_without_new_credentials() {
    let app = provider_app();
    let (provider_id, admin_token) = register_provider(&app.app).await;

    let first = register_provider_bot_request(&app.app, &provider_id, &admin_token, "replay-ref")
        .await;
    assert_eq!(first.status(), StatusCode::OK);
    let first_body = response_json(first).await;
    let bot_uuid = first_body["bot_uuid"].as_str().unwrap().to_string();
    let first_token = first_body["bot_runtime_token"]
        .as_str()
        .expect("the first registration issues a runtime token")
        .to_string();

    let replay = app
        .app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Code Reviewer",
                        "owners": ["11111111"],
                        "provider_bot_ref": "replay-ref",
                        "avatar_url": "https://cdn.example.com/updated.png"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(replay.status(), StatusCode::OK);
    let replay_body = response_json(replay).await;
    assert_eq!(replay_body["bot_uuid"], bot_uuid);
    assert!(
        replay_body.get("bot_runtime_token").is_none(),
        "a replay must omit the runtime token field: {replay_body}"
    );

    assert_eq!(
        stored_avatar(&app.registry, &bot_uuid).await,
        Some(Some("https://cdn.example.com/updated.png".to_string())),
        "an explicit avatar on a duplicate registration still applies"
    );

    // The ORIGINAL token survives the replay: no rotation, no reissuance.
    let reader: Arc<dyn BotRegistryCoreService> = app.registry.clone();
    let resolved = reader
        .resolve_bot_token(&first_token, None)
        .await
        .unwrap()
        .expect("the original runtime token still resolves");
    assert_eq!(resolved.bot.bot_id, bot_uuid);
}

#[tokio::test]
async fn provider_register_route_rejects_null_and_non_string_avatar_values() {
    let app = provider_app();
    let (provider_id, admin_token) = register_provider(&app.app).await;

    for avatar in [Value::Null, json!(7)] {
        let mut body = json!({
            "name": "Code Reviewer",
            "owners": ["11111111"],
            "provider_bot_ref": "reject-ref"
        });
        body["avatar_url"] = avatar;
        let response = app
            .app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri(format!("/providers/{provider_id}/bots"))
                    .header("content-type", "application/json")
                    .header("authorization", format!("Bearer {admin_token}"))
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_ne!(response.status(), StatusCode::OK);
        assert!(
            response.status().is_client_error(),
            "rejection is a 4xx parse error, not a panic: {}",
            response.status()
        );
    }
}
