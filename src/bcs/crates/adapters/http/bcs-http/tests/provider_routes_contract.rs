use std::sync::Arc;

use async_trait::async_trait;
use axum::{
    Router,
    body::{Body, to_bytes},
    http::{HeaderMap, Request, StatusCode, Uri},
};
use bcs_auth_api::{AuthError, AuthPluginChain, AuthPrincipal, UserIdentityInfo};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::{BotControlPlaneCore, BotCore, ProviderCore, ProviderManagement};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_config::resolve_env_str;
use bcs_http::{
    router::build_router,
    service_key::{ApiKeyEntry, ApiKeyRegistry},
    state::{
        ChainUserIdentityPort, HttpAppState, HttpUserIdentity, UserIdentityPort,
        VisibilitySyncPort, VisibilitySyncRequest,
    },
};
use bcs_service_api::application::v1::ApplicationError;
use bcs_user_directory_api::{UserDirectoryPlugin, UserDirectoryProfile};
use bcs_service_api::{
    ActorKind, BotControlPlaneCoreService, BotControlPlanePatch, BotInternalAttributes,
    BotRegistryCoreService, EnsureOwnerEdgesResult, FriendCheckInStrategy,
    InternalBotAttributesService, PatchBotInternalAttributes, ProviderBotBindingRepoPort,
    ProviderBotCoreService, ProviderCoreService, ProviderCredential, ProviderCredentialRepoPort,
    ProviderRecord, ProviderRepoPort, RelationCoreService, RelationEdge,
    ServiceResult, UserVisibility,
};
use bcs_services_container::Services;
use serde_json::{Value, json};
use tempfile::TempDir;
use tokio::sync::Notify;
use tower::ServiceExt;

struct TestApp {
    app: Router,
    provider_repo: Arc<dyn ProviderRepoPort>,
    provider_credentials: Arc<dyn ProviderCredentialRepoPort>,
    registry: Arc<BotCore>,
    relation: Arc<RecordingRelationCoreService>,
    control_plane: Arc<BotControlPlaneCore>,
    internal_bot_attributes: Arc<RecordingInternalBotAttributesService>,
    visibility_sync: Option<Arc<RecordingVisibilitySyncPort>>,
    _temp_dir: TempDir,
}

#[derive(Default)]
struct RecordingVisibilitySyncPort {
    requests: tokio::sync::Mutex<Vec<VisibilitySyncRequest>>,
    notify: Notify,
}

#[async_trait::async_trait]
impl VisibilitySyncPort for RecordingVisibilitySyncPort {
    async fn sync_visibility(&self, request: VisibilitySyncRequest) {
        self.requests.lock().await.push(request);
        self.notify.notify_waiters();
    }
}

impl RecordingVisibilitySyncPort {
    async fn wait_for(&self, count: usize) {
        let mut retries = 0;
        loop {
            if self.requests.lock().await.len() >= count {
                return;
            }
            retries += 1;
            if retries > 200 {
                panic!(
                    "timed out waiting for {count} visibility sync requests, got {}",
                    self.requests.lock().await.len()
                );
            }
            tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        }
    }
}

#[derive(Default)]
struct RecordingInternalBotAttributesService {
    patches: tokio::sync::Mutex<Vec<PatchBotInternalAttributes>>,
}

#[async_trait]
impl InternalBotAttributesService for RecordingInternalBotAttributesService {
    async fn get(&self, _bot_id: String) -> Result<BotInternalAttributes, ApplicationError> {
        Ok(BotInternalAttributes {
            visibility: "protected".to_string(),
            user_visibility: UserVisibility::Protected,
            friend_ext: serde_json::Map::new(),
            friend_check_in_strategy: FriendCheckInStrategy::Approval,
        })
    }

    async fn patch(
        &self,
        command: PatchBotInternalAttributes,
    ) -> Result<BotInternalAttributes, ApplicationError> {
        let attributes = BotInternalAttributes {
            visibility: command
                .visibility
                .clone()
                .unwrap_or_else(|| "protected".to_string()),
            user_visibility: command.user_visibility.unwrap_or(UserVisibility::Protected),
            friend_ext: command.friend_ext.clone().unwrap_or_default(),
            friend_check_in_strategy: command
                .friend_check_in_strategy
                .unwrap_or(FriendCheckInStrategy::Approval),
        };
        self.patches.lock().await.push(command);
        Ok(attributes)
    }
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
    test_app_with_options(user_identity, user_directory, Vec::new(), Vec::new(), None)
}

fn test_app_with_allowed_switch_provider_ids(allowed_provider_ids: Vec<String>) -> TestApp {
    let chain = static_auth_chain("11111111", "Admin");
    test_app_with_options(
        Arc::new(ChainUserIdentityPort::new(chain)),
        None,
        allowed_provider_ids,
        Vec::new(),
        None,
    )
}

fn test_app_with_allowed_switch_provider_ids_and_visibility_sync(
    allowed_provider_ids: Vec<String>,
    visibility_sync: Arc<RecordingVisibilitySyncPort>,
) -> TestApp {
    let chain = static_auth_chain("11111111", "Admin");
    test_app_with_options(
        Arc::new(ChainUserIdentityPort::new(chain)),
        None,
        allowed_provider_ids,
        Vec::new(),
        Some(visibility_sync),
    )
}

fn test_app_with_options(
    user_identity: Arc<dyn UserIdentityPort>,
    user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
    allowed_provider_ids: Vec<String>,
    service_keys: Vec<ApiKeyEntry>,
    visibility_sync: Option<Arc<RecordingVisibilitySyncPort>>,
) -> TestApp {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let provider_store = Arc::new(MemoryProviderStore::new());
    let provider_repo: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let provider_credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
    let provider_bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
    let internal_bot_attributes = Arc::new(RecordingInternalBotAttributesService::default());
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    let control_plane = Arc::new(BotControlPlaneCore::new(
        bot_repo.clone(),
        provider_repo.clone(),
        provider_bindings.clone(),
    ));
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
    provider_management = provider_management.with_control_plane(control_plane.clone());
    let provider_management = Arc::new(provider_management);

    let services = Services::builder()
        .registry(registry_service)
        .provider_core(provider_core)
        .provider_bot_core(provider_bot_core)
        .provider_management(provider_management)
        .build_for_test();

    let mut state_builder = HttpAppState::new(services)
        .with_user_identity(user_identity)
        .with_allowed_switch_provider_ids(allowed_provider_ids)
        .with_internal_bot_attributes_service(internal_bot_attributes.clone())
        .with_service_api_keys(Arc::new(ApiKeyRegistry::new(service_keys)));
    if let Some(visibility_sync) = visibility_sync.clone() {
        state_builder = state_builder.with_visibility_sync(visibility_sync);
    }

    TestApp {
        app: build_router(state_builder),
        provider_repo,
        provider_credentials,
        registry,
        relation,
        control_plane,
        internal_bot_attributes,
        visibility_sync,
        _temp_dir: temp_dir,
    }
}

#[derive(Default)]
struct RecordingRelationCoreService {
    owner_edges: tokio::sync::Mutex<Vec<(String, String, String)>>,
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

    async fn lookup_department_by_staff_no(
        &self,
        _staff_no: &str,
    ) -> Result<Option<String>, bcs_user_directory_api::UserDirectoryError> {
        Ok(None)
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

async fn patch_provider_bot(
    app: &Router,
    provider_id: &str,
    admin_token: &str,
    provider_bot_ref: &str,
    patch: Value,
) -> (StatusCode, Value) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}/bots/{provider_bot_ref}"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(patch.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    (status, response_json(response).await)
}

#[tokio::test]
async fn patch_provider_bot_merges_and_preserves_unmodified_fields() {
    let TestApp {
        app, registry, _temp_dir, ..
    } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    // Seed a bot with full capabilities in one POST (the default helper omits
    // domains/skills/scopes; a second POST with the same ref is idempotent and
    // would NOT update them).
    let seeded = response_json(
        app.clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri(format!("/providers/{provider_id}/bots"))
                    .header("content-type", "application/json")
                    .header("authorization", format!("Bearer {admin_token}"))
                    .body(
                        Body::from(
                            json!({
                                "name": "Code Reviewer",
                                "summary": "Reviews code",
                                "owners": ["12345678"],
                                "provider_bot_ref": "reviewer-v2",
                                "domains": ["development", "security"],
                                "skills": ["code_review"],
                                "scopes": ["production"]
                            })
                            .to_string(),
                        ),
                    )
                    .unwrap(),
            )
            .await
            .unwrap(),
    )
    .await;
    let bot_uuid = seeded["bot_uuid"].as_str().unwrap();

    let (status, body) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({
            "name": "Senior Reviewer",
            "domains": ["database"]
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["bot_uuid"], bot_uuid);
    assert_eq!(body["name"], "Senior Reviewer");
    assert_eq!(body["domains"], json!(["database"]));
    // Unmodified fields preserved.
    assert_eq!(body["summary"], "Reviews code");
    assert_eq!(body["scopes"], json!(["production"]));
    let skills: Vec<&str> = body["skills"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v["name"].as_str().unwrap())
        .collect();
    assert_eq!(skills, vec!["code_review"]);
    // Persisted to the registry.
    let bot = registry.get(bot_uuid).await.expect("bot registered");
    assert_eq!(bot.capabilities.name.as_deref(), Some("Senior Reviewer"));
    assert_eq!(bot.capabilities.domains, vec!["database"]);
    assert_eq!(bot.capabilities.summary.as_deref(), Some("Reviews code"));
}

#[tokio::test]
async fn patch_provider_bot_clears_array_with_empty_vec_and_keeps_when_field_absent() {
    let TestApp {
        app, registry, _temp_dir, ..
    } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let bot =
        register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;
    let bot_uuid = bot["bot_uuid"].as_str().unwrap();

    // Absent fields leave the (default) empty arrays unchanged.
    let (status, body) =
        patch_provider_bot(&app, provider_id, admin_token, "reviewer-v2", json!({})).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["bot_uuid"], bot_uuid);

    // Now seed domains/scopes and clear them with empty vecs.
    patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({"domains": ["development"], "scopes": ["production"]}),
    )
    .await;
    // Confirm they landed before clearing.
    let seeded = registry.get(bot_uuid).await.expect("bot registered");
    assert_eq!(seeded.capabilities.domains, vec!["development"]);
    assert_eq!(seeded.capabilities.scopes, vec!["production"]);

    let (status, body) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({"domains": [], "scopes": []}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["domains"], json!([]));
    assert_eq!(body["scopes"], json!([]));
    // Regression guard: the cleared arrays must persist in the *live* registry
    // (not just the response / DB). Previously `register`'s empty-array skip
    // left the in-memory bot with the old, non-empty arrays until restart.
    let cleared = registry.get(bot_uuid).await.expect("bot registered");
    assert!(cleared.capabilities.domains.is_empty(), "domains must be cleared in registry");
    assert!(cleared.capabilities.scopes.is_empty(), "scopes must be cleared in registry");
    // An unmodified field (e.g. visibility default) must be preserved.
    assert!(
        matches!(cleared.capabilities.visibility.as_str(), "public" | "protected" | "private"),
        "visibility must remain valid, got {}",
        cleared.capabilities.visibility
    );
}

#[tokio::test]
async fn patch_provider_bot_accepts_structured_skills_and_round_trips() {
    // PATCH skills use structured {name, description} objects only (the legacy
    // bare-string form is rejected). The request shape matches the response so
    // a client can round-trip it, and skill descriptions are mutable.
    let TestApp { app, registry, _temp_dir, .. } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    let bot = register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;
    let bot_uuid = bot["bot_uuid"].as_str().unwrap();

    // Structured skills with a description are accepted and round-tripped in
    // the response (objects, not strings).
    let (status, body) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({
            "skills": [
                {"name": "code_review", "description": "Reviews submitted code"}
            ]
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        body["skills"],
        json!([{"name": "code_review", "description": "Reviews submitted code"}])
    );

    // Persisted to the registry with the description intact.
    let stored = registry.get(bot_uuid).await.expect("bot registered");
    let skills = &stored.capabilities.skills;
    assert_eq!(skills.len(), 1);
    assert_eq!(skills[0].name, "code_review");
    assert_eq!(skills[0].description.as_deref(), Some("Reviews submitted code"));

    // A subsequent PATCH can update only the description.
    let (status, body) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({
            "skills": [{"name": "code_review", "description": "Senior code reviews"}]
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        body["skills"],
        json!([{"name": "code_review", "description": "Senior code reviews"}])
    );

    // Skills can be cleared entirely (empty array).
    let (status, body) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({"skills": []}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["skills"], json!([]));
    let cleared = registry.get(bot_uuid).await.expect("bot registered");
    assert!(cleared.capabilities.skills.is_empty());
}

#[tokio::test]
async fn patch_provider_bot_rejects_legacy_string_skills() {
    // The legacy bare-string skill form (accepted by registration) is rejected
    // on PATCH: skills must be structured {name, description} objects.
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}/bots/reviewer-v2"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(json!({"skills": ["code_review"]}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
}

#[tokio::test]
async fn patch_provider_bot_rejects_invalid_visibility() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;

    let (status, body) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({"visibility": "friends"}),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(
        body["error"]
            .as_str()
            .unwrap()
            .contains("visibility must be 'public', 'protected', or 'private'")
    );
}

#[tokio::test]
async fn patch_provider_bot_accepts_admin_token_without_human_identity() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;

    let (status, _) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({"name": "Renamed"}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
}

#[tokio::test]
async fn patch_provider_bot_rejects_without_admin_token() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}/bots/reviewer-v2"))
                .header("content-type", "application/json")
                .body(Body::from(json!({"name": "Renamed"}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);

    let response = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}/bots/reviewer-v2"))
                .header("content-type", "application/json")
                .header("authorization", "Bearer not-the-admin-token")
                .body(Body::from(json!({"name": "Renamed"}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn patch_provider_bot_preserves_agent_code_for_agentpass_bot() {
    // The whole point of the core-layer agent_code reconstruction: an AgentPass
    // bot's routing identifier (== its provider_bot_ref) must survive an update.
    // `registry.get()` strips agent_code, so assert via `find_bot_by_agent_code`.
    let TestApp { app, registry, _temp_dir, .. } = test_app();
    let provider = register_provider(&app, json!({"mode": "agentpass"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let bot = register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;
    let bot_uuid = bot["bot_uuid"].as_str().unwrap();
    assert_eq!(
        registry.find_bot_by_agent_code("reviewer-v2").await,
        Some(bot_uuid.to_string())
    );

    let (status, _) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({"name": "Renamed Agent"}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);

    // agent_code must still resolve to the same bot after the update.
    assert_eq!(
        registry.find_bot_by_agent_code("reviewer-v2").await,
        Some(bot_uuid.to_string())
    );
}

#[tokio::test]
async fn patch_provider_bot_rejects_unreachable_bot() {
    // When the bound bot has been soft-deleted (unreachable from the registry),
    // the update surfaces BotNotFound (404), never silently no-ops.
    let TestApp { app, registry, _temp_dir, .. } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    let bot = register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;
    let bot_uuid = bot["bot_uuid"].as_str().unwrap();

    let _ = registry.soft_delete(bot_uuid).await;

    let (status, _) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({"name": "Renamed"}),
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn patch_provider_bot_returns_not_found_for_unknown_ref() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let (status, _) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "nonexistent-bot",
        json!({"name": "Renamed"}),
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
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
    assert!(body["coordination"]
        .get("worker_send_task_message_enabled")
        .is_none());
}

#[tokio::test]
async fn register_provider_round_trips_native_mcp_tool_name_mapping() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let assign_tool = "mcp_mcp.ant.agentclawscs.bcs_mcp_bcs_assign_task";
    let send_message_tool = "mcp_mcp.ant.agentclawscs.bcs_mcp_bcs_send_task_message";

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
                            "mode": "native_mcp",
                            "worker_send_task_message_enabled": false,
                            "mcp_server": "mcp.ant.agentclawscs.bcs",
                            "tool_name_mapping": {
                                (assign_tool): "bcs_assign_task",
                                (send_message_tool): "bcs_send_task_message"
                            }
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
    assert_eq!(body["coordination"]["mode"], "native_mcp");
    assert_eq!(
        body["coordination"]["tool_name_mapping"][assign_tool],
        "bcs_assign_task"
    );
    assert_eq!(
        body["coordination"]["tool_name_mapping"][send_message_tool],
        "bcs_send_task_message"
    );
    assert_eq!(
        body["coordination"]["worker_send_task_message_enabled"],
        false
    );
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
async fn patch_provider_updates_and_returns_admin_callback_url() {
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
    let callback_url = "https://provider.example.com/bcs/admin-callback";

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
                        "admin_callback_url": callback_url
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["admin_callback_url"], callback_url);

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
    assert_eq!(body["admin_callback_url"], callback_url);
}

#[tokio::test]
async fn organization_admin_run_requires_provider_admin_token_in_unified_envelope() {
    let app = test_app();
    let response = app
        .app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/organizations/engineering/admin-runs")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "target_bot_uuid": "bot_target",
                        "message": {
                            "role": "user",
                            "content": [{ "type": "text", "text": "please review" }]
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
    assert_eq!(body["code"], 40101);
    assert_eq!(body["message"], "valid provider admin token is required");
    assert!(body["request_id"].as_str().is_some());
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
async fn provider_bot_attributes_allow_an_allowlisted_provider_admin_to_manage_unbound_plugin_bots() {
    let provider_id = "prv_internal_attributes".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        internal_bot_attributes,
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

    let register = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Teamclaw Bot",
                        "owners": ["alice"],
                        "provider_bot_ref": "teamclaw-bot:alice",
                        "connection_mode": "plugin"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(register.status(), StatusCode::OK);

    let get = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!(
                    "/providers/{provider_id}/bots/teamclaw-bot:alice/attributes"
                ))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(get.status(), StatusCode::OK);
    let get_body = response_json(get).await;
    assert_eq!(get_body["visibility"], "protected");
    assert_eq!(get_body["user_visibility"], "protected");
    assert_eq!(get_body["friend_ext"], json!({}));
    assert_eq!(get_body["friend_check_in_strategy"], "APPROVAL");

    let patch = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!(
                    "/providers/{provider_id}/bots/teamclaw-bot:alice/attributes"
                ))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "visibility": "private",
                        "user_visibility": "public",
                        "friend_ext": {"department_code": "TECH"},
                        "friend_check_in_strategy": "DEPT_FREE"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(patch.status(), StatusCode::OK);
    let patch_body = response_json(patch).await;
    assert_eq!(patch_body["visibility"], "private");
    assert_eq!(patch_body["user_visibility"], "public");
    assert_eq!(patch_body["friend_ext"]["department_code"], "TECH");
    assert_eq!(patch_body["friend_check_in_strategy"], "DEPT_FREE");

    for body in [
        json!({}),
        json!({"user_visibility": "public", "forged": true}),
        json!({"user_visibility": null}),
        json!({"friend_ext": []}),
    ] {
        let invalid = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("PATCH")
                    .uri(format!(
                        "/providers/{provider_id}/bots/teamclaw-bot:alice/attributes"
                    ))
                    .header("content-type", "application/json")
                    .header("authorization", format!("Bearer {admin_token}"))
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(invalid.status(), StatusCode::BAD_REQUEST);
    }

    let patches = internal_bot_attributes.patches.lock().await;
    assert_eq!(patches.len(), 1);
    assert_eq!(patches[0].bot_id, "teamclaw-bot:alice");
    assert_eq!(patches[0].visibility.as_deref(), Some("private"));
    assert_eq!(patches[0].user_visibility, Some(UserVisibility::Public));
    assert_eq!(
        patches[0].friend_check_in_strategy,
        Some(FriendCheckInStrategy::DeptFree)
    );
}

#[tokio::test]
async fn provider_bot_attributes_reject_a_disabled_provider_for_get_and_patch() {
    let provider_id = "prv_disabled_attributes".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
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

    let register = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Teamclaw Bot",
                        "owners": ["alice"],
                        "provider_bot_ref": "teamclaw-bot:alice"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(register.status(), StatusCode::OK);

    provider_repo
        .update_provider_disabled(&provider_id, true, 2)
        .await
        .expect("disable provider");

    let get = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!(
                    "/providers/{provider_id}/bots/teamclaw-bot:alice/attributes"
                ))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(get.status(), StatusCode::FORBIDDEN);

    let patch = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!(
                    "/providers/{provider_id}/bots/teamclaw-bot:alice/attributes"
                ))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(json!({ "user_visibility": "public" }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(patch.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn provider_bot_attributes_fail_closed_when_provider_is_not_allowlisted() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider_as(&app, "11111111").await;
    let provider_id = provider["provider_id"].as_str().expect("provider id");
    let admin_token = provider["provider_admin_token"]
        .as_str()
        .expect("provider admin token");
    let registered_bot = app
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
                        "provider_bot_ref": "reviewer"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    let bot_uuid = response_json(registered_bot).await["bot_uuid"]
        .as_str()
        .expect("bot uuid")
        .to_string();

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!("/providers/{provider_id}/bots/{bot_uuid}/attributes"))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
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

async fn set_task_modes(
    control_plane: &Arc<BotControlPlaneCore>,
    bot_uuid: &str,
    env: &str,
    task_claim_mode: Option<bool>,
    task_dream_mode: Option<bool>,
) {
    control_plane
        .patch(
            bot_uuid,
            env,
            BotControlPlanePatch {
                name: None,
                visibility: None,
                status: None,
                descriptor: None,
                task_claim_mode,
                task_dream_mode,
                ..Default::default()
            },
        )
        .await
        .expect("patch control-plane toggles")
        .expect("bot control-plane record should exist after onboarding");
}

async fn task_mode_roster(
    app: &Router,
    provider_id: &str,
    token: Option<&str>,
    query: &str,
) -> (StatusCode, Value) {
    let mut builder = Request::builder()
        .method("GET")
        .uri(format!("/providers/{provider_id}/bots/by-task-modes{query}"));
    if let Some(token) = token {
        builder = builder.header("authorization", format!("Bearer {token}"));
    }
    let response = app
        .clone()
        .oneshot(builder.body(Body::empty()).unwrap())
        .await
        .unwrap();
    let status = response.status();
    (status, response_json(response).await)
}

fn roster_bot_ids(body: &Value) -> Vec<String> {
    body["items"]
        .as_array()
        .expect("roster response has items array")
        .iter()
        .map(|item| item["bot_id"].as_str().expect("item has bot_id").to_string())
        .collect()
}

fn sorted_ids(mut ids: Vec<String>) -> Vec<String> {
    ids.sort();
    ids
}

#[tokio::test]
async fn list_provider_bots_by_task_modes_is_env_scoped_for_allow_listed_provider() {
    // The roster is admission-gated by `allowed_switch_provider_ids` and
    // env-scoped: it returns every current-env bot whose task-mode toggles
    // satisfy the filter, regardless of which provider a bot is bound to.
    let provider_id = "prv_task_modes".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        control_plane,
        provider_repo,
        provider_credentials,
        ..
    } = test_app_with_allowed_switch_provider_ids(vec![provider_id.clone()]);
    seed_provider_admin(
        &provider_repo,
        &provider_credentials,
        &provider_id,
        admin_token,
        "11111111",
    )
    .await;

    // A second provider (NOT in the allow-list) hosts bot-d. Under the new
    // env-scoped semantics d must still appear in provider_id's roster.
    let other = register_provider(&app, json!({ "mode": "static_bearer" })).await;
    let other_provider_id = other["provider_id"].as_str().unwrap().to_string();
    let other_token = other["provider_admin_token"].as_str().unwrap().to_string();

    let bot_a = register_provider_bot(&app, &provider_id, admin_token, "bot-a").await;
    let bot_b = register_provider_bot(&app, &provider_id, admin_token, "bot-b").await;
    let bot_c = register_provider_bot(&app, &provider_id, admin_token, "bot-c").await;
    let bot_d = register_provider_bot(&app, &other_provider_id, &other_token, "bot-d").await;
    let uuid_a = bot_a["bot_uuid"].as_str().unwrap().to_string();
    let uuid_b = bot_b["bot_uuid"].as_str().unwrap().to_string();
    let uuid_c = bot_c["bot_uuid"].as_str().unwrap().to_string();
    let uuid_d = bot_d["bot_uuid"].as_str().unwrap().to_string();

    let env = resolve_env_str();
    // a = claim, b = dream, c = claim+dream, d = claim+dream (other provider).
    set_task_modes(&control_plane, &uuid_a, &env, Some(true), Some(false)).await;
    set_task_modes(&control_plane, &uuid_b, &env, Some(false), Some(true)).await;
    set_task_modes(&control_plane, &uuid_c, &env, Some(true), Some(true)).await;
    set_task_modes(&control_plane, &uuid_d, &env, Some(true), Some(true)).await;

    // No toggles => all current-env bots (d included; binding scoping removed).
    let (status, body) = task_mode_roster(&app, &provider_id, Some(admin_token), "").await;
    assert_eq!(status, StatusCode::OK, "no-filter roster failed: {body}");
    assert_eq!(
        sorted_ids(roster_bot_ids(&body)),
        sorted_ids(vec![
            uuid_a.clone(),
            uuid_b.clone(),
            uuid_c.clone(),
            uuid_d.clone()
        ])
    );

    // claim_mode=true, match=any => a, c, d (every env bot with claim on).
    let (status, body) = task_mode_roster(
        &app,
        &provider_id,
        Some(admin_token),
        "?task_claim_mode=true&match=any",
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        sorted_ids(roster_bot_ids(&body)),
        sorted_ids(vec![uuid_a.clone(), uuid_c.clone(), uuid_d.clone()])
    );

    // claim=true AND dream=true, match=all => c, d.
    let (status, body) = task_mode_roster(
        &app,
        &provider_id,
        Some(admin_token),
        "?task_claim_mode=true&task_dream_mode=true&match=all",
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        sorted_ids(roster_bot_ids(&body)),
        sorted_ids(vec![uuid_c.clone(), uuid_d.clone()])
    );

    // claim=true OR dream=true, match=any => a, b, c, d.
    let (status, body) = task_mode_roster(
        &app,
        &provider_id,
        Some(admin_token),
        "?task_claim_mode=true&task_dream_mode=true&match=any",
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        sorted_ids(roster_bot_ids(&body)),
        sorted_ids(vec![
            uuid_a.clone(),
            uuid_b.clone(),
            uuid_c.clone(),
            uuid_d.clone()
        ])
    );

    // dream=true, match=any => b, c, d.
    let (status, body) = task_mode_roster(
        &app,
        &provider_id,
        Some(admin_token),
        "?task_dream_mode=true&match=any",
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        sorted_ids(roster_bot_ids(&body)),
        sorted_ids(vec![uuid_b.clone(), uuid_c.clone(), uuid_d.clone()])
    );

    // Missing token => 401.
    let (status, body) =
        task_mode_roster(&app, &provider_id, None, "?task_claim_mode=true").await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["status"], 401);

    // Wrong token => 401.
    let (status, body) = task_mode_roster(
        &app,
        &provider_id,
        Some("not-the-admin-token"),
        "?task_claim_mode=true",
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["status"], 401);

    // Token that authenticates a different provider than the path => 403.
    let (status, body) = task_mode_roster(
        &app,
        &provider_id,
        Some(other_token.as_str()),
        "?task_claim_mode=true",
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["error"], "provider_id_mismatch");

    // Provider admin token valid, but the path provider is not allow-listed.
    let (status, body) = task_mode_roster(
        &app,
        &other_provider_id,
        Some(other_token.as_str()),
        "?task_claim_mode=true",
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(
        body["error"],
        format!(
            "provider '{}' is not allowed to access the task-mode roster",
            other_provider_id
        )
    );
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
async fn seed_provider_admin(
    provider_repo: &Arc<dyn ProviderRepoPort>,
    provider_credentials: &Arc<dyn ProviderCredentialRepoPort>,
    provider_id: &str,
    admin_token: &str,
    created_by: &str,
) {
    provider_repo
        .insert_provider(ProviderRecord {
            provider_id: provider_id.to_string(),
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
            created_by: created_by.to_string(),
            owners: format!("[\"{created_by}\"]"),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("seed provider");
    provider_credentials
        .insert_credential(ProviderCredential {
            provider_id: provider_id.to_string(),
            credential_kind: "provider_admin".to_string(),
            secret_value: admin_token.to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("seed provider admin credential");
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

/// Register a provider bot with an explicit `connection_mode` and return the
/// raw response (status not asserted — caller decides).
async fn register_provider_bot_mode(
    app: &Router,
    provider_id: &str,
    admin_token: &str,
    provider_bot_ref: &str,
    mode: &str,
) -> (StatusCode, Value) {
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
                        "name": "Plugin Bot",
                        "summary": "Connected via BCN plugin",
                        "owners": ["11111111"],
                        "provider_bot_ref": provider_bot_ref,
                        "connection_mode": mode
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    (status, response_json(response).await)
}

#[tokio::test]
async fn register_provider_bot_plugin_mode_rejected_for_non_allow_listed_provider() {
    // The default test_app is NOT allow-listed, so plugin mode must be
    // refused with 400 before reaching the service layer.
    let app = test_app().app;
    let provider = register_provider(&app, json!({ "mode": "static_bearer" })).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let (status, body) =
        register_provider_bot_mode(&app, provider_id, admin_token, "plugin-bot-1", "plugin").await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    let msg = body["error"].as_str().unwrap_or_default();
    assert!(
        msg.contains("connection_mode plugin requires an allow-listed provider"),
        "unexpected rejection message: {msg}"
    );
}

#[tokio::test]
async fn register_provider_bot_plugin_mode_accepted_for_allow_listed_provider() {
    let provider_id = "prv_plugin_allowed".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
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
        .expect("seed plugin provider record");
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
        .expect("seed plugin provider admin credential");

    let bot_ref = "plugin-bot:alice";
    let (status, body) =
        register_provider_bot_mode(&app, &provider_id, admin_token, bot_ref, "plugin").await;
    assert_eq!(
        status,
        StatusCode::OK,
        "plugin mode should be accepted for allow-listed provider: {body}"
    );
    // deterministic id == provider_bot_ref (allow-listed + plugin)
    assert_eq!(body["bot_uuid"].as_str(), Some(bot_ref));
    // plugin mode never writes a binding → response carries no runtime token
    // (the real token comes from the WS handshake); the MOCK sentinel is not
    // exposed.
    assert!(
        body["bot_runtime_token"].is_null(),
        "plugin mode response must not expose a runtime token, got: {body}"
    );
}

async fn response_json(response: axum::response::Response) -> Value {
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    serde_json::from_slice(&body).unwrap()
}

#[tokio::test]
async fn list_provider_bots_by_task_modes_rejects_invalid_toggle_and_accepts_false() {
    let provider_id = "prv_task_modes_toggle".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        ..
    } = test_app_with_allowed_switch_provider_ids(vec![provider_id.clone()]);
    seed_provider_admin(
        &provider_repo,
        &provider_credentials,
        &provider_id,
        admin_token,
        "11111111",
    )
    .await;

    // `false` parses to Some(false) — exercises the false/0 arm of
    // parse_task_mode_toggle. With no current-env bots the roster is empty
    // but 200.
    let (status, body) =
        task_mode_roster(&app, &provider_id, Some(admin_token), "?task_claim_mode=false").await;
    assert_eq!(status, StatusCode::OK, "false toggle failed: {body}");
    assert!(body["items"].is_array(), "false toggle response missing items: {body}");

    // `0` is the other accepted false spelling (same parse arm).
    let (status, _body) =
        task_mode_roster(&app, &provider_id, Some(admin_token), "?task_dream_mode=0").await;
    assert_eq!(status, StatusCode::OK, "0 toggle failed");

    // An unrecognized toggle value surfaces as 400 bad_request from the handler
    // (parse_task_mode_toggle error arm), before the service is consulted.
    let (status, body) =
        task_mode_roster(&app, &provider_id, Some(admin_token), "?task_claim_mode=maybe").await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "invalid toggle not rejected: {body}");
    assert_eq!(body["status"], 400);
}

async fn seed_allowed_provider(
    provider_repo: &Arc<dyn ProviderRepoPort>,
    provider_credentials: &Arc<dyn ProviderCredentialRepoPort>,
    provider_id: &str,
    admin_token: &str,
) {
    seed_provider_admin(provider_repo, provider_credentials, provider_id, admin_token, "11111111")
        .await;
}

#[tokio::test]
async fn register_provider_bot_dispatches_visibility_sync_for_allowlisted_provider() {
    let provider_id = "prv_visibility_sync_allowed".to_string();
    let admin_token = "admin-token";
    let sync = Arc::new(RecordingVisibilitySyncPort::default());
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        visibility_sync,
        ..
    } = test_app_with_allowed_switch_provider_ids_and_visibility_sync(
        vec![provider_id.clone()],
        sync.clone(),
    );
    seed_allowed_provider(&provider_repo, &provider_credentials, &provider_id, &admin_token).await;

    let bot = register_provider_bot(&app, &provider_id, &admin_token, "teamclaw-bot:alice").await;
    let bot_uuid = bot["bot_uuid"].as_str().unwrap().to_string();
    let sync = visibility_sync.expect("visibility sync port wired");
    sync.wait_for(1).await;
    let requests = sync.requests.lock().await;
    assert_eq!(requests.len(), 1, "expected exactly one visibility sync dispatch");
    assert_eq!(requests[0].bot_uuid, bot_uuid);
    assert_eq!(requests[0].actor_kind, ActorKind::Bot);
    assert_eq!(requests[0].capabilities.visibility, "protected");
    assert_eq!(requests[0].visibility, "protected");
}

#[tokio::test]
async fn register_provider_bot_skips_visibility_sync_for_non_allowlisted_provider() {
    let sync = Arc::new(RecordingVisibilitySyncPort::default());
    let TestApp {
        app,
        visibility_sync,
        ..
    } = test_app_with_allowed_switch_provider_ids_and_visibility_sync(Vec::new(), sync);
    let provider = register_provider(&app, json!({ "mode": "static_bearer" })).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let _ = register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;
    // No notify is emitted for 0 requests, so poll briefly instead of wait_for.
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    let sync = visibility_sync.expect("visibility sync port wired");
    let requests = sync.requests.lock().await;
    assert!(
        requests.is_empty(),
        "non-allowlisted provider must not dispatch visibility sync, got {requests:?}"
    );
}

#[tokio::test]
async fn register_provider_bot_skips_visibility_sync_for_idempotent_gateway_replay() {
    let provider_id = "prv_visibility_sync_replay".to_string();
    let admin_token = "admin-token";
    let sync = Arc::new(RecordingVisibilitySyncPort::default());
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        visibility_sync,
        ..
    } = test_app_with_allowed_switch_provider_ids_and_visibility_sync(
        vec![provider_id.clone()],
        sync,
    );
    seed_allowed_provider(&provider_repo, &provider_credentials, &provider_id, &admin_token).await;

    let first = register_provider_bot(&app, &provider_id, &admin_token, "teamclaw-bot:alice").await;
    let first_bot_uuid = first["bot_uuid"].as_str().unwrap().to_string();
    let sync = visibility_sync.expect("visibility sync port wired");
    sync.wait_for(1).await;

    // Idempotent replay with the same provider_bot_ref: Gateway short-circuits in
    // core (`duplicate_registration=true`) and the message indicates the existing
    // bot was returned.
    let _ = register_provider_bot(&app, &provider_id, &admin_token, "teamclaw-bot:alice").await;
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;

    let requests = sync.requests.lock().await;
    assert_eq!(
        requests.len(),
        1,
        "idempotent replay must not dispatch a second visibility sync, got {requests:?}"
    );
    assert_eq!(requests[0].bot_uuid, first_bot_uuid);
}

#[tokio::test]
async fn register_provider_bot_dispatches_visibility_sync_for_plugin_mode_allowlisted() {
    let provider_id = "prv_visibility_sync_plugin".to_string();
    let admin_token = "admin-token";
    let sync = Arc::new(RecordingVisibilitySyncPort::default());
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        visibility_sync,
        ..
    } = test_app_with_allowed_switch_provider_ids_and_visibility_sync(
        vec![provider_id.clone()],
        sync,
    );
    seed_allowed_provider(&provider_repo, &provider_credentials, &provider_id, &admin_token).await;

    let bot_ref = "plugin-bot:alice";
    let (status, body) =
        register_provider_bot_mode(&app, &provider_id, &admin_token, bot_ref, "plugin").await;
    assert_eq!(status, StatusCode::OK, "plugin mode rejected: {body}");
    assert_eq!(body["bot_uuid"].as_str(), Some(bot_ref));

    let sync = visibility_sync.expect("visibility sync port wired");
    sync.wait_for(1).await;
    let requests = sync.requests.lock().await;
    assert_eq!(
        requests.len(),
        1,
        "plugin mode over allowlisted provider should dispatch visibility sync once, got {requests:?}"
    );
    assert_eq!(requests[0].bot_uuid, bot_ref);
    assert_eq!(requests[0].actor_kind, ActorKind::Bot);
}

#[tokio::test]
async fn list_provider_bots_by_task_modes_parses_visibility_status_user_visibility_filters() {
    // Exercises the parser arms added for the visibility / status /
    // user_visibility roster filters. Every accepted spelling returns 200;
    // unrecognized status / user_visibility values surface as 400 bad_request
    // from the handler before the service is consulted. No bots are seeded —
    // the parser branches are covered regardless of roster cardinality (the
    // None/empty arms are already covered by the toggle tests that omit them).
    let provider_id = "prv_task_modes_filters".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        ..
    } = test_app_with_allowed_switch_provider_ids(vec![provider_id.clone()]);
    seed_provider_admin(
        &provider_repo,
        &provider_credentials,
        &provider_id,
        admin_token,
        "11111111",
    )
    .await;

    // Accepted status spellings => 200 (online + hidden arms of parse_actor_status).
    let (status, _body) =
        task_mode_roster(&app, &provider_id, Some(admin_token), "?status=online").await;
    assert_eq!(status, StatusCode::OK, "status=online rejected");
    let (status, _body) =
        task_mode_roster(&app, &provider_id, Some(admin_token), "?status=hidden").await;
    assert_eq!(status, StatusCode::OK, "status=hidden rejected");

    // Accepted user_visibility spellings => 200 (public/protected/private arms).
    for value in ["public", "protected", "private"] {
        let (status, _body) = task_mode_roster(
            &app,
            &provider_id,
            Some(admin_token),
            &format!("?user_visibility={value}"),
        )
        .await;
        assert_eq!(status, StatusCode::OK, "user_visibility={value} rejected");
    }

    // Non-empty visibility => 200 (Some arm of parse_non_empty_query).
    let (status, _body) =
        task_mode_roster(&app, &provider_id, Some(admin_token), "?visibility=public").await;
    assert_eq!(status, StatusCode::OK, "visibility=public rejected");

    // Empty spellings are treated as "no filter" => 200 (empty -> None arms).
    let (status, _body) = task_mode_roster(
        &app,
        &provider_id,
        Some(admin_token),
        "?status=&user_visibility=&visibility=",
    )
    .await;
    assert_eq!(status, StatusCode::OK, "empty filter values rejected");

    // Unrecognized status => 400 (error arm of parse_actor_status).
    let (status, body) =
        task_mode_roster(&app, &provider_id, Some(admin_token), "?status=maybe").await;
    assert_eq!(
        status,
        StatusCode::BAD_REQUEST,
        "invalid status not rejected: {body}"
    );
    assert_eq!(body["status"], 400);

    // Unrecognized user_visibility => 400 (error arm of parse_user_visibility).
    let (status, body) = task_mode_roster(
        &app,
        &provider_id,
        Some(admin_token),
        "?user_visibility=secret",
    )
    .await;
    assert_eq!(
        status,
        StatusCode::BAD_REQUEST,
        "invalid user_visibility not rejected: {body}"
    );
    assert_eq!(body["status"], 400);
}
