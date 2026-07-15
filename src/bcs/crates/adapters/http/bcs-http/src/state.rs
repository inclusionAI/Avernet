use crate::service_key::ApiKeyRegistry;
use bcs_auth_api::{AuthError, UserIdentityInfo};
use bcs_channel_api::ChannelHttpIngressRegistry;
use bcs_config_api::ManifestConfig;
use bcs_domain::BotCapabilities;
use bcs_route_security::OutboundUrlGuard;
pub use bcs_service_api::{ChatRunCleanupPort, ChatRunEventPort};
use bcs_service_api::{ProviderCredentialRepoPort, ProviderStreamGrayList};
use bcs_services_container::Services;
use std::sync::Arc;
use std::{
    collections::HashMap,
    time::{SystemTime, UNIX_EPOCH},
};

#[async_trait::async_trait]
pub trait HealthPort: Send + Sync {
    async fn health(&self) -> serde_json::Value;
}

#[derive(Debug, Default)]
pub struct DefaultHealthPort;

#[async_trait::async_trait]
impl HealthPort for DefaultHealthPort {
    async fn health(&self) -> serde_json::Value {
        serde_json::json!({
            "status": "ok"
        })
    }
}

#[derive(Debug, Clone, Default)]
pub struct HttpUserIdentity {
    pub staff_no: Option<String>,
    pub nick_name: Option<String>,
}

#[async_trait::async_trait]
pub trait UserIdentityPort: Send + Sync {
    async fn extract(
        &self,
        headers: &axum::http::HeaderMap,
        uri: &axum::http::Uri,
    ) -> Option<HttpUserIdentity>;

    async fn ensure_identity(
        &self,
        auth_source: &str,
        external_user_id: &str,
        external_user_name: Option<&str>,
        avatar: Option<&str>,
        env: &str,
    ) -> Result<String, AuthError>;

    async fn get_identity_by_token(
        &self,
        token: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError>;

    async fn get_identity_by_user_id(
        &self,
        user_id: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError>;

    async fn update_token(
        &self,
        user_id: &str,
        token: &str,
        expire_at: u64,
    ) -> Result<(), AuthError>;
}

/// `UserIdentityPort` backed by the auth plugin chain. Runs the chain over the
/// request headers and maps the resolved principal to staff_no / nick_name.
/// Delegates identity persistence methods to an optional inner
/// `bcs_auth_api::UserIdentityPort`.
pub struct ChainUserIdentityPort {
    chain: Arc<bcs_auth_api::AuthPluginChain>,
    inner: Option<Arc<dyn bcs_auth_api::UserIdentityPort>>,
}

impl std::fmt::Debug for ChainUserIdentityPort {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ChainUserIdentityPort")
            .field("chain", &"<AuthPluginChain>")
            .finish()
    }
}

impl ChainUserIdentityPort {
    pub fn new(chain: Arc<bcs_auth_api::AuthPluginChain>) -> Self {
        Self { chain, inner: None }
    }

    pub fn with_inner(mut self, inner: Arc<dyn bcs_auth_api::UserIdentityPort>) -> Self {
        self.inner = Some(inner);
        self
    }
}

#[async_trait::async_trait]
impl UserIdentityPort for ChainUserIdentityPort {
    async fn extract(
        &self,
        headers: &axum::http::HeaderMap,
        _uri: &axum::http::Uri,
    ) -> Option<HttpUserIdentity> {
        let result = self.chain.authenticate(headers).await.ok()?;
        let principal = result.principal?;
        Some(HttpUserIdentity {
            staff_no: principal.user_id,
            nick_name: principal.user_name,
        })
    }

    async fn ensure_identity(
        &self,
        auth_source: &str,
        external_user_id: &str,
        external_user_name: Option<&str>,
        avatar: Option<&str>,
        env: &str,
    ) -> Result<String, AuthError> {
        match &self.inner {
            Some(port) => {
                port.ensure_identity(
                    auth_source,
                    external_user_id,
                    external_user_name,
                    avatar,
                    env,
                )
                .await
            }
            None => Err(AuthError::LookupFailed(
                "no identity port configured".to_string(),
            )),
        }
    }

    async fn get_identity_by_token(
        &self,
        token: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        match &self.inner {
            Some(port) => port.get_identity_by_token(token).await,
            None => Ok(None),
        }
    }

    async fn get_identity_by_user_id(
        &self,
        user_id: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        match &self.inner {
            Some(port) => port.get_identity_by_user_id(user_id).await,
            None => Ok(None),
        }
    }

    async fn update_token(
        &self,
        user_id: &str,
        token: &str,
        expire_at: u64,
    ) -> Result<(), AuthError> {
        match &self.inner {
            Some(port) => port.update_token(user_id, token, expire_at).await,
            None => Ok(()),
        }
    }
}

#[async_trait::async_trait]
pub trait BotRuntimeTokenResolverPort: Send + Sync {
    async fn resolve_agentpass_agent_code(&self, token: &str) -> Option<String>;

    /// Look up a provider_admin credential by its secret value. Returns the
    /// owning `provider_id` when the token matches, `None` otherwise.
    async fn try_provider_admin(&self, _token: &str) -> Option<String> {
        None
    }
}

#[derive(Clone, Default)]
pub struct BcsHttpAuthBotRuntimeTokenResolver {
    credentials: Option<Arc<dyn ProviderCredentialRepoPort>>,
}

impl BcsHttpAuthBotRuntimeTokenResolver {
    pub fn with_credentials(mut self, credentials: Arc<dyn ProviderCredentialRepoPort>) -> Self {
        self.credentials = Some(credentials);
        self
    }
}

#[async_trait::async_trait]
impl BotRuntimeTokenResolverPort for BcsHttpAuthBotRuntimeTokenResolver {
    async fn resolve_agentpass_agent_code(&self, token: &str) -> Option<String> {
        let _ = token;
        None
    }

    async fn try_provider_admin(&self, token: &str) -> Option<String> {
        let credentials = self.credentials.as_ref()?;
        let credential = credentials
            .get_credential_by_secret("provider_admin", token)
            .await
            .ok()??;
        if credential.disabled {
            return None;
        }
        Some(credential.provider_id)
    }
}

#[derive(Debug, Default)]
pub struct NoopChatRunCleanupPort;

#[async_trait::async_trait]
impl ChatRunCleanupPort for NoopChatRunCleanupPort {
    async fn unregister(&self, _run_id: &str) {}
}

#[derive(Debug, Default)]
pub struct NoopChatRunEventPort;

#[async_trait::async_trait]
impl ChatRunEventPort for NoopChatRunEventPort {
    async fn register(
        &self,
        _run_id: String,
        _session_key: String,
        _sender: tokio::sync::mpsc::Sender<String>,
        _source: Option<String>,
        _from: Option<String>,
    ) {
    }

    async fn unregister(&self, _run_id: &str) {}
}

#[async_trait::async_trait]
pub trait BotRequestPort: Send + Sync {
    async fn send_request(
        &self,
        bot_uuid: &str,
        method: &str,
        params: serde_json::Value,
        timeout_ms: u64,
    ) -> Result<serde_json::Value, String>;
}

#[derive(Debug, Default)]
pub struct NoopBotRequestPort;

#[async_trait::async_trait]
impl BotRequestPort for NoopBotRequestPort {
    async fn send_request(
        &self,
        _bot_uuid: &str,
        _method: &str,
        _params: serde_json::Value,
        _timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        Err("bot_request_not_configured".to_string())
    }
}

#[derive(Debug, Clone)]
pub struct VisibilitySyncRequest {
    pub bot_uuid: String,
    pub capabilities: BotCapabilities,
    pub visibility: String,
    pub actor_kind: bcs_domain::ActorKind,
}

#[async_trait::async_trait]
pub trait VisibilitySyncPort: Send + Sync {
    async fn sync_visibility(&self, request: VisibilitySyncRequest);
}

#[derive(Debug, Default)]
pub struct NoopVisibilitySyncPort;

#[async_trait::async_trait]
impl VisibilitySyncPort for NoopVisibilitySyncPort {
    async fn sync_visibility(&self, _request: VisibilitySyncRequest) {}
}

/// Process-local best-effort association for organization-admin invocations.
/// It intentionally has no persistence or retry semantics; a process restart
/// makes old runs unavailable to this management surface.
#[derive(Debug, Clone)]
pub struct AdminInvocationRun {
    pub provider_id: String,
    pub organization_code: String,
    pub target_bot_uuid: String,
    pub session_id: String,
    pub detach: bool,
    pub expires_at_ms: u64,
    pub delivery_error: Option<String>,
    pub callback: Option<AdminInvocationCallback>,
    pub callback_claimed: bool,
}

#[derive(Debug, Clone)]
pub struct AdminInvocationCallback {
    pub url: String,
    pub bearer_token: String,
}

#[derive(Debug, Default)]
pub struct AdminInvocationStore {
    runs: std::sync::Mutex<HashMap<String, AdminInvocationRun>>,
}

impl AdminInvocationStore {
    pub fn insert(&self, run_id: String, run: AdminInvocationRun) {
        let mut runs = self
            .runs
            .lock()
            .expect("admin invocation store lock poisoned");
        purge_expired(&mut runs);
        runs.insert(run_id, run);
    }

    pub fn get_for_provider(&self, run_id: &str, provider_id: &str) -> Option<AdminInvocationRun> {
        let mut runs = self
            .runs
            .lock()
            .expect("admin invocation store lock poisoned");
        purge_expired(&mut runs);
        runs.get(run_id)
            .filter(|run| run.provider_id == provider_id)
            .cloned()
    }

    pub fn get(&self, run_id: &str) -> Option<AdminInvocationRun> {
        let mut runs = self
            .runs
            .lock()
            .expect("admin invocation store lock poisoned");
        purge_expired(&mut runs);
        runs.get(run_id).cloned()
    }

    pub fn claim_callback(&self, run_id: &str) -> Option<AdminInvocationRun> {
        let mut runs = self
            .runs
            .lock()
            .expect("admin invocation store lock poisoned");
        purge_expired(&mut runs);
        let run = runs.get_mut(run_id)?;
        if run.detach || run.callback.is_none() || run.callback_claimed {
            return None;
        }
        run.callback_claimed = true;
        Some(run.clone())
    }

    pub fn claim_callback_for_bot(
        &self,
        run_id: &str,
        target_bot_uuid: &str,
    ) -> Option<AdminInvocationRun> {
        let mut runs = self
            .runs
            .lock()
            .expect("admin invocation store lock poisoned");
        purge_expired(&mut runs);
        let run = runs.get_mut(run_id)?;
        if run.target_bot_uuid != target_bot_uuid
            || run.detach
            || run.callback.is_none()
            || run.callback_claimed
        {
            return None;
        }
        run.callback_claimed = true;
        Some(run.clone())
    }

    pub fn set_delivery_error(&self, run_id: &str, error: String) {
        let mut runs = self
            .runs
            .lock()
            .expect("admin invocation store lock poisoned");
        if let Some(run) = runs.get_mut(run_id) {
            run.delivery_error = Some(error);
        }
    }
}

fn purge_expired(runs: &mut HashMap<String, AdminInvocationRun>) {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
    runs.retain(|_, run| run.expires_at_ms > now);
}

#[derive(Clone)]
pub struct HttpAppState {
    pub services: Services,
    pub health: Arc<dyn HealthPort>,
    pub async_chat_poll_wait_max_ms: u64,
    pub botchat_url: Option<String>,
    pub register_path: String,
    pub chat_run_cleanup: Arc<dyn ChatRunCleanupPort>,
    pub chat_run_events: Arc<dyn ChatRunEventPort>,
    pub bot_request: Arc<dyn BotRequestPort>,
    pub user_identity: Arc<dyn UserIdentityPort>,
    pub bot_runtime_token_resolver: Arc<dyn BotRuntimeTokenResolverPort>,
    pub visibility_sync: Arc<dyn VisibilitySyncPort>,
    pub strict_container_validation: bool,
    pub onboard_binding_enabled: bool,
    pub default_visibility: Option<String>,
    pub bcs_endpoint: Option<String>,
    pub bind: String,
    pub port: u16,
    pub max_groups_as_driver: usize,
    pub max_group_members: usize,
    pub max_groups_as_member: usize,
    pub store_messages: bool,
    pub max_group_messages: u64,
    pub group_chat_delay_min_ms: u64,
    pub group_chat_delay_max_ms: u64,
    pub async_chat_run_timeout_ms: u64,
    pub service_api_keys: Arc<ApiKeyRegistry>,
    pub invite_token_secret: Vec<u8>,
    pub invite_default_ttl_seconds: u64,
    pub invite_base_url: Option<String>,
    pub invite_group_link_url: Option<String>,
    pub invite_session_link_url: Option<String>,
    pub manifest_env: String,
    pub manifest: ManifestConfig,
    pub allowed_switch_provider_ids: Arc<Vec<String>>,
    pub provider_stream_gray_list: Arc<ProviderStreamGrayList>,
    pub judge_enabled: bool,
    pub channel_http_ingress: Option<Arc<ChannelHttpIngressRegistry>>,
    pub auth_chain: Option<Arc<bcs_auth_api::AuthPluginChain>>,
    pub auth_config: bcs_auth_api::AuthConfig,
    pub outbound_url_guard: OutboundUrlGuard,
    pub admin_invocation_runs: Arc<AdminInvocationStore>,
}

impl HttpAppState {
    pub fn new(services: Services) -> Self {
        Self {
            services,
            health: Arc::new(DefaultHealthPort),
            async_chat_poll_wait_max_ms: 30_000,
            botchat_url: None,
            register_path: "/bcn/register".to_string(),
            chat_run_cleanup: Arc::new(NoopChatRunCleanupPort),
            chat_run_events: Arc::new(NoopChatRunEventPort),
            bot_request: Arc::new(NoopBotRequestPort),
            user_identity: Arc::new(ChainUserIdentityPort::new(Arc::new(
                bcs_auth_api::AuthPluginChain::new(vec![]),
            ))),
            bot_runtime_token_resolver: Arc::new(BcsHttpAuthBotRuntimeTokenResolver::default()),
            visibility_sync: Arc::new(NoopVisibilitySyncPort),
            strict_container_validation: false,
            onboard_binding_enabled: false,
            default_visibility: None,
            bcs_endpoint: None,
            bind: "127.0.0.1".to_string(),
            port: 21000,
            max_groups_as_driver: 3,
            max_group_members: 5,
            max_groups_as_member: 10,
            store_messages: true,
            max_group_messages: 100,
            group_chat_delay_min_ms: 0,
            group_chat_delay_max_ms: 0,
            async_chat_run_timeout_ms: 300_000,
            service_api_keys: Arc::new(ApiKeyRegistry::new(Vec::new())),
            invite_token_secret: Vec::new(),
            invite_default_ttl_seconds: 86400,
            invite_base_url: None,
            invite_group_link_url: None,
            invite_session_link_url: None,
            manifest_env: "local".to_string(),
            manifest: ManifestConfig::default(),
            allowed_switch_provider_ids: Arc::new(Vec::new()),
            provider_stream_gray_list: Arc::new(ProviderStreamGrayList::default()),
            judge_enabled: false,
            channel_http_ingress: None,
            auth_chain: None,
            auth_config: bcs_auth_api::AuthConfig::default(),
            outbound_url_guard: OutboundUrlGuard::strict(),
            admin_invocation_runs: Arc::new(AdminInvocationStore::default()),
        }
    }

    pub fn with_async_chat_poll_wait_max_ms(mut self, value: u64) -> Self {
        self.async_chat_poll_wait_max_ms = value;
        self
    }

    pub fn with_health(mut self, health: Arc<dyn HealthPort>) -> Self {
        self.health = health;
        self
    }

    pub fn with_onboard_url_config(
        mut self,
        botchat_url: Option<String>,
        register_path: String,
    ) -> Self {
        self.botchat_url = botchat_url;
        self.register_path = register_path;
        self
    }

    pub fn with_chat_run_cleanup(mut self, cleanup: Arc<dyn ChatRunCleanupPort>) -> Self {
        self.chat_run_cleanup = cleanup;
        self
    }

    pub fn with_chat_run_events(mut self, chat_run_events: Arc<dyn ChatRunEventPort>) -> Self {
        self.chat_run_events = chat_run_events;
        self
    }

    pub fn with_bot_request(mut self, bot_request: Arc<dyn BotRequestPort>) -> Self {
        self.bot_request = bot_request;
        self
    }

    pub fn with_user_identity(mut self, user_identity: Arc<dyn UserIdentityPort>) -> Self {
        self.user_identity = user_identity;
        self
    }

    pub fn with_bot_runtime_token_resolver(
        mut self,
        resolver: Arc<dyn BotRuntimeTokenResolverPort>,
    ) -> Self {
        self.bot_runtime_token_resolver = resolver;
        self
    }

    pub fn with_visibility_sync(mut self, visibility_sync: Arc<dyn VisibilitySyncPort>) -> Self {
        self.visibility_sync = visibility_sync;
        self
    }

    pub fn dispatch_visibility_sync(&self, request: VisibilitySyncRequest) {
        let visibility_sync = self.visibility_sync.clone();
        let _sync_task = tokio::spawn(async move {
            visibility_sync.sync_visibility(request).await;
        });
    }

    pub fn with_group_request_config(
        mut self,
        bcs_endpoint: Option<String>,
        bind: String,
        port: u16,
        max_groups_as_driver: usize,
        max_group_members: usize,
        max_groups_as_member: usize,
    ) -> Self {
        self.bcs_endpoint = bcs_endpoint;
        self.bind = bind;
        self.port = port;
        self.max_groups_as_driver = max_groups_as_driver;
        self.max_group_members = max_group_members;
        self.max_groups_as_member = max_groups_as_member;
        self
    }

    pub fn with_message_config(
        mut self,
        store_messages: bool,
        max_group_messages: u64,
        group_chat_delay_min_ms: u64,
        group_chat_delay_max_ms: u64,
        async_chat_run_timeout_ms: u64,
    ) -> Self {
        self.store_messages = store_messages;
        self.max_group_messages = max_group_messages;
        self.group_chat_delay_min_ms = group_chat_delay_min_ms;
        self.group_chat_delay_max_ms = group_chat_delay_max_ms;
        self.async_chat_run_timeout_ms = async_chat_run_timeout_ms;
        self
    }

    pub fn with_strict_container_validation(mut self, value: bool) -> Self {
        self.strict_container_validation = value;
        self
    }

    pub fn with_onboard_policy(
        mut self,
        onboard_binding_enabled: bool,
        default_visibility: Option<String>,
    ) -> Self {
        self.onboard_binding_enabled = onboard_binding_enabled;
        self.default_visibility = default_visibility;
        self
    }

    pub fn with_service_api_keys(mut self, service_api_keys: Arc<ApiKeyRegistry>) -> Self {
        self.service_api_keys = service_api_keys;
        self
    }

    pub fn with_invite_config(
        mut self,
        token_secret: Vec<u8>,
        default_ttl_seconds: u64,
        base_url: Option<String>,
        group_link_url: Option<String>,
        session_link_url: Option<String>,
    ) -> Self {
        self.invite_token_secret = token_secret;
        self.invite_default_ttl_seconds = default_ttl_seconds;
        self.invite_base_url = base_url;
        self.invite_group_link_url = group_link_url;
        self.invite_session_link_url = session_link_url;
        self
    }

    pub fn with_manifest_config(mut self, env: String, manifest: ManifestConfig) -> Self {
        self.manifest_env = env;
        self.manifest = manifest;
        self
    }

    pub fn with_allowed_switch_provider_ids(mut self, ids: Vec<String>) -> Self {
        self.allowed_switch_provider_ids = Arc::new(ids);
        self
    }

    pub fn with_judge_enabled(mut self, value: bool) -> Self {
        self.judge_enabled = value;
        self
    }

    pub fn with_provider_stream_gray_list(
        mut self,
        gray_list: Arc<ProviderStreamGrayList>,
    ) -> Self {
        self.provider_stream_gray_list = gray_list;
        self
    }

    pub fn with_channel_http_ingress(
        mut self,
        ingress: Option<Arc<ChannelHttpIngressRegistry>>,
    ) -> Self {
        self.channel_http_ingress = ingress;
        self
    }

    pub fn with_auth_chain(
        mut self,
        chain: Arc<bcs_auth_api::AuthPluginChain>,
        config: bcs_auth_api::AuthConfig,
    ) -> Self {
        self.auth_chain = Some(chain);
        self.auth_config = config;
        self
    }

    pub fn with_outbound_url_guard(mut self, outbound_url_guard: OutboundUrlGuard) -> Self {
        self.outbound_url_guard = outbound_url_guard;
        self
    }

    pub fn with_admin_invocation_runs(mut self, runs: Arc<AdminInvocationStore>) -> Self {
        self.admin_invocation_runs = runs;
        self
    }

    /// Resolve the calling bot's `bot_uuid` from request headers.
    ///
    /// Primary path: run the injected auth plugin chain and return
    /// `principal.bot_uuid` (set by the agentpass/session plugins; a human
    /// cookie principal has no `bot_uuid`, so it cannot masquerade as a bot).
    ///
    /// Fallback: when no chain is configured (e.g. `HttpAppState::new` in
    /// contract tests) or the chain has no bot-resolving plugin (debug builds
    /// default to `["local"]`), resolve a non-JWT session token directly via the
    /// registry — mirroring the legacy `SessionTokenPlugin` non-JWT branch. JWT
    /// tokens without a chain are not resolvable (production always has a chain).
    pub async fn bot_uuid_from_headers(&self, headers: &axum::http::HeaderMap) -> Option<String> {
        if let Some(chain) = self.auth_chain.as_ref() {
            if let Ok(result) = chain.authenticate(headers).await {
                if let Some(bot_uuid) = result.principal.and_then(|p| p.bot_uuid) {
                    return Some(bot_uuid);
                }
            }
        }

        let token = bot_token_from_headers(headers)?;
        if bcs_auth_api::is_jwt_format(&token) {
            return None;
        }
        self.services.registry.find_bot_by_token(&token).await
    }
}

/// Extract a bot session token from headers: `X-BCS-Bot-Token` wins, else a
/// `Bearer` token. The `Bearer` extraction is shared via
/// [`crate::headers::extract_bearer_token`]; kept private to `state` only to
/// preserve the `X-BCS-Bot-Token` precedence and mirror
/// `routes::caller::bot_token_from_headers`.
fn bot_token_from_headers(headers: &axum::http::HeaderMap) -> Option<String> {
    if let Some(token) = headers
        .get("X-BCS-Bot-Token")
        .and_then(|value| value.to_str().ok())
        .filter(|token| !token.is_empty())
    {
        return Some(token.to_string());
    }

    crate::headers::extract_bearer_token(headers)
}

impl std::fmt::Debug for HttpAppState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("HttpAppState")
            .field("services", &"<Services>")
            .field("health", &"<HealthPort>")
            .field(
                "async_chat_poll_wait_max_ms",
                &self.async_chat_poll_wait_max_ms,
            )
            .field("botchat_url", &self.botchat_url)
            .field("register_path", &self.register_path)
            .field("chat_run_cleanup", &"<ChatRunCleanupPort>")
            .field("chat_run_events", &"<ChatRunEventPort>")
            .field("bot_request", &"<BotRequestPort>")
            .field("user_identity", &"<UserIdentityPort>")
            .field(
                "bot_runtime_token_resolver",
                &"<BotRuntimeTokenResolverPort>",
            )
            .field("visibility_sync", &"<VisibilitySyncPort>")
            .field(
                "strict_container_validation",
                &self.strict_container_validation,
            )
            .field("onboard_binding_enabled", &self.onboard_binding_enabled)
            .field("default_visibility", &self.default_visibility)
            .field("bcs_endpoint", &self.bcs_endpoint)
            .field("bind", &self.bind)
            .field("port", &self.port)
            .field("max_groups_as_driver", &self.max_groups_as_driver)
            .field("max_group_members", &self.max_group_members)
            .field("max_groups_as_member", &self.max_groups_as_member)
            .field("store_messages", &self.store_messages)
            .field("max_group_messages", &self.max_group_messages)
            .field("group_chat_delay_min_ms", &self.group_chat_delay_min_ms)
            .field("group_chat_delay_max_ms", &self.group_chat_delay_max_ms)
            .field("async_chat_run_timeout_ms", &self.async_chat_run_timeout_ms)
            .field("invite_token_secret", &"<redacted>")
            .field(
                "invite_default_ttl_seconds",
                &self.invite_default_ttl_seconds,
            )
            .field("invite_base_url", &self.invite_base_url)
            .field("invite_group_link_url", &self.invite_group_link_url)
            .field("invite_session_link_url", &self.invite_session_link_url)
            .field("manifest_env", &self.manifest_env)
            .field("manifest", &self.manifest)
            .field(
                "allowed_switch_provider_ids",
                &self.allowed_switch_provider_ids,
            )
            .field(
                "auth_chain",
                &self.auth_chain.as_ref().map(|_| "<AuthPluginChain>"),
            )
            .field("auth_config", &self.auth_config)
            .field("outbound_url_guard", &self.outbound_url_guard)
            .finish()
    }
}
