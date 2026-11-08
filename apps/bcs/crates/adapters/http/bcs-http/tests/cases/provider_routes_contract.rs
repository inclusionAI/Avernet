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
use bcs_bot_store::provider::MemoryBotProviderStore;
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
    // The production composition always wires the Bot
    // Provider metadata over the SAME shared MemoryBotState,
    // so the atomic retirement contract tests run on the
    // real wiring.
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
    let provider_core_impl = Arc::new(ProviderCore::new(
        provider_repo.clone(),
        provider_credentials.clone(),
        provider_bindings,
        registry_service.clone(),
    )
    .with_bot_provider_repo(bot_providers));
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
        .bot_query(Arc::new(bcs_bot::Bot::new(registry_service.clone())))
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
            slug: None,
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


async fn response_json(response: axum::response::Response) -> Value {
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    serde_json::from_slice(&body).unwrap()
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






#[path = "provider_routes_contract/registration.rs"]
mod registration;
#[path = "provider_routes_contract/bot_lifecycle.rs"]
mod bot_lifecycle;
#[path = "provider_routes_contract/attributes.rs"]
mod attributes;
#[path = "provider_routes_contract/management.rs"]
mod management;
#[path = "provider_routes_contract/caller_caps.rs"]
mod caller_caps;
