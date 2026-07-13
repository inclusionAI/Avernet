use std::sync::Arc;

use axum::{
    Router,
    body::{Body, to_bytes},
    http::{HeaderMap, Request, StatusCode, Uri},
};
use bcs_auth_api::{AuthError, AuthPluginChain, AuthPrincipal, UserIdentityInfo};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::{BotCore, ProviderCore, ProviderManagement};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_http::{
    router::build_router,
    service_key::{ApiKeyEntry, ApiKeyRegistry, sha256_hex},
    state::{ChainUserIdentityPort, HttpAppState, HttpUserIdentity, UserIdentityPort},
};
use bcs_user_directory_api::{UserDirectoryPlugin, UserDirectoryProfile};
use bcs_service_api::{
    ActorKind, EnsureOwnerEdgesResult, BotRegistryCoreService, ProviderBotBindingRepoPort,
    ProviderBotCoreService, ProviderCoreService, ProviderCredential, ProviderCredentialRepoPort,
    ProviderRecord, ProviderRepoPort, ProviderStreamGrayList, RelationCoreService, RelationEdge,
    ServiceResult,
};
use bcs_services_container::Services;
use serde_json::{Value, json};
use tempfile::TempDir;
use tower::ServiceExt;

struct TestApp {
    app: Router,
    provider_repo: Arc<dyn ProviderRepoPort>,
    provider_credentials: Arc<dyn ProviderCredentialRepoPort>,
    provider_stream_gray_list: Arc<ProviderStreamGrayList>,
    registry: Arc<BotCore>,
    relation: Arc<RecordingRelationCoreService>,
    _temp_dir: TempDir,
}

fn test_app() -> TestApp {
    let chain = static_auth_chain("11111111", "Admin");
    test_app_with_user_identity(Arc::new(ChainUserIdentityPort::new(chain)))
}

fn test_app_with_user_identity(user_identity: Arc<dyn UserIdentityPort>) -> TestApp {
    test_app_with_user_identity_and_user_directory(user_identity, None)
}

fn test_app_with_user_identity_and_user_directory(
    user_identity: Arc<dyn UserIdentityPort>,
    user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
) -> TestApp {
    test_app_with_options(user_identity, user_directory, Vec::new(), Vec::new())
}

fn test_app_with_allowed_switch_provider_ids(allowed_provider_ids: Vec<String>) -> TestApp {
    let chain = static_auth_chain("11111111", "Admin");
    test_app_with_options(
        Arc::new(ChainUserIdentityPort::new(chain)),
        None,
        allowed_provider_ids,
        Vec::new(),
    )
}

fn test_app_with_service_keys(service_keys: Vec<ApiKeyEntry>) -> TestApp {
    let chain = static_auth_chain("197262", "Admin");
    test_app_with_options(
        Arc::new(ChainUserIdentityPort::new(chain)),
        None,
        Vec::new(),
        service_keys,
    )
}

fn test_app_with_options(
    user_identity: Arc<dyn UserIdentityPort>,
    user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
    allowed_provider_ids: Vec<String>,
    service_keys: Vec<ApiKeyEntry>,
) -> TestApp {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let provider_store = Arc::new(MemoryProviderStore::new());
    let provider_repo: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let provider_credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
    let provider_bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
    let provider_stream_gray_list = Arc::new(ProviderStreamGrayList::default());
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    let registry = Arc::new(BotCore::with_provider_repos(
        bot_repo,
        provider_repo.clone(),
        provider_credentials.clone(),
        provider_bindings.clone(),
    ));
    let registry_service: Arc<dyn BotRegistryCoreService> = registry.clone();
    let provider_core_impl = Arc::new(ProviderCore::new(
        provider_repo.clone(),
        provider_credentials.clone(),
        provider_bindings,
        registry_service.clone(),
    ));
    let provider_core: Arc<dyn ProviderCoreService> = provider_core_impl.clone();
    let provider_bot_core: Arc<dyn ProviderBotCoreService> = provider_core_impl.clone();
    let relation = Arc::new(RecordingRelationCoreService::default());
    let mut provider_management = ProviderManagement::new(
        provider_core.clone(),
        provider_bot_core.clone(),
        registry_service.clone(),
        relation.clone(),
    );
    if let Some(user_directory) = user_directory {
        provider_management = provider_management.with_user_directory(user_directory);
    }
    let provider_management = Arc::new(provider_management);

    let services = Services::builder()
        .registry(registry_service)
        .provider_core(provider_core)
        .provider_bot_core(provider_bot_core)
        .provider_management(provider_management)
        .build_for_test();

    TestApp {
        app: build_router(
            HttpAppState::new(services)
                .with_user_identity(user_identity)
                .with_allowed_switch_provider_ids(allowed_provider_ids)
                .with_service_api_keys(Arc::new(ApiKeyRegistry::new(service_keys)))
                .with_provider_stream_gray_list(provider_stream_gray_list.clone()),
        ),
        provider_repo,
        provider_credentials,
        provider_stream_gray_list,
        registry,
        relation,
        _temp_dir: temp_dir,
    }
}

fn admin_service_key() -> (&'static str, ApiKeyEntry) {
    let raw_key = "stream-gray-admin-key";
    (
        raw_key,
        ApiKeyEntry {
            name: "stream-gray-admin".to_string(),
            sha256: sha256_hex(raw_key),
            bound_groups: Vec::new(),
        },
    )
}

fn bound_service_key() -> (&'static str, ApiKeyEntry) {
    let raw_key = "stream-gray-bound-key";
    (
        raw_key,
        ApiKeyEntry {
            name: "stream-gray-bound".to_string(),
            sha256: sha256_hex(raw_key),
            bound_groups: vec!["group-1".to_string()],
        },
    )
}

#[derive(Default)]
struct RecordingRelationCoreService {
    owner_edges: tokio::sync::Mutex<Vec<(String, String, String)>>,
}

#[tokio::test]
async fn stream_gray_get_returns_current_created_by_list() {
    let (raw_key, entry) = admin_service_key();
    let app = test_app_with_service_keys(vec![entry]);
    app.provider_stream_gray_list.replace(vec![
        " 197262 ".to_string(),
        "alice".to_string(),
        "197262".to_string(),
    ]);

    let response = app
        .app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/providers/stream-gray")
                .header("X-BCS-Service-Key", raw_key)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["enabled"], json!(false));
    assert_eq!(body["created_by"], json!(["197262", "alice"]));
}

#[tokio::test]
async fn stream_gray_put_replaces_and_normalizes_created_by_list() {
    let (raw_key, entry) = admin_service_key();
    let app = test_app_with_service_keys(vec![entry]);
    app.provider_stream_gray_list
        .replace(vec!["old".to_string()]);

    let response = app
        .app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/providers/stream-gray")
                .header("content-type", "application/json")
                .header("X-BCS-Service-Key", raw_key)
                .body(Body::from(
                    json!({
                        "enabled": true,
                        "created_by": [" bob ", "", "alice", "bob"]
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["enabled"], json!(true));
    assert_eq!(body["created_by"], json!(["alice", "bob"]));
    assert!(app.provider_stream_gray_list.is_enabled());
    assert_eq!(
        app.provider_stream_gray_list.list(),
        vec!["alice".to_string(), "bob".to_string()]
    );
    assert!(!app.provider_stream_gray_list.contains(Some("missing")));
}

#[tokio::test]
async fn stream_gray_put_can_disable_gray_mode_without_replacing_created_by_list() {
    let app = test_app_with_service_keys(vec![]);
    app.provider_stream_gray_list.update(
        Some(true),
        Some(vec!["alice".to_string()]),
    );

    let response = app
        .app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/providers/stream-gray")
                .header("content-type", "application/json")
                .body(Body::from(json!({ "enabled": false }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["enabled"], json!(false));
    assert_eq!(body["created_by"], json!(["alice"]));
    assert!(!app.provider_stream_gray_list.is_enabled());
    assert_eq!(
        app.provider_stream_gray_list.list(),
        vec!["alice".to_string()]
    );
    assert!(app.provider_stream_gray_list.contains(Some("missing")));
}

#[tokio::test]
async fn stream_gray_put_works_without_admin_service_key() {
    let app = test_app_with_service_keys(vec![]);

    let response = app
        .app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/providers/stream-gray")
                .header("content-type", "application/json")
                .body(Body::from(json!({ "created_by": ["alice"] }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["created_by"], json!(["alice"]));
    assert_eq!(
        app.provider_stream_gray_list.list(),
        vec!["alice".to_string()]
    );
}

#[tokio::test]
async fn stream_gray_put_ignores_service_key_header() {
    let (raw_key, _entry) = bound_service_key();
    let app = test_app_with_service_keys(vec![]);

    let response = app
        .app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/providers/stream-gray")
                .header("content-type", "application/json")
                .header("X-BCS-Service-Key", raw_key)
                .body(Body::from(json!({ "created_by": ["alice"] }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        app.provider_stream_gray_list.list(),
        vec!["alice".to_string()]
    );
}

#[async_trait::async_trait]
impl RelationCoreService for RecordingRelationCoreService {
    async fn upsert_edge(&self, _edge: RelationEdge) -> ServiceResult<()> {
        Ok(())
    }

    async fn delete_edge(&self, _from_id: &str, _to_id: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn get_edge(
        &self,
        _from_id: &str,
        _to_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<RelationEdge>> {
        Ok(None)
    }

    async fn ensure_owner_edges(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<()> {
        self.owner_edges.lock().await.push((
            human_id.to_string(),
            bot_id.to_string(),
            env.to_string(),
        ));
        Ok(())
    }

    async fn ensure_owner_edges_counted(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<EnsureOwnerEdgesResult> {
        self.ensure_owner_edges(human_id, bot_id, env).await?;
        Ok(EnsureOwnerEdgesResult {
            created: 2,
            upgraded: 0,
        })
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

    async fn list_friends_via_relation(
        &self,
        _actor_id: &str,
        _env: &str,
    ) -> ServiceResult<Vec<String>> {
        Ok(Vec::new())
    }
}

fn static_auth_chain(staff_no: &str, nick_name: &str) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        user_id: Some(staff_no.to_string()),
        user_name: Some(nick_name.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(StaticAuthPlugin::with_principal(principal))]))
}

#[derive(Default)]
struct RecordingUserDirectoryPlugin {
    nick_name: String,
    lookups: tokio::sync::Mutex<Vec<String>>,
}

#[async_trait::async_trait]
impl UserDirectoryPlugin for RecordingUserDirectoryPlugin {
    async fn lookup_by_staff_no(
        &self,
        staff_no: &str,
    ) -> Result<Option<UserDirectoryProfile>, bcs_user_directory_api::UserDirectoryError> {
        self.lookups.lock().await.push(staff_no.to_string());
        Ok(Some(UserDirectoryProfile {
            staff_no: staff_no.to_string(),
            nick_name: Some(self.nick_name.clone()),
        }))
    }
}

struct NoUserIdentity;

#[async_trait::async_trait]
impl UserIdentityPort for NoUserIdentity {
    async fn extract(&self, _headers: &HeaderMap, _uri: &Uri) -> Option<HttpUserIdentity> {
        None
    }

    async fn ensure_identity(
        &self,
        _auth_source: &str,
        _external_user_id: &str,
        _external_user_name: Option<&str>,
        _avatar: Option<&str>,
        _env: &str,
    ) -> Result<String, AuthError> {
        Ok("noop-identity".to_string())
    }

    async fn get_identity_by_token(
        &self,
        _token: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn get_identity_by_user_id(
        &self,
        _user_id: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn update_token(
        &self,
        _user_id: &str,
        _token: &str,
        _expire_at: u64,
    ) -> Result<(), AuthError> {
        Ok(())
    }
}

struct HeaderUserIdentity;

#[async_trait::async_trait]
impl UserIdentityPort for HeaderUserIdentity {
    async fn extract(&self, headers: &HeaderMap, _uri: &Uri) -> Option<HttpUserIdentity> {
        headers
            .get("x-test-staff-no")
            .and_then(|value| value.to_str().ok())
            .map(|staff_no| HttpUserIdentity {
                staff_no: Some(staff_no.to_string()),
                nick_name: None,
            })
    }

    async fn ensure_identity(
        &self,
        _auth_source: &str,
        _external_user_id: &str,
        _external_user_name: Option<&str>,
        _avatar: Option<&str>,
        _env: &str,
    ) -> Result<String, AuthError> {
        Ok("noop-identity".to_string())
    }

    async fn get_identity_by_token(
        &self,
        _token: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn get_identity_by_user_id(
        &self,
        _user_id: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn update_token(
        &self,
        _user_id: &str,
        _token: &str,
        _expire_at: u64,
    ) -> Result<(), AuthError> {
        Ok(())
    }
}

#[tokio::test]
async fn register_provider_requires_human_identity() {
    let TestApp {
        app, _temp_dir, ..
    } = test_app_with_user_identity(Arc::new(NoUserIdentity));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "name": "Provider",
                        "webhook_url": "https://provider.example.com/bcs/webhook",
                        "auth": {
                            "mode": "static_bearer"
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body = response_json(response).await;
    assert_eq!(body["error"], "valid human identity is required");
}

#[tokio::test]
async fn register_provider_sets_created_by_and_owners_from_human_identity() {
    let TestApp {
        app,
        provider_repo,
        _temp_dir,
        ..
    } = test_app_with_user_identity(Arc::new(ChainUserIdentityPort::new(
        static_auth_chain("11111111", "Admin"),
    )));

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
                        "auth": {
                            "mode": "static_bearer"
                        },
                        "owners": ["mallory"]
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let provider = response_json(response).await;
    let provider_id = provider["provider_id"].as_str().unwrap();

    let stored = provider_repo
        .get_provider(provider_id)
        .await
        .expect("get provider")
        .expect("provider exists");

    assert_eq!(stored.created_by, "11111111");
    assert_eq!(stored.owners, r#"["11111111"]"#);
}

#[tokio::test]
async fn register_provider_ignores_client_supplied_provider_id() {
    let TestApp { app, _temp_dir, .. } = test_app();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "provider_id": "client-supplied-provider",
                        "name": "Provider",
                        "webhook_url": "https://provider.example.com/bcs/webhook",
                        "auth": {
                            "mode": "static_bearer"
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    let provider_id = body["provider_id"].as_str().unwrap();
    assert_ne!(provider_id, "client-supplied-provider");
    assert!(provider_id.starts_with("prv_"));
}

#[tokio::test]
async fn register_provider_rejects_private_webhook_url() {
    let TestApp { app, _temp_dir, .. } = test_app();

    for webhook_url in [
        "http://127.0.0.1:8080/webhook",
        "http://169.254.169.254/latest/meta-data/",
    ] {
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
                            "webhook_url": webhook_url,
                            "auth": {
                                "mode": "static_bearer"
                            }
                        })
                        .to_string(),
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "{webhook_url}");
        let body = response_json(response).await;
        assert!(
            body["error"].as_str().unwrap_or_default().contains("webhook_url is not allowed"),
            "{body}"
        );
    }
}

#[tokio::test]
async fn register_provider_persists_protocol_version() {
    let TestApp {
        app,
        provider_repo,
        _temp_dir,
        ..
    } = test_app();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "name": "Provider",
                        "webhook_url": "https://provider.example.com/bcs/webhook",
                        "protocol_version": "2.0",
                        "auth": {
                            "mode": "provider_admin"
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    let provider_id = body["provider_id"].as_str().unwrap();
    let stored = provider_repo
        .get_provider(provider_id)
        .await
        .expect("get provider")
        .expect("provider exists");
    let config: Value = serde_json::from_str(&stored.config).unwrap();
    assert_eq!(config["downlink"]["protocol_version"], "2.0");
}

#[tokio::test]
async fn register_static_bearer_provider_bot_returns_runtime_token() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(
        &app,
        json!({
            "mode": "static_bearer"
        }),
    )
    .await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let bot = register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;

    assert_eq!(bot["provider_id"], provider_id);
    assert_eq!(bot["provider_bot_ref"], "reviewer-v2");
    assert!(bot["bot_runtime_token"]
        .as_str()
        .is_some_and(|token| uuid::Uuid::parse_str(token).is_ok()));
}

#[tokio::test]
async fn register_provider_bot_is_idempotent_for_existing_provider_ref() {
    let TestApp {
        app,
        registry,
        _temp_dir,
        ..
    } = test_app();
    let provider = register_provider(
        &app,
        json!({
            "mode": "static_bearer"
        }),
    )
    .await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let first = register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;
    let first_bot_uuid = first["bot_uuid"].as_str().unwrap().to_string();
    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Updated Reviewer",
                        "summary": "Should not update existing bot",
                        "owners": ["197262"],
                        "provider_bot_ref": "reviewer-v2"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["bot_uuid"], first_bot_uuid);
    assert_eq!(body["provider_id"], provider_id);
    assert_eq!(body["provider_bot_ref"], "reviewer-v2");
    assert_eq!(body["message"], "provider bot ref already registered; returning existing bot");
    assert!(body["bot_runtime_token"].is_null());
    let bot = registry
        .get(&first_bot_uuid)
        .await
        .expect("existing bot should still be registered");
    assert_eq!(bot.capabilities.name.as_deref(), Some("Code Reviewer"));
}

#[tokio::test]
async fn register_provider_bot_ensures_human_actor_and_owner_edges() {
    let user_directory = Arc::new(RecordingUserDirectoryPlugin {
        nick_name: "Alice Hua".to_string(),
        ..Default::default()
    });
    let TestApp {
        app,
        registry,
        relation,
        _temp_dir,
        ..
    } = test_app_with_user_identity_and_user_directory(
        Arc::new(HeaderUserIdentity),
        Some(user_directory.clone()),
    );
    let provider = register_provider_as(&app, "11111111").await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Code Reviewer",
                        "summary": "Reviews code",
                        "owners": ["11111111"],
                        "provider_bot_ref": "reviewer-v2"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    let bot_uuid = body["bot_uuid"].as_str().unwrap();
    let bot = registry.get(bot_uuid).await.expect("bot should be registered");
    assert_eq!(bot.created_by.as_deref(), Some("11111111"));
    let human = registry
        .get("human_11111111")
        .await
        .expect("human actor should be ensured");
    assert_eq!(human.actor_kind, ActorKind::Human);
    assert_eq!(human.capabilities.name.as_deref(), Some("Alice Hua"));
    assert_eq!(user_directory.lookups.lock().await.as_slice(), ["11111111"]);
    let owner_edges = relation.owner_edges.lock().await;
    assert!(
        owner_edges.iter().any(|(human_id, edge_bot_id, _)| {
            human_id == "human_11111111" && edge_bot_id == bot_uuid
        }),
        "expected owner edge for human_11111111 -> {bot_uuid}, got {owner_edges:?}",
    );
}

#[tokio::test]
async fn register_provider_bot_persists_skills_domains_scopes() {
    let TestApp {
        app,
        registry,
        _temp_dir,
        ..
    } = test_app();
    let provider = register_provider(
        &app,
        json!({
            "mode": "static_bearer"
        }),
    )
    .await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Code Reviewer",
                        "summary": "Reviews code",
                        "owners": ["12345678"],
                        "provider_bot_ref": "reviewer-v2",
                        "domains": ["development", "security"],
                        "skills": [
                            "code_review",
                            {"name": "sql_analysis", "description": "Analyzes SQL"}
                        ],
                        "scopes": ["production"]
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    let bot_uuid = body["bot_uuid"].as_str().unwrap();
    let bot = registry.get(bot_uuid).await.expect("bot should be registered");
    assert_eq!(bot.capabilities.domains, vec!["development", "security"]);
    assert_eq!(bot.capabilities.scopes, vec!["production"]);
    let skills: Vec<(&str, Option<&str>)> = bot
        .capabilities
        .skills
        .iter()
        .map(|skill| (skill.name.as_str(), skill.description.as_deref()))
        .collect();
    assert_eq!(
        skills,
        vec![
            ("code_review", None),
            ("sql_analysis", Some("Analyzes SQL")),
        ]
    );
}

#[tokio::test]
async fn register_agentpass_provider_bot_omits_runtime_token() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(
        &app,
        json!({
            "mode": "agentpass"
        }),
    )
    .await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let bot = register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;

    assert_eq!(bot["provider_id"], provider_id);
    assert!(bot.get("bot_runtime_token").is_none());
}

#[tokio::test]
async fn delete_provider_bot_soft_deletes_bound_bot_and_runtime_token() {
    let TestApp {
        app,
        registry,
        _temp_dir,
        ..
    } = test_app();
    let provider = register_provider(
        &app,
        json!({
            "mode": "static_bearer"
        }),
    )
    .await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    let bot = register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;
    let bot_uuid = bot["bot_uuid"].as_str().unwrap();
    let runtime_token = bot["bot_runtime_token"].as_str().unwrap();

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri(format!("/providers/{provider_id}/bots/reviewer-v2"))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["deleted"], true);
    assert_eq!(body["provider_id"], provider_id);
    assert_eq!(body["provider_bot_ref"], "reviewer-v2");
    assert_eq!(body["bot_uuid"], bot_uuid);
    assert!(registry.get(bot_uuid).await.is_none());
    assert_eq!(registry.find_bot_by_token(runtime_token).await, None);

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["items"].as_array().unwrap().len(), 0);
}

#[tokio::test]
async fn delete_provider_bot_returns_ok_when_bot_is_not_registered_in_bcs() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(
        &app,
        json!({
            "mode": "static_bearer"
        }),
    )
    .await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    let missing_provider_bot_ref = "missing-bot";

    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri(format!("/providers/{provider_id}/bots/{missing_provider_bot_ref}"))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["deleted"], false);
    assert_eq!(body["provider_id"], provider_id);
    assert_eq!(body["provider_bot_ref"], missing_provider_bot_ref);
    assert_eq!(body["message"], "bot is not registered in BCS");
}

#[tokio::test]
async fn delete_allowed_switch_provider_legacy_bot_without_binding() {
    let provider_id = "prv_allowed_switch".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        registry,
        _temp_dir,
        ..
    } = test_app_with_allowed_switch_provider_ids(vec![provider_id.clone()]);
    provider_repo
        .insert_provider(ProviderRecord {
            provider_id: provider_id.clone(),
            name: "Provider".to_string(),
            config: json!({
                "downlink": {
                    "enabled": true,
                    "webhook_url": "https://provider.example.com/bcs/webhook",
                    "auth_mode": "static_bearer",
                    "protocol_version": "1.0"
                }
            })
            .to_string(),
            created_by: "11111111".to_string(),
            owners: r#"["11111111"]"#.to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("seed provider");
    provider_credentials
        .insert_credential(ProviderCredential {
            provider_id: provider_id.clone(),
            credential_kind: "provider_admin".to_string(),
            secret_value: admin_token.to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("seed provider admin credential");
    registry
        .register_with_owner_and_token(
            "teamclaw-bot:alice".to_string(),
            bcs_service_api::BotCapabilities {
                name: Some("Teamclaw Bot".to_string()),
                summary: Some("Existing TC-style bot".to_string()),
                ..Default::default()
            },
            "alice",
            "legacy-token",
        )
        .await
        .expect("seed existing bot row");

    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri(format!("/providers/{provider_id}/bots/teamclaw-bot:alice"))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["deleted"], true);
    assert_eq!(body["bot_uuid"], "teamclaw-bot:alice");
    assert!(registry.get("teamclaw-bot:alice").await.is_none());
    assert_eq!(registry.find_bot_by_token("legacy-token").await, None);
}

#[tokio::test]
async fn get_provider_returns_metadata_without_tokens() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(
        &app,
        json!({
            "mode": "static_bearer"
        }),
    )
    .await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    let bcs_to_provider_token = provider["bcs_to_provider_token"].as_str().unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!("/providers/{provider_id}"))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["provider_id"], provider_id);
    assert_eq!(body["name"], "Provider");
    assert_eq!(body["webhook_url"], "https://provider.example.com/bcs/webhook");
    assert_eq!(body["auth_mode"], "static_bearer");
    assert!(body.get("provider_admin_token").is_none());
    assert!(body.get("bcs_to_provider_token").is_none());
    let body_text = body.to_string();
    assert!(!body_text.contains(admin_token));
    assert!(!body_text.contains(bcs_to_provider_token));
}

#[tokio::test]
async fn register_provider_returns_coordination_metadata() {
    let TestApp { app, _temp_dir, .. } = test_app();

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
                        "auth": {
                            "mode": "static_bearer"
                        },
                        "coordination": {
                            "mode": "mcporter_mcp",
                            "mcp_server": "bcs",
                            "mcporter_command": "mcporter"
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let registered = response_json(response).await;
    let provider_id = registered["provider_id"].as_str().unwrap();
    let admin_token = registered["provider_admin_token"].as_str().unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!("/providers/{provider_id}"))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["coordination"]["mode"], "mcporter_mcp");
    assert_eq!(body["coordination"]["mcp_server"], "bcs");
    assert_eq!(body["coordination"]["mcporter_command"], "mcporter");
}

#[tokio::test]
async fn patch_provider_updates_name_and_webhook_url() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(
        &app,
        json!({
            "mode": "static_bearer"
        }),
    )
    .await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Updated Provider",
                        "webhook_url": "https://provider.example.com/updated/webhook"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["provider_id"], provider_id);
    assert_eq!(body["name"], "Updated Provider");
    assert_eq!(
        body["webhook_url"],
        "https://provider.example.com/updated/webhook"
    );
    assert_eq!(body["auth_mode"], "static_bearer");

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!("/providers/{provider_id}"))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["name"], "Updated Provider");
    assert_eq!(
        body["webhook_url"],
        "https://provider.example.com/updated/webhook"
    );
}

#[tokio::test]
async fn patch_and_get_provider_use_typed_organization_management_config() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider_a = register_provider_as(&app, "11111111").await;
    let provider_b = register_provider_as(&app, "11111111").await;
    let provider_a_id = provider_a["provider_id"].as_str().unwrap();
    let provider_b_id = provider_b["provider_id"].as_str().unwrap();
    let admin_token_b = provider_b["provider_admin_token"].as_str().unwrap();

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_b_id}"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token_b}"))
                .header("x-test-staff-no", "11111111")
                .body(Body::from(
                    json!({
                        "organization_management": {
                            "authorized_manager_provider_ids": [provider_a_id, provider_a_id]
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(
        body["organization_management"]["authorized_manager_provider_ids"],
        json!([provider_a_id])
    );
    assert!(body.get("config").is_none());

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!("/providers/{provider_b_id}"))
                .header("authorization", format!("Bearer {admin_token_b}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(
        body["organization_management"]["authorized_manager_provider_ids"],
        json!([provider_a_id])
    );
    assert!(body.get("config").is_none());
}

#[tokio::test]
async fn patch_provider_preserves_absent_and_clears_empty_organization_management_config() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider_a = register_provider_as(&app, "11111111").await;
    let provider_b = register_provider_as(&app, "11111111").await;
    let provider_a_id = provider_a["provider_id"].as_str().unwrap();
    let provider_b_id = provider_b["provider_id"].as_str().unwrap();
    let admin_token_b = provider_b["provider_admin_token"].as_str().unwrap();

    for body in [
        json!({
            "organization_management": {
                "authorized_manager_provider_ids": [provider_a_id]
            }
        }),
        json!({"name": "Renamed Provider"}),
    ] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("PATCH")
                    .uri(format!("/providers/{provider_b_id}"))
                    .header("content-type", "application/json")
                    .header("authorization", format!("Bearer {admin_token_b}"))
                    .header("x-test-staff-no", "11111111")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = response_json(response).await;
        assert_eq!(
            body["organization_management"]["authorized_manager_provider_ids"],
            json!([provider_a_id])
        );
    }

    let response = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_b_id}"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token_b}"))
                .header("x-test-staff-no", "11111111")
                .body(Body::from(
                    json!({
                        "organization_management": {
                            "authorized_manager_provider_ids": []
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(
        body["organization_management"]["authorized_manager_provider_ids"],
        json!([])
    );
}

#[tokio::test]
async fn patch_provider_requires_owner_identity() {
    let TestApp { app, _temp_dir, .. } =
        test_app_with_user_identity(Arc::new(HeaderUserIdentity));
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers")
                .header("content-type", "application/json")
                .header("x-test-staff-no", "11111111")
                .body(Body::from(
                    json!({
                        "name": "Provider",
                        "webhook_url": "https://provider.example.com/bcs/webhook",
                        "auth": {
                            "mode": "static_bearer"
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let provider = response_json(response).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .header("x-test-staff-no", "12345678")
                .body(Body::from(
                    json!({
                        "name": "Updated Provider"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["error"], "provider_owner_required");
}

#[tokio::test]
async fn patch_provider_requires_human_identity() {
    let TestApp { app, _temp_dir, .. } =
        test_app_with_user_identity(Arc::new(HeaderUserIdentity));
    let provider = register_provider_as(&app, "11111111").await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Updated Provider"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body = response_json(response).await;
    assert_eq!(body["error"], "valid human identity is required");
}

#[tokio::test]
async fn disable_provider_requires_owner_identity() {
    let TestApp { app, _temp_dir, .. } =
        test_app_with_user_identity(Arc::new(HeaderUserIdentity));
    let provider = register_provider_as(&app, "11111111").await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/disable"))
                .header("authorization", format!("Bearer {admin_token}"))
                .header("x-test-staff-no", "12345678")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["error"], "provider_owner_required");
}

#[tokio::test]
async fn enable_provider_requires_owner_identity() {
    let TestApp { app, _temp_dir, .. } =
        test_app_with_user_identity(Arc::new(HeaderUserIdentity));
    let provider = register_provider_as(&app, "11111111").await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/disable"))
                .header("authorization", format!("Bearer {admin_token}"))
                .header("x-test-staff-no", "11111111")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/enable"))
                .header("authorization", format!("Bearer {admin_token}"))
                .header("x-test-staff-no", "12345678")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["error"], "provider_owner_required");
}

#[tokio::test]
async fn register_provider_bot_accepts_admin_token_without_human_identity() {
    let TestApp { app, _temp_dir, .. } =
        test_app_with_user_identity(Arc::new(HeaderUserIdentity));
    let provider = register_provider_as(&app, "11111111").await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Code Reviewer",
                        "summary": "Reviews code",
                        "owners": ["11111111"],
                        "provider_bot_ref": "reviewer-v2"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["provider_id"], provider_id);
    assert_eq!(body["provider_bot_ref"], "reviewer-v2");
}

#[tokio::test]
async fn register_provider_bot_reuses_provider_ref_as_bot_uuid_for_allowed_switch_provider() {
    let provider_id = "prv_allowed_switch".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        registry,
        _temp_dir,
        ..
    } = test_app_with_allowed_switch_provider_ids(vec![provider_id.clone()]);
    provider_repo
        .insert_provider(ProviderRecord {
            provider_id: provider_id.clone(),
            name: "Provider".to_string(),
            config: json!({
                "downlink": {
                    "enabled": true,
                    "webhook_url": "https://provider.example.com/bcs/webhook",
                    "auth_mode": "static_bearer",
                    "protocol_version": "1.0"
                }
            })
            .to_string(),
            created_by: "11111111".to_string(),
            owners: r#"["11111111"]"#.to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("seed provider");
    provider_credentials
        .insert_credential(ProviderCredential {
            provider_id: provider_id.clone(),
            credential_kind: "provider_admin".to_string(),
            secret_value: admin_token.to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("seed provider admin credential");

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {}", admin_token))
                .body(Body::from(
                    json!({
                        "name": "Teamclaw Bot",
                        "summary": "Handles Teamclaw tasks",
                        "owners": ["alice"],
                        "provider_bot_ref": "teamclaw-bot:alice"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["bot_uuid"], "teamclaw-bot:alice");
    assert_eq!(body["provider_bot_ref"], "teamclaw-bot:alice");
    let bot = registry
        .get("teamclaw-bot:alice")
        .await
        .expect("bot should use provider ref as uuid");
    assert_eq!(bot.created_by.as_deref(), Some("alice"));
}

#[tokio::test]
async fn register_provider_bot_rejects_allowed_switch_provider_ref_that_is_existing_bot_uuid() {
    let provider_id = "prv_allowed_switch".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        registry,
        _temp_dir,
        ..
    } = test_app_with_allowed_switch_provider_ids(vec![provider_id.clone()]);
    provider_repo
        .insert_provider(ProviderRecord {
            provider_id: provider_id.clone(),
            name: "Provider".to_string(),
            config: json!({
                "downlink": {
                    "enabled": true,
                    "webhook_url": "https://provider.example.com/bcs/webhook",
                    "auth_mode": "static_bearer",
                    "protocol_version": "1.0"
                }
            })
            .to_string(),
            created_by: "11111111".to_string(),
            owners: r#"["11111111"]"#.to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("seed provider");
    provider_credentials
        .insert_credential(ProviderCredential {
            provider_id: provider_id.clone(),
            credential_kind: "provider_admin".to_string(),
            secret_value: admin_token.to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("seed provider admin credential");
    registry
        .register_with_owner_and_token(
            "teamclaw-bot:alice".to_string(),
            bcs_service_api::BotCapabilities {
                name: Some("Teamclaw Bot".to_string()),
                summary: Some("Already registered".to_string()),
                ..Default::default()
            },
            "alice",
            "existing-token",
        )
        .await
        .expect("seed existing bot row");

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {}", admin_token))
                .body(Body::from(
                    json!({
                        "name": "Teamclaw Bot",
                        "summary": "Handles Teamclaw tasks",
                        "owners": ["alice"],
                        "provider_bot_ref": "teamclaw-bot:alice"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body = response_json(response).await;
    assert!(body["error"]
        .as_str()
        .unwrap()
        .contains("already registered"));
}

#[tokio::test]
async fn provider_admin_token_cannot_manage_another_provider() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider_a = register_provider(
        &app,
        json!({
            "mode": "static_bearer"
        }),
    )
    .await;
    let provider_b = register_provider(
        &app,
        json!({
            "mode": "static_bearer"
        }),
    )
    .await;
    let provider_b_id = provider_b["provider_id"].as_str().unwrap();
    let admin_token_a = provider_a["provider_admin_token"].as_str().unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_b_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token_a}"))
                .body(Body::from(
                    json!({
                        "name": "Code Reviewer",
                        "owners": ["11111111"],
                        "provider_bot_ref": "reviewer-v2"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["error"], "provider_id_mismatch");
}

async fn register_provider(app: &Router, auth: Value) -> Value {
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
                        "auth": auth
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    response_json(response).await
}

async fn register_provider_as(app: &Router, staff_no: &str) -> Value {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers")
                .header("content-type", "application/json")
                .header("x-test-staff-no", staff_no)
                .body(Body::from(
                    json!({
                        "name": "Provider",
                        "webhook_url": "https://provider.example.com/bcs/webhook",
                        "auth": {
                            "mode": "static_bearer"
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    response_json(response).await
}

async fn register_provider_bot(
    app: &Router,
    provider_id: &str,
    admin_token: &str,
    provider_bot_ref: &str,
) -> Value {
    let response = app
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
                        "summary": "Reviews code",
                        "owners": ["11111111"],
                        "provider_bot_ref": provider_bot_ref
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    response_json(response).await
}

async fn response_json(response: axum::response::Response) -> Value {
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    serde_json::from_slice(&body).unwrap()
}
