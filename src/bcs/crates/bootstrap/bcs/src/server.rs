//! HTTP server for the Bot Coordination Service.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::{Arc, OnceLock};
use std::time::Instant;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use axum::extract::MatchedPath;
use axum::extract::ws::WebSocketUpgrade as WsUpgrade;
use axum::{
    Router,
    body::Body,
    extract::{State, WebSocketUpgrade},
    http::{Request, StatusCode, header::CONTENT_TYPE},
    middleware::{self, Next},
    response::{IntoResponse, Json, Response},
    routing::get,
};
use tokio::sync::Mutex;
use tower_http::catch_panic::CatchPanicLayer;
use tower_http::cors::{AllowHeaders, AllowMethods, AllowOrigin, CorsLayer};
use tower_http::trace::TraceLayer;
use tracing::{debug, info, warn};

use crate::auth_wiring::AuthPluginFactory;
use crate::Result;
use crate::config::{BcsConfig, CollaborationTemplateStorageKind, LlmConfig, LlmProviderType};
use crate::lifecycle::LifecycleOrchestrator;
use crate::plugins::{
    DbPluginKind, InfrastructurePlugins, LeaderElectionRegistration,
    build_registered_channel_provider,
    build_registered_leader_election, build_registered_llm_provider,
    build_registered_security_gateway, build_registered_user_directory,
};
use bcs_bot::{Bot, BotCore, ProviderBotEvents, ProviderCore, ProviderManagement};
use bcs_bot_store::{DbProviderStore, PersistentBotRepo, MemoryBotRepo, MemoryProviderStore};
use bcs_channel::{BcsChannelService, ChannelServiceInboundSink};
use bcs_channel_api::{ChannelHttpIngressRegistry, ChannelProvider, ChannelProviderRegistry};
use bcs_channel_store::{
    DbChannelBindingStore, DbConversationSessionStore, DbImParticipantStore,
    MemoryChannelBindingRepo, MemoryConversationSessionRepo, MemoryImParticipantRepo,
};
use bcs_friend::{FriendCore, FriendRequestCore};
use bcs_friend_store::{
    DbFriendRequestStore, DbFriendStore, MemoryFriendRepo, MemoryFriendRequestRepo,
};
use bcs_fuse_client::FuseClient;
use bcs_fusion::{FuseClientService, FuseWorkerProfileService, LocalFusionService};
use bcs_group::{GroupConfig, GroupCore, GroupManagement};
use bcs_group_store::{MemoryGroupRepo, MySqlGroupStore};
use bcs_http::{
    admin_invocation_terminal::AdminInvocationTerminalObserver,
    state::AdminInvocationStore,
};
use bcs_judge::{LlmJudgeService, NoopJudgeEvaluator};
use bcs_leader_election::StandaloneLeaderElection;
use bcs_llm_api::LlmChatCompletionPort;
use bcs_llm_openai_compatible::OpenAiCompatibleLlmClient;
use bcs_message::MessageService;
use bcs_message_flow::{
    A2aChat, BcsGroupFusion, BcsGroupMessageHistory, BcsMessageFlow,
};
use bcs_message_store::{MemoryMessageRepo, MySqlMessageStore};
use bcs_organization::{OrganizationCore, OrganizationManagement};
use bcs_organization_store::{DbOrganizationStore, MemoryOrganizationRepo};
use bcs_collaboration_runtime::CollaborationRuntime;
use bcs_collaboration_store::{
    DbCollaborationTemplateRepo, MemoryCollaborationStore, MySqlCollaborationStore,
};
use bcs_collaboration_template::{
    CollaborationTemplateServiceImpl, FileCollaborationTemplateRepo,
};
use bcs_db_api::DbSqlFlavor;
use bcs_proposal::{GroupProposalUseCases, GroupProposalUseCasesConfig, ProposalStore};
use bcs_relation::RelationCore;
use bcs_relation_store::DbRelationStore;
use bcs_routing::MessageRouter;
use bcs_routing::security::SecurityInterceptor;
use bcs_security_gateway_local::NoopSecurityGateway;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::interceptor::InterceptorChain;
use bcs_security_gateway_api::SecurityGatewayPort;
use bcs_service_api::lifecycle::ServiceLifecycle;
use bcs_service_api::{
    A2aChatRunService, A2aChatService, BotDeliveryPort, BotDeliveryTarget, BotRegistryCoreService,
    BotMetricsSnapshotPort, BotRunContextPort, BotTerminalObserverPort, ChannelService,
    CollaborationTemplateService,
    DirectChatClientKind, DirectChatRunEvent, DirectChatRunLifecycleHook,
    DirectChatRunReason, DirectChatRunSnapshotPort, FrontendDeliveryPort, GroupCoreService,
    GroupHistoryBotRequestPort, GroupManagementService, GroupMessageHistoryService,
    GroupMetricsSnapshotPort, GroupRepoPort, GroupSessionMetricsSnapshotPort,
    JudgeEvaluatorPort, LeaderElectionPort, MessageFlowService, MetricsResult,
    OrganizationCoreService, OrganizationManagementService, OrganizationRepoPort,
    ProviderBotBindingRepoPort, ProviderBotCoreService, ProviderBotEventService, ProviderCoreService,
    ProviderCredentialRepoPort, ProviderManagementService, ProviderRepoPort, ProviderStreamGrayList,
    RoutingCoreService, SessionManagementService, WsCloseReason, WsErrorKind,
    WsLifecycleInstrumentationHook, WsPeer,
    port::repo::{
        ChannelBindingRepoPort, ConversationSessionRepoPort, ImParticipantRepoPort,
        MessageRepoPort, SessionRepoPort,
    },
};
use bcs_services_container::{Services, ServicesBuilder};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::{MemorySessionRepo, MySqlSessionStore};
use bcs_system_message::{
    SystemMessageDispatcherImpl, SystemMessageServiceImpl,
    producers::bot_hidden_notice::BotHiddenNoticeProducer,
    producers::bot_joined::BotJoinedMessageProducer,
    producers::bot_left::BotLeftMessageProducer,
    producers::generic::GenericNotificationMessageProducer,
    producers::human_joined::HumanJoinedMessageProducer,
    producers::participant_mode_changed::ParticipantModeChangedMessageProducer,
    producers::session_context::SessionContextMessageProducer,
};
use bcs_user_directory_api::UserDirectoryPlugin;
use bcs_ws::bot::BotConnectionRegistry;
use bcs_ws::shared::RunChannelManager;
use bcs_ws::web::{WorkbenchConnectionRegistry, WorkbenchFrontendDelivery};
use secrecy::{ExposeSecret, Secret};

/// Check if debug mode is enabled via BCS_DEBUG env var
fn is_debug_enabled() -> bool {
    std::env::var("BCS_DEBUG").is_ok_and(|v| v == "true")
}

/// Build a default `SecretService` for the `ServicesBuilder` step.
///
/// At builder time we don't yet know if mist is enabled — wiring there happens
/// in `http_adapter::build_http_app_state`, which is async. We seed every
/// `Services` instance with a Noop so the builder's required-field invariant
/// is satisfied; the real backend (Mist when enabled) is swapped in alongside
/// `HttpAppState` construction.
fn default_bootstrap_secret_service() -> Arc<dyn bcs_service_api::SecretService> {
    use bcs_secret::DefaultSecretService;
    use bcs_secret_local::NoopSecretAccess;
    Arc::new(DefaultSecretService::new(Arc::new(NoopSecretAccess)))
}

fn build_file_collaboration_template_service_with_judge_templates(
    config: &BcsConfig,
    judge_templates_enabled: bool,
) -> Arc<dyn CollaborationTemplateService> {
    let repo = Arc::new(FileCollaborationTemplateRepo::new(
        config.collaboration.templates.base_dir.clone(),
    ));
    Arc::new(
        CollaborationTemplateServiceImpl::new(
            repo,
            config.collaboration.templates.default_language.clone(),
        )
        .with_judge_templates_enabled(judge_templates_enabled),
    )
}

type ChannelSlot = Arc<OnceLock<Arc<dyn ChannelService>>>;

type ChannelRepos = (
    Arc<dyn ChannelBindingRepoPort>,
    Arc<dyn ConversationSessionRepoPort>,
    Arc<dyn ImParticipantRepoPort>,
);

struct ChannelRuntime {
    service: Arc<dyn ChannelService>,
    http_ingress: Option<Arc<ChannelHttpIngressRegistry>>,
    lifecycles: Vec<Arc<dyn ServiceLifecycle>>,
}

#[derive(Debug, Default)]
struct DisabledChannelService;

#[async_trait]
impl ChannelService for DisabledChannelService {
    async fn handle_inbound(
        &self,
        _msg: bcs_service_api::application::channel::InboundMessage,
    ) -> std::result::Result<(), bcs_service_api::application::channel::ChannelInboundError> {
        Ok(())
    }

    async fn try_outbound(
        &self,
        _msg: bcs_service_api::application::channel::OutboundMessage,
    ) -> std::result::Result<(), bcs_service_api::application::channel::ChannelUseCaseError> {
        Ok(())
    }

    async fn create_binding(
        &self,
        _cmd: bcs_service_api::application::channel::CreateBindingCommand,
    ) -> std::result::Result<
        bcs_domain::ChannelBinding,
        bcs_service_api::application::channel::ChannelUseCaseError,
    > {
        Err(bcs_service_api::application::channel::ChannelUseCaseError::InvalidParams(
            "channel bridge is disabled".to_string(),
        ))
    }

    async fn list_bindings(
        &self,
    ) -> std::result::Result<
        Vec<bcs_domain::ChannelBinding>,
        bcs_service_api::application::channel::ChannelUseCaseError,
    > {
        Ok(Vec::new())
    }

    async fn set_binding_status(
        &self,
        _id: &str,
        _active: bool,
    ) -> std::result::Result<(), bcs_service_api::application::channel::ChannelUseCaseError> {
        Ok(())
    }

    async fn update_binding_config(
        &self,
        _id: &str,
        _config: serde_json::Value,
    ) -> std::result::Result<(), bcs_service_api::application::channel::ChannelUseCaseError> {
        Ok(())
    }

    async fn delete_binding(
        &self,
        _id: &str,
    ) -> std::result::Result<(), bcs_service_api::application::channel::ChannelUseCaseError> {
        Ok(())
    }
}

fn now_ms() -> u64 {
    match SystemTime::now().duration_since(UNIX_EPOCH) {
        Ok(duration) => duration.as_millis() as u64,
        Err(_) => 0,
    }
}

fn channel_bridge_enabled(config: &BcsConfig) -> bool {
    config.channels.enabled
}

fn memory_channel_repos(data_dir: Option<PathBuf>) -> ChannelRepos {
    match data_dir {
        Some(dir) => (
            Arc::new(MemoryChannelBindingRepo::with_data_dir(dir.clone())),
            Arc::new(MemoryConversationSessionRepo::with_data_dir(dir.clone())),
            Arc::new(MemoryImParticipantRepo::with_data_dir(dir)),
        ),
        None => (
            Arc::new(MemoryChannelBindingRepo::new()),
            Arc::new(MemoryConversationSessionRepo::new()),
            Arc::new(MemoryImParticipantRepo::new()),
        ),
    }
}

async fn channel_repos_with_storage(
    infrastructure_plugins: &InfrastructurePlugins,
) -> crate::Result<ChannelRepos> {
    let db_plugin = infrastructure_plugins.db().ok_or_else(|| {
        crate::BcsError::StorageInitError(
            "channel storage: DbPlugin handle unavailable".to_string(),
        )
    })?;
    match infrastructure_plugins.db_kind() {
        DbPluginKind::LocalSqlite => {
            info!("Initializing SQLite channel storage");
            Ok((
                Arc::new(DbChannelBindingStore::sqlite(db_plugin.clone())),
                Arc::new(DbConversationSessionStore::sqlite(db_plugin.clone())),
                Arc::new(DbImParticipantStore::sqlite(db_plugin)),
            ))
        }
        DbPluginKind::Mysql => {
            info!("Initializing MySQL channel storage");
            Ok((
                Arc::new(DbChannelBindingStore::mysql(db_plugin.clone())),
                Arc::new(DbConversationSessionStore::mysql(db_plugin.clone())),
                Arc::new(DbImParticipantStore::mysql(db_plugin)),
            ))
        }
        DbPluginKind::External(provider) => {
            Err(crate::BcsError::StorageInitError(format!(
                "external database plugin '{provider}' has no channel storage wiring"
            )))
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn build_channel_runtime(
    config: &BcsConfig,
    channel_slot: ChannelSlot,
    channel_repos: ChannelRepos,
    session_repo: Arc<dyn SessionRepoPort>,
    message_flow: Arc<dyn MessageFlowService>,
    system_message: Arc<dyn bcs_service_api::SystemMessageService>,
    collaboration_runtime: Arc<dyn bcs_service_api::CollaborationRuntimeService>,
    group: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
) -> Result<ChannelRuntime> {
    if !channel_bridge_enabled(config) {
        info!("channel bridge disabled");
        return Ok(ChannelRuntime {
            service: Arc::new(DisabledChannelService),
            http_ingress: None,
            lifecycles: Vec::new(),
        });
    }

    let (channel_bindings, channel_conversations, channel_im_participants) = channel_repos;
    let providers = build_configured_channel_providers(
        config,
        channel_bindings.clone(),
    )?;
    let provider_registry = Arc::new(
        ChannelProviderRegistry::new(providers.clone())
            .map_err(|error| crate::BcsError::InvalidConfig(error.to_string()))?,
    );
    let channel_service: Arc<dyn ChannelService> = Arc::new(BcsChannelService::new(
        channel_bindings,
        channel_conversations,
        channel_im_participants,
        session_repo,
        message_flow,
        system_message,
        collaboration_runtime,
        group,
        registry,
        provider_registry,
        bcs_config::resolve_env_str(),
        Arc::new(now_ms),
        Arc::new(|| uuid::Uuid::new_v4().to_string()),
    ));
    if channel_slot.set(channel_service.clone()).is_err() {
        warn!("message-flow channel slot already initialized");
    }
    let sink: Arc<dyn bcs_channel_api::ChannelInboundSink> =
        Arc::new(ChannelServiceInboundSink::new(channel_service.clone()));
    let ingress = Arc::new(
        ChannelHttpIngressRegistry::new(providers.clone(), sink.clone())
            .map_err(|error| crate::BcsError::InvalidConfig(error.to_string()))?,
    );
    let http_ingress = if ingress.route_specs().is_empty() {
        None
    } else {
        Some(ingress)
    };
    let mut lifecycles = Vec::new();
    for provider in providers {
        if let Some(lifecycle) = provider.stream_lifecycle(sink.clone()) {
            lifecycles.push(lifecycle);
        }
    }

    Ok(ChannelRuntime {
        service: channel_service,
        http_ingress,
        lifecycles,
    })
}

fn build_configured_channel_providers(
    config: &BcsConfig,
    channel_bindings: Arc<dyn ChannelBindingRepoPort>,
) -> Result<Vec<Arc<dyn ChannelProvider>>> {
    let mut providers = Vec::new();
    for (provider_name, provider_config) in config.channels.enabled_provider_configs() {
        match build_registered_channel_provider(
            config,
            &provider_name,
            provider_config,
            channel_bindings.clone(),
            Arc::new(now_ms),
        )? {
            Some(provider) => providers.push(provider),
            None => {
                return Err(crate::BcsError::InvalidConfig(format!(
                    "channel provider '{provider_name}' is configured but not available in this binary"
                )));
            }
        }
    }
    Ok(providers)
}

fn build_file_collaboration_template_service(
    config: &BcsConfig,
) -> Arc<dyn CollaborationTemplateService> {
    build_file_collaboration_template_service_with_judge_templates(config, config.llm.is_enabled())
}

fn build_standalone_collaboration_template_service(
    config: &BcsConfig,
) -> Arc<dyn CollaborationTemplateService> {
    match config.collaboration.templates.storage_type {
        CollaborationTemplateStorageKind::File => build_file_collaboration_template_service(config),
        CollaborationTemplateStorageKind::Mysql => {
            panic!(
                "standalone BCS server cannot use mysql collaboration template storage; \
                 use BcsServer::new_with_storage"
            )
        }
    }
}

fn build_collaboration_template_service_with_storage(
    config: &BcsConfig,
    infrastructure_plugins: &InfrastructurePlugins,
    judge_templates_enabled: bool,
) -> Result<Arc<dyn CollaborationTemplateService>> {
    match config.collaboration.templates.storage_type {
        CollaborationTemplateStorageKind::File => {
            info!("Using file-backed collaboration template catalog");
            Ok(build_file_collaboration_template_service_with_judge_templates(
                config,
                judge_templates_enabled,
            ))
        }
        CollaborationTemplateStorageKind::Mysql => {
            let db_plugin = infrastructure_plugins.db().ok_or_else(|| {
                crate::BcsError::StorageInitError(
                    "collaboration template storage is 'mysql' but DbPlugin handle is unavailable"
                        .to_string(),
                )
            })?;
            let env = crate::env::resolve_env();
            info!(
                env = %env,
                db_plugin = %infrastructure_plugins.db_kind(),
                "Using DB-backed collaboration template catalog"
            );
            let repo = Arc::new(DbCollaborationTemplateRepo::new(db_plugin, env));
            Ok(Arc::new(
                CollaborationTemplateServiceImpl::new(
                    repo,
                    config.collaboration.templates.default_language.clone(),
                )
                .with_judge_templates_enabled(judge_templates_enabled),
            ))
        }
    }
}

/// Debug middleware to log incoming HTTP requests
async fn debug_middleware(req: Request<Body>, next: Next) -> Response {
    static DEBUG: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    let debug = *DEBUG.get_or_init(is_debug_enabled);

    if debug {
        let method = req.method();
        let uri = req.uri();
        let path = uri.path();

        // BCS_DEBUG is also the E2E endpoint-coverage signal, so health must
        // be logged together with every other registered HTTP route.
        eprintln!("\x1b[2m[→BCS] {} {}\x1b[0m", method, path);
    }

    next.run(req).await
}

async fn metrics_handler(State(state): State<Arc<BcsServerState>>) -> Response {
    let Some(metrics) = state.metrics.clone() else {
        return StatusCode::NOT_FOUND.into_response();
    };
    metrics.refresh_on_scrape(&state).await;
    (
        [(CONTENT_TYPE, "text/plain; version=0.0.4; charset=utf-8")],
        metrics.render(),
    )
        .into_response()
}

async fn http_metrics_middleware(
    State(state): State<Arc<BcsServerState>>,
    req: Request<Body>,
    next: Next,
) -> Response {
    let Some(metrics) = state.metrics.clone() else {
        return next.run(req).await;
    };
    if req.uri().path() == metrics.endpoint_path {
        return next.run(req).await;
    }

    let method = req.method().as_str().to_string();
    let route = req
        .extensions()
        .get::<MatchedPath>()
        .map(|matched| matched.as_str().to_string())
        .unwrap_or_else(|| "unmatched".to_string());
    let start = Instant::now();
    let response = next.run(req).await;
    let status = response.status();
    metrics.record_http_request(&route, &method, status, start.elapsed());
    response
}

/// BCS server state.
pub struct BcsServerState {
    /// Configuration.
    pub config: BcsConfig,

    /// Services bundle.
    pub services: Services,

    /// Run channel manager for routing events back to clients (legacy, fallback).
    pub run_channels: Arc<RunChannelManager>,

    /// Bot socket sender registry owned by the WebSocket adapter.
    pub bot_connections: Arc<BotConnectionRegistry>,

    /// Workbench frontend sender registry owned by the WebSocket adapter.
    pub frontend_connections: Arc<WorkbenchConnectionRegistry>,

    /// Run-channel registry owned by the WebSocket adapter.
    pub frontend_run_channels: Arc<RunChannelManager>,

    /// Coordination echo deduplication store shared by bot WebSocket reconnects.
    pub coordination_processed: Arc<Mutex<std::collections::HashMap<String, u64>>>,

    /// Leader election port used by health and lifecycle.
    pub leader_election: Arc<dyn LeaderElectionPort>,

    /// Production lifecycle orchestrator for services with explicit startup/shutdown.
    pub lifecycle: Arc<Mutex<LifecycleOrchestrator>>,

    /// bcsfuse HTTP client (present when bcsfuse integration is enabled).
    pub fuse_client: Option<Arc<FuseClient>>,

    /// Provider credential repo used by HTTP auth adapter token resolution.
    pub provider_credentials: Arc<dyn ProviderCredentialRepoPort>,

    /// Runtime gray list controlling provider 2.0 SSE rollout by bot creator.
    pub provider_stream_gray_list: Arc<ProviderStreamGrayList>,

    /// Host-mounted channel provider HTTP ingress routes.
    pub channel_http_ingress: Option<Arc<ChannelHttpIngressRegistry>>,

    /// Snapshot port for low-cardinality group metrics.
    pub group_metrics_snapshot: Arc<dyn GroupMetricsSnapshotPort>,

    /// Snapshot port for low-cardinality group session metrics.
    pub group_session_metrics_snapshot: Arc<dyn GroupSessionMetricsSnapshotPort>,

    /// Snapshot port for low-cardinality bot metrics.
    pub bot_metrics_snapshot: Arc<dyn BotMetricsSnapshotPort>,

    /// Snapshot port for low-cardinality direct chat run metrics.
    pub direct_chat_run_snapshot: Arc<dyn DirectChatRunSnapshotPort>,

    /// Optional Prometheus metrics runtime.
    pub metrics: Option<Arc<crate::metrics::MetricsRuntime>>,

    /// Auth plugin chain (built once at startup; shared by HTTP state and WS upgrade).
    pub auth_chain: Arc<bcs_auth_api::AuthPluginChain>,

    /// Auth chain configuration.
    pub auth_config: bcs_auth_api::AuthConfig,

    /// Shared OAuth identity port (used to build `/auth/*` route state).
    pub user_identity_port: Option<Arc<dyn bcs_auth_api::UserIdentityPort>>,

    /// User-controlled outbound HTTP URL security policy.
    pub outbound_url_guard: OutboundUrlGuard,

    /// Process-local organization-admin invocation callback associations.
    pub admin_invocation_runs: Arc<AdminInvocationStore>,
}

impl std::fmt::Debug for BcsServerState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BcsServerState")
            .field("config", &self.config)
            .field("services", &"<Services>")
            .field("run_channels", &"<RunChannelManager>")
            .field("bot_connections", &"<BotConnectionRegistry>")
            .field("frontend_connections", &"<WorkbenchConnectionRegistry>")
            .field("frontend_run_channels", &"<RunChannelManager>")
            .field("coordination_processed", &"<CoordinationProcessed>")
            .field("leader_election", &"<LeaderElectionPort>")
            .field("lifecycle", &"<LifecycleOrchestrator>")
            .field("provider_credentials", &"<ProviderCredentialRepoPort>")
            .field("provider_stream_gray_list", &"<ProviderStreamGrayList>")
            .field("channel_http_ingress", &self.channel_http_ingress.is_some())
            .field("group_metrics_snapshot", &"<GroupMetricsSnapshotPort>")
            .field("group_session_metrics_snapshot", &"<GroupSessionMetricsSnapshotPort>")
            .field("bot_metrics_snapshot", &"<BotMetricsSnapshotPort>")
            .field("direct_chat_run_snapshot", &"<DirectChatRunSnapshotPort>")
            .field("metrics", &"<MetricsRuntime>")
            .field("auth_chain", &"<AuthPluginChain>")
            .field("auth_config", &self.auth_config)
            .field("outbound_url_guard", &self.outbound_url_guard)
            .finish()
    }
}

/// Optional composition-root extensions supplied by an embedding binary.
///
/// Public startup uses `Default`; internal binaries can inject implementations
/// of the public plugin contracts without adding private SDKs to this crate.
#[derive(Clone, Default)]
pub struct BcsServerExtensions {
    pub auth_plugin_factories: Vec<AuthPluginFactory>,
    pub llm_provider: Option<Arc<dyn LlmChatCompletionPort>>,
    pub user_directory_plugin: Option<Arc<dyn UserDirectoryPlugin>>,
    pub leader_election: Option<LeaderElectionRegistration>,
}

#[derive(Clone)]
struct ProviderRepoBundle {
    provider_repo: Arc<dyn ProviderRepoPort>,
    provider_credentials: Arc<dyn ProviderCredentialRepoPort>,
    provider_bindings: Arc<dyn ProviderBotBindingRepoPort>,
}

fn memory_provider_repos() -> ProviderRepoBundle {
    let store = Arc::new(MemoryProviderStore::new());
    ProviderRepoBundle {
        provider_repo: store.clone(),
        provider_credentials: store.clone(),
        provider_bindings: store,
    }
}

fn db_sql_flavor(db_kind: &DbPluginKind) -> DbSqlFlavor {
    match db_kind {
        DbPluginKind::LocalSqlite => DbSqlFlavor::Sqlite,
        DbPluginKind::Mysql => DbSqlFlavor::Mysql,
        DbPluginKind::External(provider) => {
            panic!("external database plugin '{}' has no SQL flavor wiring", provider)
        }
    }
}

fn db_provider_repos(
    db_plugin: Arc<dyn bcs_db_api::DbPlugin>,
    db_kind: &DbPluginKind,
) -> ProviderRepoBundle {
    let store = match db_kind {
        DbPluginKind::LocalSqlite => Arc::new(DbProviderStore::sqlite(db_plugin)),
        DbPluginKind::Mysql => Arc::new(DbProviderStore::mysql(db_plugin)),
        DbPluginKind::External(provider) => {
            panic!("external database plugin '{}' has no provider store wiring", provider)
        }
    };
    ProviderRepoBundle {
        provider_repo: store.clone(),
        provider_credentials: store.clone(),
        provider_bindings: store,
    }
}

fn memory_organization_services(
    provider_repos: &ProviderRepoBundle,
    provider_core: Arc<dyn ProviderCoreService>,
    bot_registry: Arc<dyn BotRegistryCoreService>,
) -> (
    Arc<dyn OrganizationCoreService>,
    Arc<dyn OrganizationManagementService>,
) {
    let organization_repo: Arc<dyn OrganizationRepoPort> =
        Arc::new(MemoryOrganizationRepo::new());
    build_organization_services(
        organization_repo,
        provider_repos,
        provider_core,
        bot_registry,
    )
}

fn db_organization_services(
    db_plugin: Arc<dyn bcs_db_api::DbPlugin>,
    db_kind: &DbPluginKind,
    provider_repos: &ProviderRepoBundle,
    provider_core: Arc<dyn ProviderCoreService>,
    bot_registry: Arc<dyn BotRegistryCoreService>,
) -> (
    Arc<dyn OrganizationCoreService>,
    Arc<dyn OrganizationManagementService>,
) {
    let organization_repo: Arc<dyn OrganizationRepoPort> = match db_kind {
        DbPluginKind::LocalSqlite => Arc::new(DbOrganizationStore::sqlite(db_plugin.clone())),
        DbPluginKind::Mysql => Arc::new(DbOrganizationStore::mysql(db_plugin.clone())),
        DbPluginKind::External(provider) => {
            panic!("external database plugin '{}' has no organization store wiring", provider)
        }
    };
    build_organization_services(
        organization_repo,
        provider_repos,
        provider_core,
        bot_registry,
    )
}

fn build_organization_services(
    organization_repo: Arc<dyn OrganizationRepoPort>,
    provider_repos: &ProviderRepoBundle,
    provider_core: Arc<dyn ProviderCoreService>,
    bot_registry: Arc<dyn BotRegistryCoreService>,
) -> (
    Arc<dyn OrganizationCoreService>,
    Arc<dyn OrganizationManagementService>,
) {
    let organization_core: Arc<dyn OrganizationCoreService> = Arc::new(OrganizationCore::new(
        crate::env::resolve_env(),
        organization_repo,
        provider_repos.provider_repo.clone(),
        provider_repos.provider_bindings.clone(),
        bot_registry,
    ));
    let organization_management: Arc<dyn OrganizationManagementService> = Arc::new(
        OrganizationManagement::new(provider_core, organization_core.clone()),
    );
    (organization_core, organization_management)
}

fn build_provider_services_with_webhook_url_guard(
    repos: &ProviderRepoBundle,
    registry: Arc<dyn BotRegistryCoreService>,
    relation: Arc<dyn bcs_service_api::RelationCoreService>,
    user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
    webhook_url_guard: OutboundUrlGuard,
) -> (
    Arc<dyn ProviderCoreService>,
    Arc<dyn ProviderBotCoreService>,
    Arc<dyn ProviderManagementService>,
) {
    let provider_core_impl = Arc::new(ProviderCore::new_with_webhook_url_guard(
        repos.provider_repo.clone(),
        repos.provider_credentials.clone(),
        repos.provider_bindings.clone(),
        registry.clone(),
        webhook_url_guard,
    ));
    let provider_core: Arc<dyn ProviderCoreService> = provider_core_impl.clone();
    let provider_bot_core: Arc<dyn ProviderBotCoreService> = provider_core_impl;
    let mut provider_management = ProviderManagement::new(
        provider_core.clone(),
        provider_bot_core.clone(),
        registry,
        relation,
    );
    if let Some(user_directory) = user_directory {
        provider_management = provider_management.with_user_directory(user_directory);
    }
    let provider_management: Arc<dyn ProviderManagementService> = Arc::new(provider_management);
    (provider_core, provider_bot_core, provider_management)
}

fn create_user_directory_plugin(
    config: &BcsConfig,
) -> crate::Result<Option<Arc<dyn UserDirectoryPlugin>>> {
    let directory = &config.user_directory;
    if !directory.enabled {
        return Ok(None);
    }

    let provider = directory
        .provider
        .as_deref()
        .map(str::trim)
        .filter(|provider| !provider.is_empty())
        .ok_or_else(|| {
            crate::BcsError::InvalidConfig(
                "user_directory.provider is required when user_directory.enabled = true"
                    .to_string(),
            )
        })?;

    let provider_config = directory.providers.get(provider).cloned().unwrap_or_default();
    build_registered_user_directory(config, provider, provider_config)?
        .ok_or_else(|| {
            crate::BcsError::InvalidConfig(format!(
                "user_directory provider '{provider}' is not available in this binary"
            ))
        })
        .map(|registration| Some(registration.plugin))
}

fn create_provider_stream_gray_list(config: &BcsConfig) -> Arc<ProviderStreamGrayList> {
    let entries = config.provider_stream_gray_created_by.clone();
    if config.provider_stream_gray_enabled {
        Arc::new(ProviderStreamGrayList::new(entries))
    } else {
        Arc::new(ProviderStreamGrayList::new_disabled(entries))
    }
}

fn outbound_url_guard_from_config(config: &BcsConfig) -> OutboundUrlGuard {
    let policy = &config.security.outbound_url;
    OutboundUrlGuard::new(policy.block_private_networks, policy.allow_loopback)
}

impl Default for BcsServerState {
    fn default() -> Self {
        let config = BcsConfig::default();
        let outbound_url_guard = outbound_url_guard_from_config(&config);
        let admin_invocation_runs = Arc::new(AdminInvocationStore::default());
        let provider_repos = memory_provider_repos();
        let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(config.bots_base_dir.clone()));
        let bot_metrics_snapshot: Arc<dyn BotMetricsSnapshotPort> = bot_repo.clone();
        let bot_core_arc: Arc<BotCore> = Arc::new(BotCore::with_provider_repos(
            bot_repo,
            provider_repos.provider_repo.clone(),
            provider_repos.provider_credentials.clone(),
            provider_repos.provider_bindings.clone(),
        ));
        let bot_registry: Arc<dyn BotRegistryCoreService> = bot_core_arc.clone();
        // F.1/F.2 dual-write wiring: relation store must be created BEFORE
        // friend_store and provider_management so it can be injected into both.
        let relation_store: Arc<RelationCore> = Arc::new(RelationCore::memory());
        let user_directory = create_user_directory_plugin(&config)
            .expect("default user directory config is valid");
        let (provider_core, provider_bot_core, provider_management) =
            build_provider_services_with_webhook_url_guard(
                &provider_repos,
                bot_registry.clone(),
                relation_store.clone() as Arc<dyn bcs_service_api::RelationCoreService>,
                user_directory.clone(),
                outbound_url_guard.clone(),
            );
        let (organization_core, organization_management) = memory_organization_services(
            &provider_repos,
            provider_core.clone(),
            bot_registry.clone(),
        );
        let group_repo = Arc::new(MemoryGroupRepo::new());
        let group_metrics_snapshot: Arc<dyn GroupMetricsSnapshotPort> = group_repo.clone();
        let group_repo_for_session: Arc<dyn GroupRepoPort> = group_repo.clone();
        let sessions = Arc::new(GroupCore::with_repo(group_repo));
        let router = Arc::new(MessageRouter::new());
        let fusion = Arc::new(LocalFusionService::new(config.bots_base_dir.clone()));
        let proposals = Arc::new(ProposalStore::new());
        let friend_repo = Arc::new(MemoryFriendRepo::new());
        let friend_store: Arc<FriendCore> =
            Arc::new(FriendCore::with_repo(friend_repo).with_relation(
                relation_store.clone() as Arc<dyn bcs_service_api::RelationCoreService>
            ));
        let bot_connections = Arc::new(BotConnectionRegistry::new());
        let mut bot_use_cases = Bot::new_with_friend(bot_registry.clone(), friend_store.clone())
            .with_bot_core(bot_core_arc.clone())
            .with_organization(organization_core.clone())
            .with_relation(
                relation_store.clone() as Arc<dyn bcs_service_api::RelationCoreService>
            )
            .with_connection_control(
                bot_connections.clone()
                    as Arc<dyn bcs_service_api::BotConnectionControlPort>,
        );
        if let Some(user_directory) = user_directory.clone() {
            bot_use_cases = bot_use_cases.with_user_directory(user_directory);
        }
        let bot_use_cases = Arc::new(bot_use_cases);
        let frontend_connections = Arc::new(WorkbenchConnectionRegistry::with_bot_query(
            bot_use_cases.clone(),
        ));
        let run_channels = Arc::new(RunChannelManager::new());
        let frontend_run_channels = run_channels.clone();
        let ws_bot_delivery: Arc<dyn BotDeliveryPort> = bot_connections.clone();
        let provider_transport = Arc::new(
            bcs_provider_http::HttpProviderTransport::with_url_guard(
                outbound_url_guard.clone(),
            ),
        );
        let provider_stream_gray_list = create_provider_stream_gray_list(&config);
        let raw_bot_delivery: Arc<dyn BotDeliveryPort> = Arc::new(
            bcs_provider_http::BotTransportMux::new(
                ws_bot_delivery,
                provider_transport.clone(),
            ),
        );
        let bot_delivery = maybe_wrap_bot_delivery(&config, raw_bot_delivery);
        let raw_frontend_delivery: Arc<dyn FrontendDeliveryPort> =
            Arc::new(WorkbenchFrontendDelivery::new(
                frontend_connections.clone(),
                frontend_run_channels.clone(),
            ));
        let frontend_delivery = maybe_wrap_frontend_delivery(&config, raw_frontend_delivery);
        let interceptors =
            create_interceptor_chain(&config).expect("default security gateway config is valid");
        let cutoff_timestamp = config.message_history.cutoff_timestamp;
        let manager_worker_cutoff_timestamp =
            config.message_history.manager_worker_cutoff_timestamp;
        let session_repo = Arc::new(MemorySessionRepo::new());
        let message_repo: Arc<dyn MessageRepoPort> = Arc::new(MemoryMessageRepo::new());
        let group_session_metrics_snapshot: Arc<dyn GroupSessionMetricsSnapshotPort> =
            session_repo.clone();
        let session_management: Arc<dyn SessionManagementService> = Arc::new(
            SessionManagementServiceImpl::new(session_repo.clone(), group_repo_for_session.clone())
                .with_bot_runtime(bot_use_cases.clone()),
        );
        let bot_run_context: Arc<dyn BotRunContextPort> =
            Arc::new(bcs_message_flow::MemoryBotRunContextStore::new());
        let group_message_history = create_group_message_history_service(
            sessions.clone(),
            bot_registry.clone(),
            bot_delivery.clone(),
            Arc::clone(&bot_connections),
            provider_transport.clone(),
            message_repo.clone(),
            session_repo.clone(),
            cutoff_timestamp,
            manager_worker_cutoff_timestamp,
            config.message_history.new_participant_visible_limit,
            config.message_history.default_page_limit,
            config.message_history.max_page_limit,
        );
        let a2a_run_store = Arc::new(bcs_message_flow::a2a_chat::ChatRunStore::with_capacity(
            config.async_chat_run_max_entries,
        ));
        let a2a_run_port = Arc::new(crate::http_adapter::BootstrapRunChannelPort {
            run_channels: run_channels.clone(),
        });
        let metrics = crate::metrics::MetricsRuntime::install(&config)
            .expect("metrics runtime must initialize");
        let a2a_chat_impl = Arc::new(
            A2aChat::new_with_run_ports(
                bot_delivery.clone(),
                a2a_run_store,
                config.async_chat_run_timeout_ms,
                bot_registry.clone(),
                friend_store.clone(),
                a2a_run_port.clone(),
                a2a_run_port.clone(),
            )
            .with_organization(organization_core.clone())
            .with_interceptors(interceptors.clone())
            .with_run_lifecycle_hook(direct_chat_run_lifecycle_hook(metrics.as_ref()))
            .with_bot_run_context(bot_run_context.clone()),
        );
        let a2a_chat: Arc<dyn A2aChatService> = a2a_chat_impl.clone();
        let a2a_chat_runs: Arc<dyn A2aChatRunService> = a2a_chat_impl.clone();
        let a2a_chat_runs = maybe_wrap_a2a_chat_runs(&config, a2a_chat_runs);
        let direct_chat_run_snapshot: Arc<dyn DirectChatRunSnapshotPort> = a2a_chat_impl;
        let proposal_base_url = config
            .bcs_endpoint
            .clone()
            .unwrap_or_else(|| format!("http://{}:{}", config.bind, config.port));
        let system_message: Arc<dyn bcs_service_api::SystemMessageService> = {
            let dispatcher = SystemMessageDispatcherImpl::builder()
                .with_registry(bot_registry.clone())
                .with_delivery(bot_delivery.clone())
                .with_frontend_delivery(frontend_delivery.clone())
                .with_bot_run_context(bot_run_context.clone())
                .with_message_repo(message_repo.clone())
                .with_provider_stream_gray_list(provider_stream_gray_list.clone())
                .register(BotJoinedMessageProducer::new(
                    group_message_history.clone(),
                ))
                .register(HumanJoinedMessageProducer::new())
                .register(ParticipantModeChangedMessageProducer)
                .register(GenericNotificationMessageProducer)
                .register(BotLeftMessageProducer)
                .register(SessionContextMessageProducer)
                .register(BotHiddenNoticeProducer)
                .build()
                .expect("system message dispatcher must be fully wired");
            Arc::new(SystemMessageServiceImpl::new(
                Arc::new(dispatcher),
                sessions.clone(),
            ))
        };
        let (message_flow, channel_slot) = create_message_flow_services(
            bot_registry.clone(),
            sessions.clone(),
            router.clone(),
            bot_delivery.clone(),
            frontend_delivery.clone(),
            config.max_group_messages,
            interceptors.clone(),
            session_management.clone(),
            bot_run_context.clone(),
            system_message.clone(),
            Some(message_repo.clone()),
            provider_stream_gray_list.clone(),
            Arc::new(AdminInvocationTerminalObserver::new(
                admin_invocation_runs.clone(),
                outbound_url_guard.clone(),
            )),
        );
        let group_management_impl = Arc::new(GroupManagement::new(
            sessions.clone(),
            bot_registry.clone(),
            friend_store.clone(),
            relation_store.clone(),
            GroupConfig {
                max_group_members: config.max_group_members,
                max_groups_as_driver: config.max_groups_as_driver,
                max_groups_as_member: config.max_groups_as_member,
                relation_env: crate::env::resolve_env(),
            },
            session_management.clone(),
            system_message.clone(),
        )
        .with_outbound_url_guard(outbound_url_guard.clone())
        .with_bot_runtime(bot_use_cases.clone()));
        let group_management = maybe_wrap_group_management(&config, group_management_impl.clone());
        let group_proposals = Arc::new(GroupProposalUseCases::new(
            sessions.clone(),
            bot_registry.clone(),
            friend_store.clone(),
            proposals.clone(),
            session_management.clone(),
            system_message.clone(),
            GroupProposalUseCasesConfig {
                max_group_members: config.max_group_members,
                max_groups_as_driver: config.max_groups_as_driver,
                max_groups_as_member: config.max_groups_as_member,
                proposal_base_url,
                botchat_base_url: config.botchat_url.clone(),
            },
        ));
        let group_fusion = Arc::new(BcsGroupFusion::new(sessions.clone(), fusion.clone()));
        let message_flow = maybe_wrap_message_flow(&config, message_flow);
        provider_transport.set_ingest(message_flow.clone(), bot_run_context.clone());
        let collaboration_store = Arc::new(MemoryCollaborationStore::new());
        let judge_evaluator: Arc<dyn JudgeEvaluatorPort> =
            Arc::new(NoopJudgeEvaluator::default());
        let collaboration_runtime = Arc::new(
            CollaborationRuntime::new(
                collaboration_store.clone(),
                collaboration_store.clone(),
                collaboration_store.clone(),
                collaboration_store,
                sessions.clone(),
                session_management.clone(),
                bot_delivery.clone(),
                judge_evaluator,
            )
            .with_bot_registry(bot_registry.clone())
            .with_callback_url_guard(outbound_url_guard.clone())
            .with_frontend_delivery(frontend_delivery.clone()),
        );
        let channel_runtime = build_channel_runtime(
            &config,
            channel_slot,
            memory_channel_repos(None),
            session_repo.clone(),
            message_flow.clone(),
            system_message.clone(),
            collaboration_runtime.clone(),
            sessions.clone(),
            bot_registry.clone(),
        )
        .expect("default channel runtime must initialize");
        let channel_service = channel_runtime.service.clone();
        let provider_bot_events: Arc<dyn ProviderBotEventService> = Arc::new(
            ProviderBotEvents::new(
                provider_bot_core.clone(),
                bot_run_context.clone(),
                message_flow.clone(),
            )
            .with_collaboration_runtime(collaboration_runtime.clone()),
        );
        let services = ServicesBuilder::default()
            .registry(bot_registry.clone())
            .group(sessions)
            .routing(router)
            .fusion(fusion)
            .proposal(proposals)
            .friend(friend_store)
            .relation(relation_store)
            .bot_delivery(bot_delivery)
            .bot_run_context(bot_run_context)
            .frontend_delivery(frontend_delivery)
            .message_flow(message_flow)
            .group_message_history(group_message_history)
            .a2a_chat(a2a_chat)
            .a2a_chat_runs(a2a_chat_runs)
            .collaboration_runtime(collaboration_runtime)
            .collaboration_templates(build_standalone_collaboration_template_service(&config))
            .bot_query(bot_use_cases.clone())
            .bot_management(bot_use_cases.clone())
            .bot_runtime(bot_use_cases.clone())
            .bot_discovery(bot_use_cases)
            .provider_core(provider_core)
            .provider_bot_core(provider_bot_core)
            .provider_management(provider_management)
            .organization(organization_core)
            .organization_management(organization_management)
            .provider_bot_events(provider_bot_events)
            .group_management(group_management.clone())
            .group_query(group_management_impl.clone())
            .workbench_sessions(group_management_impl)
            .group_proposals(group_proposals)
            .group_fusion(group_fusion)
            .system_message(system_message)
            .session_management(session_management.clone())
            .channel(channel_service.clone())
            .secret(default_bootstrap_secret_service())
            .build()
            .expect("services must be fully wired");

        // Start timeout scanner for service-invocation sessions
        let _timeout_handle = crate::timeout_scanner::spawn_with_url_guard(
            services.session_management.clone(),
            services.group.clone(),
            crate::timeout_scanner::DEFAULT_SCAN_INTERVAL,
            outbound_url_guard.clone(),
        );
        let _state_machine_timeout_handle = crate::state_machine_timeout_scanner::spawn(
            services.collaboration_runtime.clone(),
            crate::state_machine_timeout_scanner::DEFAULT_SCAN_INTERVAL,
            crate::state_machine_timeout_scanner::DEFAULT_BATCH_SIZE,
            crate::state_machine_timeout_scanner::DEFAULT_TIMEOUT_GRACE_MS,
        );

        // Start JWT token expiry scanner
        let _token_expiry_handle = crate::token_expiry_scanner::spawn(
            bot_connections.clone(),
            services.bot_runtime.clone(),
            crate::token_expiry_scanner::DEFAULT_SCAN_INTERVAL,
        );

        let (leader_election, lifecycle) = create_standalone_leader_lifecycle();
        register_channel_lifecycles(&lifecycle, &channel_runtime.lifecycles);

        let auth_config = crate::auth_wiring::resolve_auth_config(
            &config.auth,
            crate::config_loader::Environment::resolve().as_str(),
        );
        let user_identity_port = Some(crate::identity_wiring::memory_user_identity_port());
        let auth_chain = Arc::new(crate::auth_wiring::build_auth_chain(
            &auth_config,
            bot_registry.clone(),
            user_identity_port.clone(),
        ));

        Self {
            config,
            services,
            run_channels,
            bot_connections,
            frontend_connections,
            frontend_run_channels,
            coordination_processed: Arc::new(Mutex::new(std::collections::HashMap::new())),
            leader_election,
            lifecycle,
            fuse_client: None,
            provider_credentials: provider_repos.provider_credentials.clone(),
            provider_stream_gray_list,
            channel_http_ingress: channel_runtime.http_ingress.clone(),
            group_metrics_snapshot,
            group_session_metrics_snapshot,
            bot_metrics_snapshot,
            direct_chat_run_snapshot,
            metrics,
            auth_chain,
            auth_config,
            user_identity_port,
            outbound_url_guard,
            admin_invocation_runs,
        }
    }
}

impl BcsServerState {
    /// Create a default state for testing.
    #[cfg(test)]
    pub fn default_for_test() -> Self {
        Self::default()
    }
}

/// BCS server.
pub struct BcsServer {
    config: BcsConfig,
    state: Arc<BcsServerState>,
}

/// Create fusion service: bcsfuse HTTP delegation or local fallback.
fn create_fusion_service(
    config: &BcsConfig,
) -> (
    Arc<dyn bcs_service_api::FusionCoreService>,
    Option<Arc<FuseClient>>,
) {
    if config.bcsfuse.enabled {
        match FuseClientService::new(&config.bcsfuse, &config.bots_base_dir) {
            Ok(svc) => {
                info!(url = %config.bcsfuse.url, "bcsfuse integration enabled");
                let shared_client = svc.client();
                (Arc::new(svc), Some(shared_client))
            }
            Err(e) => {
                warn!(error = %e, "Failed to create FuseClientService, falling back to local fusion");
                (
                    Arc::new(LocalFusionService::new(config.bots_base_dir.clone())),
                    None,
                )
            }
        }
    } else {
        (
            Arc::new(LocalFusionService::new(config.bots_base_dir.clone())),
            None,
        )
    }
}

fn create_standalone_leader_lifecycle() -> (
    Arc<dyn LeaderElectionPort>,
    Arc<Mutex<LifecycleOrchestrator>>,
) {
    let leader = Arc::new(StandaloneLeaderElection::local());
    lifecycle_with_leader("leader_election", leader)
}

fn create_leader_lifecycle(
    leader_election: Option<LeaderElectionRegistration>,
) -> (
    Arc<dyn LeaderElectionPort>,
    Arc<Mutex<LifecycleOrchestrator>>,
) {
    if let Some(registration) = leader_election {
        let mut lifecycle = LifecycleOrchestrator::new();
        if let Some(service) = registration.lifecycle {
            lifecycle.register("leader_election", service);
        }
        info!("Using configured leader election provider");
        return (registration.leader, Arc::new(Mutex::new(lifecycle)));
    }

    create_standalone_leader_lifecycle()
}

async fn create_configured_leader_election(
    config: &BcsConfig,
) -> Result<Option<LeaderElectionRegistration>> {
    let Some(election) = config.leader_election.as_ref() else {
        return Ok(None);
    };
    if !election.enabled {
        return Ok(None);
    }

    let provider = election
        .provider
        .as_deref()
        .map(str::trim)
        .filter(|provider| !provider.is_empty())
        .ok_or_else(|| {
            crate::BcsError::InvalidConfig(
                "leader_election.provider is required when leader_election.enabled = true"
                    .to_string(),
            )
        })?;

    let provider_config = election.providers.get(provider).cloned().unwrap_or_default();

    build_registered_leader_election(config, provider, provider_config)
        .await?
        .ok_or_else(|| {
            crate::BcsError::InvalidConfig(format!(
                "leader_election provider '{provider}' is not available in this binary"
            ))
        })
        .map(Some)
}

fn lifecycle_with_leader<L>(
    name: &'static str,
    leader: Arc<L>,
) -> (
    Arc<dyn LeaderElectionPort>,
    Arc<Mutex<LifecycleOrchestrator>>,
)
where
    L: LeaderElectionPort + ServiceLifecycle + 'static,
{
    let leader_election: Arc<dyn LeaderElectionPort> = leader.clone();
    let lifecycle_service: Arc<dyn ServiceLifecycle> = leader;
    let mut lifecycle = LifecycleOrchestrator::new();
    lifecycle.register(name, lifecycle_service);
    (leader_election, Arc::new(Mutex::new(lifecycle)))
}

/// Register FuseClientLifecycle (and any other late-bound lifecycle adapters)
/// onto the orchestrator. Must run after fuse_client is constructed but
/// before BcsServer::run begins driving initialize_all/shutdown_all.
///
/// Sync helper — orchestrator is freshly built and has zero contention at
/// this point, so try_lock always succeeds. Avoids polluting the call sites
/// with async/await chains.
fn register_late_lifecycles(
    lifecycle: &Arc<Mutex<LifecycleOrchestrator>>,
    fuse_client: Option<&Arc<FuseClient>>,
) {
    if let Some(client) = fuse_client {
        let adapter = Arc::new(bcs_fusion::FuseClientLifecycle::new(client.clone()));
        // try_lock cannot fail here: the orchestrator has just been built and
        // is not yet shared with any other task.
        let mut guard = lifecycle
            .try_lock()
            .expect("orchestrator should be uncontended at registration time");
        guard.register("fuse_client", adapter as Arc<dyn ServiceLifecycle>);
    }
}

fn register_channel_lifecycles(
    lifecycle: &Arc<Mutex<LifecycleOrchestrator>>,
    channel_lifecycles: &[Arc<dyn ServiceLifecycle>],
) {
    if channel_lifecycles.is_empty() {
        return;
    }
    let mut guard = lifecycle
        .try_lock()
        .expect("orchestrator should be uncontended at registration time");
    for (idx, service) in channel_lifecycles.iter().enumerate() {
        let name = match idx {
            0 => "channel_provider",
            1 => "channel_provider_1",
            2 => "channel_provider_2",
            _ => "channel_provider_extra",
        };
        guard.register(name, service.clone());
    }
}

struct UseCaseBundle {
    actor_directory: Arc<dyn bcs_service_api::ActorDirectoryService>,
    friend_use_cases: Arc<dyn bcs_service_api::FriendService>,
    human_actors: Arc<dyn bcs_service_api::HumanActorService>,
    bot_onboarding: Arc<dyn bcs_service_api::BotOnboardingService>,
    bot_query: Arc<dyn bcs_service_api::BotQueryService>,
    bot_management: Arc<dyn bcs_service_api::BotManagementService>,
    bot_runtime: Arc<dyn bcs_service_api::BotRuntimeConnectionService>,
    bot_discovery: Arc<dyn bcs_service_api::BotDiscoveryService>,
    group_management: Arc<dyn bcs_service_api::GroupManagementService>,
    group_query: Arc<dyn bcs_service_api::GroupQueryService>,
    workbench_sessions: Arc<dyn bcs_service_api::WorkbenchSessionService>,
    group_proposals: Arc<dyn bcs_service_api::GroupProposalService>,
    group_fusion: Arc<dyn bcs_service_api::GroupFusionService>,
    system_message: Arc<dyn bcs_service_api::SystemMessageService>,
}

fn build_use_case_bundle(
    config: &BcsConfig,
    bot_registry: Arc<dyn BotRegistryCoreService>,
    bot_core: Arc<BotCore>,
    organization_core: Arc<dyn OrganizationCoreService>,
    bot_connection_control: Arc<dyn bcs_service_api::BotConnectionControlPort>,
    group: Arc<dyn GroupCoreService>,
    proposal: Arc<dyn bcs_service_api::ProposalCoreService>,
    friend: Arc<dyn bcs_service_api::FriendCoreService>,
    friend_request: Arc<dyn bcs_service_api::FriendRequestCoreService>,
    relation: Arc<dyn bcs_service_api::RelationCoreService>,
    fuse_client: Option<Arc<FuseClient>>,
    fusion: Arc<dyn bcs_service_api::FusionCoreService>,
    bot_delivery: Arc<dyn BotDeliveryPort>,
    frontend_delivery: Arc<dyn FrontendDeliveryPort>,
    group_message_history: Arc<dyn GroupMessageHistoryService>,
    session_management: Arc<dyn SessionManagementService>,
    bot_run_context: Arc<dyn BotRunContextPort>,
    user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
    message_repo: Option<Arc<dyn MessageRepoPort>>,
    callback_url_guard: OutboundUrlGuard,
    provider_stream_gray_list: Arc<ProviderStreamGrayList>,
) -> UseCaseBundle {
    let mut actor_directory =
        bcs_bot::ActorDirectory::new(bot_registry.clone(), friend.clone(), relation.clone())
            .with_recommend_min_score(config.bcsfuse.recommend_min_score);
    if let Some(client) = fuse_client {
        actor_directory =
            actor_directory.with_worker_profiles(Arc::new(FuseWorkerProfileService::new(client)));
    }

    let mut bot_use_cases = Bot::new_with_friend(bot_registry.clone(), friend.clone())
        .with_bot_core(bot_core.clone())
        .with_organization(organization_core.clone())
        .with_relation(relation.clone())
        .with_connection_control(bot_connection_control.clone());
    if let Some(user_directory) = user_directory {
        bot_use_cases = bot_use_cases.with_user_directory(user_directory);
    }
    let bot_use_cases = Arc::new(bot_use_cases);
    let system_message: Arc<dyn bcs_service_api::SystemMessageService> = {
        let mut disp_builder = SystemMessageDispatcherImpl::builder()
            .with_registry(bot_registry.clone())
            .with_delivery(bot_delivery.clone())
            .with_frontend_delivery(frontend_delivery.clone())
            .with_bot_run_context(bot_run_context)
            .with_provider_stream_gray_list(provider_stream_gray_list.clone())
            .register(BotJoinedMessageProducer::new(
                group_message_history.clone(),
            ))
            .register(HumanJoinedMessageProducer::new())
            .register(ParticipantModeChangedMessageProducer)
            .register(GenericNotificationMessageProducer)
.register(BotLeftMessageProducer)
            .register(SessionContextMessageProducer)
            .register(BotHiddenNoticeProducer);
        if let Some(repo) = &message_repo {
            disp_builder = disp_builder.with_message_repo(repo.clone());
        }
        let dispatcher = disp_builder
            .build()
            .expect("system message dispatcher must be fully wired");
        Arc::new(SystemMessageServiceImpl::new(
            Arc::new(dispatcher),
            group.clone(),
        ))
    };
    let group_management = Arc::new(GroupManagement::new(
        group.clone(),
        bot_registry.clone(),
        friend.clone(),
        relation.clone(),
        GroupConfig {
            max_group_members: config.max_group_members,
            max_groups_as_driver: config.max_groups_as_driver,
            max_groups_as_member: config.max_groups_as_member,
            relation_env: crate::env::resolve_env(),
        },
        session_management.clone(),
        system_message.clone(),
    )
    .with_outbound_url_guard(callback_url_guard.clone())
    .with_bot_runtime(bot_use_cases.clone()));
    let proposal_base_url = config
        .bcs_endpoint
        .clone()
        .unwrap_or_else(|| format!("http://{}:{}", config.bind, config.port));
    let group_proposals = Arc::new(GroupProposalUseCases::new(
        group.clone(),
        bot_registry.clone(),
        friend.clone(),
        proposal,
        session_management,
        system_message.clone(),
        GroupProposalUseCasesConfig {
            max_group_members: config.max_group_members,
            max_groups_as_driver: config.max_groups_as_driver,
            max_groups_as_member: config.max_groups_as_member,
            proposal_base_url,
            botchat_base_url: config.botchat_url.clone(),
        },
    ));

    UseCaseBundle {
        actor_directory: Arc::new(actor_directory),
        friend_use_cases: Arc::new(bcs_friend::Friend::new(
            bot_registry.clone(),
            friend,
            friend_request,
            relation.clone(),
        )),
        human_actors: Arc::new(bcs_bot::HumanActor::new(
            bot_registry.clone(),
            relation.clone(),
        )),
        bot_onboarding: Arc::new(bcs_bot::BotOnboarding::new(
            bot_registry,
            relation,
            config.onboard_binding_enabled,
            config.default_visibility.clone(),
        )),
        bot_query: bot_use_cases.clone(),
        bot_management: bot_use_cases.clone(),
        bot_runtime: bot_use_cases.clone(),
        bot_discovery: bot_use_cases,
        group_management: group_management.clone(),
        group_query: group_management.clone(),
        workbench_sessions: group_management,
        group_proposals,
        group_fusion: Arc::new(BcsGroupFusion::new(group, fusion)),
        system_message,
    }
}

fn create_message_flow_services(
    registry: Arc<dyn BotRegistryCoreService>,
    group: Arc<dyn GroupCoreService>,
    routing: Arc<dyn RoutingCoreService>,
    bot_delivery: Arc<dyn BotDeliveryPort>,
    frontend_delivery: Arc<dyn FrontendDeliveryPort>,
    bot_relay_turn_limit: i64,
    interceptors: Arc<InterceptorChain>,
    session_management: Arc<dyn SessionManagementService>,
    bot_run_context: Arc<dyn BotRunContextPort>,
    system_message: Arc<dyn bcs_service_api::SystemMessageService>,
    message_repo: Option<Arc<dyn MessageRepoPort>>,
    provider_stream_gray_list: Arc<ProviderStreamGrayList>,
    bot_terminal_observer: Arc<dyn BotTerminalObserverPort>,
) -> (Arc<dyn MessageFlowService>, ChannelSlot) {
    let mut message_flow = BcsMessageFlow::new(
        group,
        routing,
        registry,
        bot_delivery.clone(),
        frontend_delivery.clone(),
    )
    .with_bot_relay_turn_limit(bot_relay_turn_limit)
    .with_interceptors(interceptors)
    .with_session_management(session_management)
    .with_bot_run_context(bot_run_context)
    .with_system_message(system_message)
    .with_provider_stream_gray_list(provider_stream_gray_list)
    .with_bot_terminal_observer(bot_terminal_observer);
    if let Some(repo) = message_repo {
        message_flow = message_flow.with_message_repo(repo);
    }
    let channel_slot = message_flow.channel_slot();
    let message_flow: Arc<dyn MessageFlowService> = Arc::new(message_flow);

    (message_flow, channel_slot)
}

fn create_interceptor_chain(config: &BcsConfig) -> crate::Result<Arc<InterceptorChain>> {
    let mut chain = InterceptorChain::new();

    #[cfg(feature = "prometheus-metrics")]
    {
        if config.metrics.enabled {
            chain.set_block_hook(Arc::new(crate::metrics::MetricsDeliveryPolicyBlockHook::new(
                Arc::from(bcs_config::resolve_env_str()),
            )));
        }
    }

    let sg = &config.security_gateway;
    let provider = sg.provider.trim();
    let gateway: Arc<dyn SecurityGatewayPort> = if provider.is_empty() || provider == "noop" {
        info!(provider = "noop", dry_run = sg.dry_run, "Initializing noop security gateway interceptor");
        Arc::new(NoopSecurityGateway)
    } else {
        let provider_config = sg.providers.get(provider).cloned().unwrap_or_default();
        build_registered_security_gateway(config, provider, provider_config)?
            .ok_or_else(|| {
                crate::BcsError::InvalidConfig(format!(
                    "security_gateway provider '{provider}' is not available in this binary"
                ))
            })?
            .gateway
    };

    chain.push(SecurityInterceptor::new(gateway, sg.dry_run));

    Ok(Arc::new(chain))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum JudgeLlmProviderKind {
    None,
    OpenAiCompatible,
}

fn select_judge_llm_provider(config: &BcsConfig) -> crate::Result<JudgeLlmProviderKind> {
    match &config.llm.provider_type {
        LlmProviderType::None => Ok(JudgeLlmProviderKind::None),
        LlmProviderType::OpenAiCompatible => Ok(JudgeLlmProviderKind::OpenAiCompatible),
        LlmProviderType::Other(provider) => Err(crate::BcsError::InvalidConfig(format!(
            "llm.type = '{}' is not available in this binary",
            provider
        ))),
    }
}

fn create_public_judge_evaluator(
    config: &BcsConfig,
) -> crate::Result<Arc<dyn JudgeEvaluatorPort>> {
    match select_judge_llm_provider(config)? {
        JudgeLlmProviderKind::None => Ok(Arc::new(NoopJudgeEvaluator::default())),
        JudgeLlmProviderKind::OpenAiCompatible => {
            let llm_config = resolve_llm_config(config);
            let llm_client =
                OpenAiCompatibleLlmClient::new(llm_config.clone()).map_err(|error| {
                    crate::BcsError::InvalidConfig(format!("invalid llm config: {error}"))
                })?;
            info!(
                model = %llm_config.model,
                base_url = %llm_config.base_url,
                structured_output = ?llm_config.structured_output,
                "OpenAI-compatible LLM judge enabled"
            );
            Ok(Arc::new(LlmJudgeService::new(
                Arc::new(llm_client),
                llm_config.model.clone(),
            )))
        }
    }
}

fn create_judge_evaluator(
    config: &BcsConfig,
    extensions: &BcsServerExtensions,
) -> crate::Result<Arc<dyn JudgeEvaluatorPort>> {
    if let Some(llm_provider) = extensions.llm_provider.clone() {
        let llm_config = resolve_llm_config(config);
        info!(
            model = %llm_config.model,
            "Injected LLM judge provider enabled"
        );
        return Ok(Arc::new(LlmJudgeService::new(
            llm_provider,
            llm_config.model.clone(),
        )));
    }

    if let LlmProviderType::Other(provider) = &config.llm.provider_type {
        if let Some(llm_provider) = build_registered_llm_provider(config, provider)? {
            let llm_config = resolve_llm_config(config);
            info!(
                provider = %provider,
                model = %llm_config.model,
                "Registered LLM judge provider enabled"
            );
            return Ok(Arc::new(LlmJudgeService::new(
                llm_provider,
                llm_config.model.clone(),
            )));
        }
    }

    create_public_judge_evaluator(config)
}

fn resolve_llm_config(config: &BcsConfig) -> LlmConfig {
    let mut llm_config = config.llm.clone();
    if llm_config
        .api_key
        .as_ref()
        .is_some_and(|api_key| api_key.expose_secret().trim().is_empty())
    {
        llm_config.api_key = None;
    }
    if llm_config.api_key.is_none() {
        if let Some(env_name) = llm_config
            .api_key_env
            .as_ref()
            .map(|env_name| env_name.trim())
            .filter(|env_name| !env_name.is_empty())
        {
            if let Ok(api_key) = std::env::var(env_name) {
                if !api_key.trim().is_empty() {
                    llm_config.api_key = Some(Secret::new(api_key));
                }
            }
        }
    }
    llm_config
}

#[cfg(test)]
mod judge_provider_tests {
    use super::*;
    use crate::plugins::LlmProviderFactory;
    use bcs_llm_api::{LlmChatCompletionRequest, LlmChatCompletionResponse, LlmError};
    use bcs_service_api::{JudgeArtifact, JudgeRequest};
    use serde_json::json;

    struct RecordingLlm {
        requests: Mutex<Vec<LlmChatCompletionRequest>>,
    }

    #[async_trait::async_trait]
    impl LlmChatCompletionPort for RecordingLlm {
        async fn complete(
            &self,
            request: LlmChatCompletionRequest,
        ) -> std::result::Result<LlmChatCompletionResponse, LlmError> {
            self.requests.lock().await.push(request);
            Ok(LlmChatCompletionResponse {
                content: json!({
                    "outcome": "approved",
                    "reason": "ok",
                    "confidence": 0.9,
                    "checked_criteria": [],
                    "retry_instruction": "",
                })
                .to_string(),
                raw: json!({}),
            })
        }
    }

    fn test_llm_factory(_config: BcsConfig) -> crate::Result<Arc<dyn LlmChatCompletionPort>> {
        Ok(Arc::new(RecordingLlm {
            requests: Mutex::new(Vec::new()),
        }))
    }

    inventory::submit! {
        LlmProviderFactory {
            name: "test-internal-llm",
            build: test_llm_factory,
        }
    }

    fn judge_request() -> JudgeRequest {
        JudgeRequest {
            run_id: "run-1".to_string(),
            node_id: "judge".to_string(),
            attempt: 1,
            judge_type: "llm".to_string(),
            criteria: vec!["must pass".to_string()],
            allowed_outcomes: vec!["approved".to_string(), "rejected".to_string()],
            input: json!({"question": "ready?"}),
            upstream_outputs: vec![JudgeArtifact {
                node_id: "work".to_string(),
                text: "candidate output".to_string(),
            }],
            artifact_text: "candidate output".to_string(),
        }
    }

    #[test]
    fn judge_llm_provider_selection_uses_openai_compatible_type() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::OpenAiCompatible;

        assert_eq!(
            select_judge_llm_provider(&config).unwrap(),
            JudgeLlmProviderKind::OpenAiCompatible
        );
    }

    #[tokio::test]
    async fn none_llm_without_injection_uses_noop_judge() {
        let config = BcsConfig::default();
        let evaluator =
            create_judge_evaluator(&config, &BcsServerExtensions::default()).expect("evaluator");

        let error = match evaluator.judge(judge_request()).await {
            Ok(_) => panic!("noop judge should reject LLM judge requests"),
            Err(error) => error,
        };

        assert!(error.to_string().contains("requires an enabled LLM"));
    }

    #[tokio::test]
    async fn injected_llm_provider_is_used_when_present() {
        let llm = Arc::new(RecordingLlm {
            requests: Mutex::new(Vec::new()),
        });
        let llm_provider: Arc<dyn LlmChatCompletionPort> = llm.clone();
        let mut config = BcsConfig::default();
        config.llm.model = "custom-judge-model".to_string();
        let extensions = BcsServerExtensions {
            llm_provider: Some(llm_provider),
            ..BcsServerExtensions::default()
        };

        let evaluator = create_judge_evaluator(&config, &extensions).expect("evaluator");
        let decision = evaluator
            .judge(judge_request())
            .await
            .expect("judge decision");

        assert_eq!(decision.outcome, "approved");
        let requests = llm.requests.lock().await;
        assert_eq!(requests.len(), 1);
        assert_eq!(requests[0].model, "custom-judge-model");
    }

    #[tokio::test]
    async fn registered_llm_provider_is_selected_by_type() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::Other("test-internal-llm".to_string());
        config.llm.model = "registered-model".to_string();

        let evaluator =
            create_judge_evaluator(&config, &BcsServerExtensions::default()).expect("evaluator");
        let decision = evaluator
            .judge(judge_request())
            .await
            .expect("judge decision");

        assert_eq!(decision.outcome, "approved");
    }
}

fn create_group_message_history_service(
    group: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    bot_delivery: Arc<dyn BotDeliveryPort>,
    bot_connections: Arc<BotConnectionRegistry>,
    provider_transport: Arc<bcs_provider_http::HttpProviderTransport>,
    message_repo: Arc<dyn MessageRepoPort>,
    session_repo: Arc<dyn SessionRepoPort>,
    cutoff_timestamp: u64,
    manager_worker_cutoff_timestamp: u64,
    new_participant_visible_limit: u64,
    default_page_limit: u32,
    max_page_limit: u32,
) -> Arc<dyn GroupMessageHistoryService> {
    let websocket_request: Arc<dyn GroupHistoryBotRequestPort> =
        Arc::new(BootstrapGroupHistoryBotRequestPort { bot_connections });
    let bot_request: Arc<dyn GroupHistoryBotRequestPort> = Arc::new(
        bcs_provider_http::HistoryRequestMux::new(websocket_request, provider_transport),
    );
    let fallback: Arc<dyn GroupMessageHistoryService> = Arc::new(BcsGroupMessageHistory::new(
        group.clone(),
        registry.clone(),
        bot_delivery,
        bot_request,
    ));
    Arc::new(MessageService::new(
        message_repo,
        fallback,
        session_repo,
        group,
        registry,
        cutoff_timestamp,
        manager_worker_cutoff_timestamp,
        new_participant_visible_limit,
        default_page_limit,
        max_page_limit,
    ))
}

fn maybe_wrap_bot_delivery(
    _config: &BcsConfig,
    delivery: Arc<dyn BotDeliveryPort>,
) -> Arc<dyn BotDeliveryPort> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if _config.metrics.enabled {
            return Arc::new(crate::metrics::MetricsBotDeliveryPort::new(
                delivery,
                Arc::from(bcs_config::resolve_env_str()),
            ));
        }
    }

    delivery
}

fn maybe_wrap_frontend_delivery(
    _config: &BcsConfig,
    delivery: Arc<dyn FrontendDeliveryPort>,
) -> Arc<dyn FrontendDeliveryPort> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if _config.metrics.enabled {
            return Arc::new(crate::metrics::MetricsFrontendDeliveryPort::new(
                delivery,
                Arc::from(bcs_config::resolve_env_str()),
            ));
        }
    }

    delivery
}

fn maybe_wrap_group_management(
    _config: &BcsConfig,
    service: Arc<dyn GroupManagementService>,
) -> Arc<dyn GroupManagementService> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if _config.metrics.enabled {
            return Arc::new(crate::metrics::MetricsGroupManagementService::new(
                service,
                Arc::from(bcs_config::resolve_env_str()),
            ));
        }
    }

    service
}

fn maybe_wrap_message_flow(
    _config: &BcsConfig,
    service: Arc<dyn MessageFlowService>,
) -> Arc<dyn MessageFlowService> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if _config.metrics.enabled {
            return Arc::new(crate::metrics::InstrumentedMessageFlowService::new(
                service,
                Arc::from(bcs_config::resolve_env_str()),
            ));
        }
    }

    service
}

fn maybe_wrap_a2a_chat_runs(
    _config: &BcsConfig,
    service: Arc<dyn A2aChatRunService>,
) -> Arc<dyn A2aChatRunService> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if _config.metrics.enabled {
            return Arc::new(crate::metrics::InstrumentedA2aChatRunService::new(
                service,
                Arc::from(bcs_config::resolve_env_str()),
            ));
        }
    }

    service
}

struct BootstrapGroupHistoryBotRequestPort {
    bot_connections: Arc<BotConnectionRegistry>,
}

#[async_trait::async_trait]
impl GroupHistoryBotRequestPort for BootstrapGroupHistoryBotRequestPort {
    async fn send_history_request(
        &self,
        target: BotDeliveryTarget,
        method: &str,
        params: serde_json::Value,
        timeout_ms: u64,
    ) -> std::result::Result<serde_json::Value, String> {
        let BotDeliveryTarget::WebSocket { bot_id } = target else {
            return Err("history request target is not a websocket bot".to_string());
        };
        self.bot_connections
            .send_request(&bot_id, method, params, timeout_ms)
            .await
    }
}

impl BcsServer {
    /// Create a new BCS server.
    pub fn new(config: BcsConfig) -> Self {
        let outbound_url_guard = outbound_url_guard_from_config(&config);
        Self::new_with_outbound_url_guards(
            config,
            outbound_url_guard.clone(),
            outbound_url_guard.clone(),
            outbound_url_guard,
        )
    }

    pub fn new_allowing_private_outbound_for_tests(config: BcsConfig) -> Self {
        Self::new_with_outbound_url_guards(
            config,
            OutboundUrlGuard::allowing_private_networks_for_tests(),
            OutboundUrlGuard::allowing_private_networks_for_tests(),
            OutboundUrlGuard::allowing_private_networks_for_tests(),
        )
    }

    fn new_with_outbound_url_guards(
        config: BcsConfig,
        provider_webhook_url_guard: OutboundUrlGuard,
        provider_request_url_guard: OutboundUrlGuard,
        callback_url_guard: OutboundUrlGuard,
    ) -> Self {
        let admin_invocation_runs = Arc::new(AdminInvocationStore::default());
        // Create service implementations (synchronous, in-memory mode)
        let provider_repos = memory_provider_repos();
        let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(config.bots_base_dir.clone()));
        let bot_metrics_snapshot: Arc<dyn BotMetricsSnapshotPort> = bot_repo.clone();
        let bot_core_arc: Arc<BotCore> = Arc::new(BotCore::with_provider_repos(
            bot_repo,
            provider_repos.provider_repo.clone(),
            provider_repos.provider_credentials.clone(),
            provider_repos.provider_bindings.clone(),
        ));
        let bot_registry: Arc<dyn BotRegistryCoreService> = bot_core_arc.clone();
        // Local single-node mode uses an in-memory relation graph.
        // F.1/F.2 dual-write wiring: relation store must be created BEFORE
        // friend_store and provider_management so it can be injected into both.
        let relation_store: Arc<RelationCore> = Arc::new(RelationCore::memory());
        let user_directory = create_user_directory_plugin(&config)
            .expect("user directory config is valid for in-memory server");
        let (provider_core, provider_bot_core, provider_management) =
            build_provider_services_with_webhook_url_guard(
                &provider_repos,
                bot_registry.clone(),
                relation_store.clone() as Arc<dyn bcs_service_api::RelationCoreService>,
                user_directory.clone(),
                provider_webhook_url_guard,
            );
        let (organization_core, organization_management) = memory_organization_services(
            &provider_repos,
            provider_core.clone(),
            bot_registry.clone(),
        );
        let group_repo = Arc::new(MemoryGroupRepo::new());
        let group_metrics_snapshot: Arc<dyn GroupMetricsSnapshotPort> = group_repo.clone();
        let group_repo_for_session: Arc<dyn GroupRepoPort> = group_repo.clone();
        let sessions = Arc::new(GroupCore::with_repo(group_repo));
        let router = Arc::new(MessageRouter::new());
        let proposals = Arc::new(ProposalStore::new());
        let friend_repo = Arc::new(MemoryFriendRepo::with_data_dir(
            config.bots_base_dir.clone(),
        ));
        let friend_store: Arc<FriendCore> =
            Arc::new(FriendCore::with_repo(friend_repo).with_relation(
                relation_store.clone() as Arc<dyn bcs_service_api::RelationCoreService>
            ));
        let friend_request_repo = Arc::new(MemoryFriendRequestRepo::with_data_dir(
            config.bots_base_dir.clone(),
        ));
        let friend_request_store: Arc<FriendRequestCore> = Arc::new(FriendRequestCore::with_repo(
            friend_request_repo,
            friend_store.clone(),
            bot_registry.clone(),
        ));

        let (fusion, fuse_client) = create_fusion_service(&config);
        let bot_connections = Arc::new(BotConnectionRegistry::new());
        let mut bot_use_cases = Bot::new_with_friend(bot_registry.clone(), friend_store.clone())
            .with_bot_core(bot_core_arc.clone())
            .with_organization(organization_core.clone())
            .with_relation(
                relation_store.clone() as Arc<dyn bcs_service_api::RelationCoreService>
            )
            .with_connection_control(
                bot_connections.clone() as Arc<dyn bcs_service_api::BotConnectionControlPort>
            );
        if let Some(user_directory) = user_directory.clone() {
            bot_use_cases = bot_use_cases.with_user_directory(user_directory);
        }
        let bot_use_cases = Arc::new(bot_use_cases);
        let frontend_bot_query: Arc<dyn bcs_service_api::BotQueryService> = bot_use_cases.clone();
        let frontend_connections = Arc::new(WorkbenchConnectionRegistry::with_bot_query(
            frontend_bot_query,
        ));
        let run_channels: Arc<RunChannelManager> = Arc::new(RunChannelManager::new());
        let frontend_run_channels = run_channels.clone();
        let ws_bot_delivery: Arc<dyn BotDeliveryPort> = bot_connections.clone();
        let provider_transport = Arc::new(
            bcs_provider_http::HttpProviderTransport::with_url_guard(provider_request_url_guard),
        );
        let provider_stream_gray_list = create_provider_stream_gray_list(&config);
        let raw_bot_delivery: Arc<dyn BotDeliveryPort> = Arc::new(
            bcs_provider_http::BotTransportMux::new(
                ws_bot_delivery,
                provider_transport.clone(),
            ),
        );
        let bot_delivery = maybe_wrap_bot_delivery(&config, raw_bot_delivery);
        let raw_frontend_delivery: Arc<dyn FrontendDeliveryPort> =
            Arc::new(WorkbenchFrontendDelivery::new(
                frontend_connections.clone(),
                frontend_run_channels.clone(),
            ));
        let frontend_delivery = maybe_wrap_frontend_delivery(&config, raw_frontend_delivery);
        let interceptors = create_interceptor_chain(&config)
            .expect("security gateway config is valid for in-memory server");
        let cutoff_timestamp = config.message_history.cutoff_timestamp;
        let manager_worker_cutoff_timestamp =
            config.message_history.manager_worker_cutoff_timestamp;
        let session_repo = Arc::new(MemorySessionRepo::new());
        let message_repo: Arc<dyn MessageRepoPort> = Arc::new(MemoryMessageRepo::new());
        let group_session_metrics_snapshot: Arc<dyn GroupSessionMetricsSnapshotPort> =
            session_repo.clone();
        let session_management: Arc<dyn SessionManagementService> = Arc::new(
            SessionManagementServiceImpl::new(session_repo.clone(), group_repo_for_session.clone())
                .with_bot_runtime(bot_use_cases.clone()),
        );
        let bot_run_context: Arc<dyn BotRunContextPort> =
            Arc::new(bcs_message_flow::MemoryBotRunContextStore::new());
        let group_message_history = create_group_message_history_service(
            sessions.clone(),
            bot_registry.clone(),
            bot_delivery.clone(),
            Arc::clone(&bot_connections),
            provider_transport.clone(),
            message_repo.clone(),
            session_repo.clone(),
            cutoff_timestamp,
            manager_worker_cutoff_timestamp,
            config.message_history.new_participant_visible_limit,
            config.message_history.default_page_limit,
            config.message_history.max_page_limit,
        );
        let a2a_run_store = Arc::new(bcs_message_flow::a2a_chat::ChatRunStore::with_capacity(
            config.async_chat_run_max_entries,
        ));
        let a2a_run_port = Arc::new(crate::http_adapter::BootstrapRunChannelPort {
            run_channels: run_channels.clone(),
        });
        let metrics = crate::metrics::MetricsRuntime::install(&config)
            .expect("metrics runtime must initialize");
        let a2a_chat_impl = Arc::new(
            A2aChat::new_with_run_ports(
                bot_delivery.clone(),
                a2a_run_store,
                config.async_chat_run_timeout_ms,
                bot_registry.clone(),
                friend_store.clone(),
                a2a_run_port.clone(),
                a2a_run_port.clone(),
            )
            .with_organization(organization_core.clone())
            .with_interceptors(interceptors.clone())
            .with_run_lifecycle_hook(direct_chat_run_lifecycle_hook(metrics.as_ref()))
            .with_bot_run_context(bot_run_context.clone()),
        );
        let a2a_chat: Arc<dyn A2aChatService> = a2a_chat_impl.clone();
        let a2a_chat_runs: Arc<dyn A2aChatRunService> = a2a_chat_impl.clone();
        let a2a_chat_runs = maybe_wrap_a2a_chat_runs(&config, a2a_chat_runs);
        let direct_chat_run_snapshot: Arc<dyn DirectChatRunSnapshotPort> = a2a_chat_impl;
        let use_cases = build_use_case_bundle(
            &config,
            bot_registry.clone(),
            bot_core_arc.clone(),
            organization_core.clone(),
            bot_connections.clone() as Arc<dyn bcs_service_api::BotConnectionControlPort>,
            sessions.clone(),
            proposals.clone(),
            friend_store.clone(),
            friend_request_store.clone(),
            relation_store.clone(),
            fuse_client.clone(),
            fusion.clone(),
            bot_delivery.clone(),
            frontend_delivery.clone(),
            group_message_history.clone(),
            session_management.clone(),
            bot_run_context.clone(),
            user_directory.clone(),
            Some(message_repo.clone()),
            callback_url_guard.clone(),
            provider_stream_gray_list.clone(),
        );
        let (message_flow, channel_slot) = create_message_flow_services(
            bot_registry.clone(),
            sessions.clone(),
            router.clone(),
            bot_delivery.clone(),
            frontend_delivery.clone(),
            config.max_group_messages,
            interceptors.clone(),
            session_management.clone(),
            bot_run_context.clone(),
            use_cases.system_message.clone(),
            Some(message_repo.clone()),
            provider_stream_gray_list.clone(),
            Arc::new(AdminInvocationTerminalObserver::new(
                admin_invocation_runs.clone(),
                callback_url_guard.clone(),
            )),
        );

        let collaboration_store = Arc::new(MemoryCollaborationStore::new());
        let extensions = BcsServerExtensions::default();
        let judge_evaluator: Arc<dyn JudgeEvaluatorPort> =
            create_judge_evaluator(&config, &extensions).unwrap_or_else(|error| {
                warn!(
                    error = %error,
                    "Failed to initialize judge evaluator; state-machine judge nodes will fail"
                );
                Arc::new(NoopJudgeEvaluator::default())
            });
        let collaboration_runtime = Arc::new(
            CollaborationRuntime::new(
                collaboration_store.clone(),
                collaboration_store.clone(),
                collaboration_store.clone(),
                collaboration_store,
                sessions.clone(),
                session_management.clone(),
                bot_delivery.clone(),
                judge_evaluator,
            )
            .with_bot_registry(bot_registry.clone())
            .with_callback_url_guard(callback_url_guard.clone())
            .with_frontend_delivery(frontend_delivery.clone()),
        );

        // Build services bundle
        let message_flow = maybe_wrap_message_flow(&config, message_flow);
        provider_transport.set_ingest(message_flow.clone(), bot_run_context.clone());
        let channel_runtime = build_channel_runtime(
            &config,
            channel_slot,
            memory_channel_repos(None),
            session_repo.clone(),
            message_flow.clone(),
            use_cases.system_message.clone(),
            collaboration_runtime.clone(),
            sessions.clone(),
            bot_registry.clone(),
        )
        .expect("in-memory channel runtime must initialize");
        let channel_service = channel_runtime.service.clone();
        let provider_bot_events: Arc<dyn ProviderBotEventService> = Arc::new(
            ProviderBotEvents::new(
                provider_bot_core.clone(),
                bot_run_context.clone(),
                message_flow.clone(),
            )
            .with_collaboration_runtime(collaboration_runtime.clone()),
        );
        let services = ServicesBuilder::default()
            .registry(bot_registry.clone())
            .group(sessions)
            .routing(router)
            .fusion(fusion)
            .proposal(proposals)
            .friend(friend_store)
            .relation(relation_store)
            .bot_delivery(bot_delivery)
            .bot_run_context(bot_run_context)
            .frontend_delivery(frontend_delivery)
            .message_flow(message_flow)
            .group_message_history(group_message_history)
            .a2a_chat(a2a_chat)
            .a2a_chat_runs(a2a_chat_runs)
            .collaboration_runtime(collaboration_runtime)
            .collaboration_templates(build_standalone_collaboration_template_service(&config))
            .actor_directory(use_cases.actor_directory)
            .friend_use_cases(use_cases.friend_use_cases)
            .human_actors(use_cases.human_actors)
            .bot_onboarding(use_cases.bot_onboarding)
            .bot_query(use_cases.bot_query)
            .bot_management(use_cases.bot_management)
            .bot_runtime(use_cases.bot_runtime)
            .bot_discovery(use_cases.bot_discovery)
            .provider_core(provider_core)
            .provider_bot_core(provider_bot_core)
            .provider_management(provider_management)
            .organization(organization_core)
            .organization_management(organization_management)
            .provider_bot_events(provider_bot_events)
            .group_management(maybe_wrap_group_management(&config, use_cases.group_management))
            .group_query(use_cases.group_query)
            .workbench_sessions(use_cases.workbench_sessions)
            .group_proposals(use_cases.group_proposals)
            .group_fusion(use_cases.group_fusion)
            .system_message(use_cases.system_message)
            .session_management(session_management.clone())
            .channel(channel_service.clone())
            .secret(default_bootstrap_secret_service())
            .build()
            .expect("services must be fully wired");

        // Start timeout scanner for service-invocation sessions
        let _timeout_handle = crate::timeout_scanner::spawn_with_url_guard(
            services.session_management.clone(),
            services.group.clone(),
            crate::timeout_scanner::DEFAULT_SCAN_INTERVAL,
            callback_url_guard.clone(),
        );
        let _state_machine_timeout_handle = crate::state_machine_timeout_scanner::spawn(
            services.collaboration_runtime.clone(),
            crate::state_machine_timeout_scanner::DEFAULT_SCAN_INTERVAL,
            crate::state_machine_timeout_scanner::DEFAULT_BATCH_SIZE,
            crate::state_machine_timeout_scanner::DEFAULT_TIMEOUT_GRACE_MS,
        );

        let (leader_election, lifecycle) = create_standalone_leader_lifecycle();
        register_late_lifecycles(&lifecycle, fuse_client.as_ref());
        register_channel_lifecycles(&lifecycle, &channel_runtime.lifecycles);
        let auth_config = crate::auth_wiring::resolve_auth_config(
            &config.auth,
            crate::config_loader::Environment::resolve().as_str(),
        );
        let user_identity_port = Some(crate::identity_wiring::memory_user_identity_port());
        let auth_chain = Arc::new(crate::auth_wiring::build_auth_chain(
            &auth_config,
            bot_registry.clone(),
            user_identity_port.clone(),
        ));
        let state = Arc::new(BcsServerState {
            config: config.clone(),
            services,
            run_channels,
            bot_connections,
            frontend_connections,
            frontend_run_channels,
            coordination_processed: Arc::new(Mutex::new(std::collections::HashMap::new())),
            leader_election,
            lifecycle,
            fuse_client,
            provider_credentials: provider_repos.provider_credentials.clone(),
            provider_stream_gray_list,
            channel_http_ingress: channel_runtime.http_ingress.clone(),
            group_metrics_snapshot,
            group_session_metrics_snapshot,
            bot_metrics_snapshot,
            direct_chat_run_snapshot,
            metrics,
            auth_chain,
            auth_config,
            user_identity_port,
            outbound_url_guard: callback_url_guard,
            admin_invocation_runs,
        });

        Self { config, state }
    }

    /// Create a new BCS server with storage (async).
    ///
    /// Public builds use local storage and standalone leader election.
    pub async fn new_with_storage(config: BcsConfig) -> crate::Result<Self> {
        let infrastructure_plugins = InfrastructurePlugins::from_config(&config).await?;
        Self::new_with_infrastructure(
            config,
            infrastructure_plugins,
            BcsServerExtensions::default(),
        )
        .await
    }

    /// Create a new BCS server with externally supplied infrastructure plugins.
    pub async fn new_with_infrastructure(
        config: BcsConfig,
        infrastructure_plugins: InfrastructurePlugins,
        extensions: BcsServerExtensions,
    ) -> crate::Result<Self> {
        use bcs_service_api::BotRegistryCoreService;

        let outbound_url_guard = outbound_url_guard_from_config(&config);
        let admin_invocation_runs = Arc::new(AdminInvocationStore::default());
        let user_directory = match extensions.user_directory_plugin.clone() {
            Some(plugin) => Some(plugin),
            None => create_user_directory_plugin(&config)?,
        };
        info!(
            cache_plugin = %infrastructure_plugins.cache_kind(),
            db_plugin = %infrastructure_plugins.db_kind(),
            cache_adapter_ready = infrastructure_plugins.cache().is_some(),
            db_adapter_ready = infrastructure_plugins.db().is_some(),
            "Selected infrastructure plugins"
        );

        // Run SQLite DDL initialization when local SQLite is selected.
        if infrastructure_plugins.db_kind() == DbPluginKind::LocalSqlite {
            if let Some(db) = infrastructure_plugins.db() {
                let report = crate::migrations::run_sqlite_migrations_with_report(db.as_ref())
                    .await
                    .map_err(|err| {
                        crate::BcsError::StorageInitError(format!(
                            "run sqlite migrations: {}",
                            err
                        ))
                    })?;
                tracing::info!(
                    current_version = ?report.current_version,
                    target_version = report.target_version,
                    applied_versions = report.applied_versions.len(),
                    repaired_columns = report.repaired_columns.len(),
                    "SQLite migrations completed"
                );
            }
        }

        let db_plugin = infrastructure_plugins.db().ok_or_else(|| {
            crate::BcsError::StorageInitError(
                "LocalSqlite storage selected but DbPlugin handle is unavailable".to_string(),
            )
        })?;
        let db_kind = infrastructure_plugins.db_kind();
        let db_flavor = db_sql_flavor(&db_kind);
        let provider_repos = db_provider_repos(db_plugin.clone(), &db_kind);

        let cache_plugin = infrastructure_plugins
            .cache()
            .unwrap_or_else(|| Arc::new(bcs_cache_local::InMemoryCachePlugin::new()));
        let cache_key_prefix = config.cache.redis.effective_key_prefix();
        info!(db_plugin = %db_kind, "Initializing DB-backed bot registry");
        let bot_repo = Arc::new(PersistentBotRepo::with_plugins_flavor_and_cache_key_prefix(
            cache_plugin,
            db_plugin.clone(),
            db_flavor,
            cache_key_prefix,
        ));
        let bot_metrics_snapshot: Arc<dyn BotMetricsSnapshotPort> = bot_repo.clone();
        let bot_core_arc = Arc::new(BotCore::with_provider_repos(
            bot_repo,
            provider_repos.provider_repo.clone(),
            provider_repos.provider_credentials.clone(),
            provider_repos.provider_bindings.clone(),
        ));
        let bot_registry: Arc<dyn BotRegistryCoreService> = bot_core_arc.clone();

        let leader_election_registration = if extensions.leader_election.is_some() {
            extensions.leader_election.clone()
        } else {
            create_configured_leader_election(&config).await?
        };
        let (leader_election, lifecycle) = create_leader_lifecycle(leader_election_registration);

        // Create group session storage.
        let (sessions, group_metrics_snapshot, group_repo): (
            Arc<dyn GroupCoreService>,
            Arc<dyn GroupMetricsSnapshotPort>,
            Arc<dyn GroupRepoPort>,
        ) = {
            let env = crate::env::resolve_env();
            info!(env = %env, db_plugin = %db_kind, "DB-backed group storage initialized");
            let repo = match db_kind {
                DbPluginKind::LocalSqlite => {
                    Arc::new(MySqlGroupStore::sqlite(db_plugin.clone(), env))
                }
                DbPluginKind::Mysql => Arc::new(MySqlGroupStore::new(db_plugin.clone(), env)),
                DbPluginKind::External(provider) => {
                    panic!("external database plugin '{}' has no group store wiring", provider)
                }
            };
            (
                Arc::new(GroupCore::with_repo(repo.clone())),
                repo.clone() as Arc<dyn GroupMetricsSnapshotPort>,
                repo as Arc<dyn GroupRepoPort>,
            )
        };

        // Create other service implementations
        let router = Arc::new(MessageRouter::new());
        let proposals = Arc::new(ProposalStore::new());

        let (fusion, fuse_client) = create_fusion_service(&config);
        register_late_lifecycles(&lifecycle, fuse_client.as_ref());

        // F.1/F.2 dual-write wiring: relation_svc MUST be constructed BEFORE
        // friend_svc so it can be injected via `with_relation(...)`.
        info!(db_plugin = %db_kind, "Initializing DB-backed relation storage");
        let relation_repo = match db_kind {
            DbPluginKind::LocalSqlite => Arc::new(DbRelationStore::sqlite(db_plugin.clone())),
            DbPluginKind::Mysql => Arc::new(DbRelationStore::mysql(db_plugin.clone())),
            DbPluginKind::External(provider) => {
                panic!(
                    "external database plugin '{}' has no relation store wiring",
                    provider
                )
            }
        };
        let relation_svc: Arc<dyn bcs_service_api::RelationCoreService> =
            Arc::new(RelationCore::with_repo(relation_repo));

        let (provider_core, provider_bot_core, provider_management) =
            build_provider_services_with_webhook_url_guard(
                &provider_repos,
                bot_registry.clone(),
                relation_svc.clone(),
                user_directory.clone(),
                outbound_url_guard.clone(),
            );
        let (organization_core, organization_management) = db_organization_services(
            db_plugin.clone(),
            &db_kind,
            &provider_repos,
            provider_core.clone(),
            bot_registry.clone(),
        );

        // Create SQLite-backed friend services.
        let (friend_svc, friend_request_svc): (
            Arc<dyn bcs_service_api::FriendCoreService>,
            Arc<dyn bcs_service_api::FriendRequestCoreService>,
        ) = {
            info!(
                db_plugin = %db_kind,
                "Initializing DB-backed friend storage with relation dual-write"
            );
            let friend_repo = match db_kind {
                DbPluginKind::LocalSqlite => Arc::new(DbFriendStore::sqlite(db_plugin.clone())),
                DbPluginKind::Mysql => Arc::new(DbFriendStore::mysql(db_plugin.clone())),
                DbPluginKind::External(provider) => {
                    panic!("external database plugin '{}' has no friend store wiring", provider)
                }
            };
            let friend_store: Arc<dyn bcs_service_api::FriendCoreService> = Arc::new(
                FriendCore::with_repo(friend_repo).with_relation(relation_svc.clone()),
            );
            let friend_request_repo = match db_kind {
                DbPluginKind::LocalSqlite => {
                    Arc::new(DbFriendRequestStore::sqlite(db_plugin.clone()))
                }
                DbPluginKind::Mysql => Arc::new(DbFriendRequestStore::mysql(db_plugin.clone())),
                DbPluginKind::External(provider) => {
                    panic!(
                        "external database plugin '{}' has no friend request store wiring",
                        provider
                    )
                }
            };
            let friend_request_store: Arc<dyn bcs_service_api::FriendRequestCoreService> =
                Arc::new(FriendRequestCore::with_repo(
                    friend_request_repo,
                    friend_store.clone(),
                    bot_registry.clone(),
                ));

            (friend_store, friend_request_store)
        };
        let bot_connections = Arc::new(BotConnectionRegistry::new());
        let mut bot_runtime_for_session =
            Bot::new_with_friend(bot_registry.clone(), friend_svc.clone())
                .with_bot_core(bot_core_arc.clone())
                .with_organization(organization_core.clone())
                .with_connection_control(
                    bot_connections.clone()
                        as Arc<dyn bcs_service_api::BotConnectionControlPort>,
                );
        if let Some(user_directory) = user_directory.clone() {
            bot_runtime_for_session =
                bot_runtime_for_session.with_user_directory(user_directory);
        }
        let bot_runtime_for_session: Arc<dyn bcs_service_api::BotRuntimeConnectionService> =
            Arc::new(bot_runtime_for_session);
        let frontend_connections = Arc::new(WorkbenchConnectionRegistry::new());
        let run_channels = Arc::new(RunChannelManager::new());
        let frontend_run_channels = run_channels.clone();
        let ws_bot_delivery: Arc<dyn BotDeliveryPort> = bot_connections.clone();
        let provider_transport = Arc::new(
            bcs_provider_http::HttpProviderTransport::with_url_guard(
                outbound_url_guard.clone(),
            ),
        );
        let provider_stream_gray_list = create_provider_stream_gray_list(&config);
        let raw_bot_delivery: Arc<dyn BotDeliveryPort> = Arc::new(
            bcs_provider_http::BotTransportMux::new(
                ws_bot_delivery,
                provider_transport.clone(),
            ),
        );
        let bot_delivery = maybe_wrap_bot_delivery(&config, raw_bot_delivery);
        let raw_frontend_delivery: Arc<dyn FrontendDeliveryPort> =
            Arc::new(WorkbenchFrontendDelivery::new(
                frontend_connections.clone(),
                frontend_run_channels.clone(),
            ));
        let frontend_delivery = maybe_wrap_frontend_delivery(&config, raw_frontend_delivery);
        let interceptors = create_interceptor_chain(&config)?;
        let cutoff_timestamp = config.message_history.cutoff_timestamp;
        let manager_worker_cutoff_timestamp =
            config.message_history.manager_worker_cutoff_timestamp;
        let (
            session_repo,
            session_management,
            group_session_metrics_snapshot,
            message_repo,
        ): (
            Arc<dyn SessionRepoPort>,
            Arc<dyn SessionManagementService>,
            Arc<dyn GroupSessionMetricsSnapshotPort>,
            Arc<dyn MessageRepoPort>,
        ) = {
            let env = crate::env::resolve_env();
            info!(env = %env, db_plugin = %db_kind, "DB-backed session and message storage initialized");
            let session_repo = match db_kind {
                DbPluginKind::LocalSqlite => {
                    Arc::new(MySqlSessionStore::sqlite(db_plugin.clone(), env.clone()))
                }
                DbPluginKind::Mysql => Arc::new(MySqlSessionStore::new(db_plugin.clone(), env.clone())),
                DbPluginKind::External(provider) => {
                    panic!(
                        "external database plugin '{}' has no session store wiring",
                        provider
                    )
                }
            };
            let message_repo: Arc<dyn MessageRepoPort> = match db_kind {
                DbPluginKind::LocalSqlite => {
                    Arc::new(MySqlMessageStore::sqlite(db_plugin.clone(), env))
                }
                DbPluginKind::Mysql => Arc::new(MySqlMessageStore::new(db_plugin.clone(), env)),
                DbPluginKind::External(provider) => {
                    panic!(
                        "external database plugin '{}' has no message store wiring",
                        provider
                    )
                }
            };
            let session_management: Arc<dyn SessionManagementService> = Arc::new(
                SessionManagementServiceImpl::new(session_repo.clone(), group_repo.clone())
                    .with_bot_runtime(bot_runtime_for_session.clone()),
            );
            (
                session_repo.clone() as Arc<dyn SessionRepoPort>,
                session_management,
                session_repo as Arc<dyn GroupSessionMetricsSnapshotPort>,
                message_repo,
            )
        };
        let bot_run_context: Arc<dyn BotRunContextPort> =
            Arc::new(bcs_message_flow::MemoryBotRunContextStore::new());
        let group_message_history = create_group_message_history_service(
            sessions.clone(),
            bot_registry.clone(),
            bot_delivery.clone(),
            Arc::clone(&bot_connections),
            provider_transport.clone(),
            message_repo.clone(),
            session_repo.clone(),
            cutoff_timestamp,
            manager_worker_cutoff_timestamp,
            config.message_history.new_participant_visible_limit,
            config.message_history.default_page_limit,
            config.message_history.max_page_limit,
        );
        let a2a_run_store = Arc::new(bcs_message_flow::a2a_chat::ChatRunStore::with_capacity(
            config.async_chat_run_max_entries,
        ));
        let a2a_run_port = Arc::new(crate::http_adapter::BootstrapRunChannelPort {
            run_channels: run_channels.clone(),
        });
        let metrics = crate::metrics::MetricsRuntime::install(&config)?;
        let a2a_chat_impl = Arc::new(
            A2aChat::new_with_run_ports(
                bot_delivery.clone(),
                a2a_run_store,
                config.async_chat_run_timeout_ms,
                bot_registry.clone(),
                friend_svc.clone(),
                a2a_run_port.clone(),
                a2a_run_port.clone(),
            )
            .with_organization(organization_core.clone())
            .with_interceptors(interceptors.clone())
            .with_run_lifecycle_hook(direct_chat_run_lifecycle_hook(metrics.as_ref()))
            .with_bot_run_context(bot_run_context.clone()),
        );
        let a2a_chat: Arc<dyn A2aChatService> = a2a_chat_impl.clone();
        let a2a_chat_runs: Arc<dyn A2aChatRunService> = a2a_chat_impl.clone();
        let a2a_chat_runs = maybe_wrap_a2a_chat_runs(&config, a2a_chat_runs);
        let direct_chat_run_snapshot: Arc<dyn DirectChatRunSnapshotPort> = a2a_chat_impl;
        let use_cases = build_use_case_bundle(
            &config,
            bot_registry.clone(),
            bot_core_arc.clone(),
            organization_core.clone(),
            bot_connections.clone() as Arc<dyn bcs_service_api::BotConnectionControlPort>,
            sessions.clone(),
            proposals.clone(),
            friend_svc.clone(),
            friend_request_svc.clone(),
            relation_svc.clone(),
            fuse_client.clone(),
            fusion.clone(),
            bot_delivery.clone(),
            frontend_delivery.clone(),
            group_message_history.clone(),
            session_management.clone(),
            bot_run_context.clone(),
            user_directory.clone(),
            Some(message_repo.clone()),
            outbound_url_guard.clone(),
            provider_stream_gray_list.clone(),
        );
        let (message_flow, channel_slot) = create_message_flow_services(
            bot_registry.clone(),
            sessions.clone(),
            router.clone(),
            bot_delivery.clone(),
            frontend_delivery.clone(),
            config.max_group_messages,
            interceptors.clone(),
            session_management.clone(),
            bot_run_context.clone(),
            use_cases.system_message.clone(),
            Some(message_repo.clone()),
            provider_stream_gray_list.clone(),
            Arc::new(AdminInvocationTerminalObserver::new(
                admin_invocation_runs.clone(),
                outbound_url_guard.clone(),
            )),
        );
        frontend_connections
            .set_bot_query(use_cases.bot_query.clone())
            .await;

        let judge_evaluator = create_judge_evaluator(&config, &extensions)?;
        let collaboration_runtime: Arc<dyn bcs_service_api::CollaborationRuntimeService> = {
            let env = crate::env::resolve_env();
            info!(env = %env, db_plugin = %db_kind, "DB-backed collaboration storage initialized");
            let collaboration_store = match db_kind {
                DbPluginKind::LocalSqlite => {
                    Arc::new(MySqlCollaborationStore::sqlite(db_plugin.clone(), env))
                }
                DbPluginKind::Mysql => {
                    Arc::new(MySqlCollaborationStore::new(db_plugin.clone(), env))
                }
                DbPluginKind::External(provider) => {
                    panic!(
                        "external database plugin '{}' has no collaboration store wiring",
                        provider
                    )
                }
            };
            Arc::new(
                CollaborationRuntime::new(
                    collaboration_store.clone(),
                    collaboration_store.clone(),
                    collaboration_store.clone(),
                    collaboration_store,
                    sessions.clone(),
                    session_management.clone(),
                    bot_delivery.clone(),
                    judge_evaluator,
                )
                .with_bot_registry(bot_registry.clone())
                .with_callback_url_guard(outbound_url_guard.clone())
                .with_frontend_delivery(frontend_delivery.clone()),
            )
        };

        // Build services bundle
        let message_flow = maybe_wrap_message_flow(&config, message_flow);
        provider_transport.set_ingest(message_flow.clone(), bot_run_context.clone());
        let channel_repos = if channel_bridge_enabled(&config) {
            channel_repos_with_storage(&infrastructure_plugins).await?
        } else {
            memory_channel_repos(None)
        };
        let channel_runtime = build_channel_runtime(
            &config,
            channel_slot,
            channel_repos,
            session_repo.clone(),
            message_flow.clone(),
            use_cases.system_message.clone(),
            collaboration_runtime.clone(),
            sessions.clone(),
            bot_registry.clone(),
        )?;
        let channel_service = channel_runtime.service.clone();
        register_channel_lifecycles(&lifecycle, &channel_runtime.lifecycles);
        let provider_bot_events: Arc<dyn ProviderBotEventService> = Arc::new(
            ProviderBotEvents::new(
                provider_bot_core.clone(),
                bot_run_context.clone(),
                message_flow.clone(),
            )
            .with_collaboration_runtime(collaboration_runtime.clone()),
        );
        let services = ServicesBuilder::default()
            .registry(bot_registry.clone())
            .group(sessions)
            .routing(router)
            .fusion(fusion)
            .proposal(proposals)
            .friend(friend_svc)
            .relation(relation_svc)
            .bot_delivery(bot_delivery)
            .bot_run_context(bot_run_context)
            .frontend_delivery(frontend_delivery)
            .message_flow(message_flow)
            .group_message_history(group_message_history)
            .a2a_chat(a2a_chat)
            .a2a_chat_runs(a2a_chat_runs)
            .collaboration_runtime(collaboration_runtime)
            .collaboration_templates(build_collaboration_template_service_with_storage(
                &config,
                &infrastructure_plugins,
                config.llm.is_enabled() || extensions.llm_provider.is_some(),
            )?)
            .actor_directory(use_cases.actor_directory)
            .friend_use_cases(use_cases.friend_use_cases)
            .human_actors(use_cases.human_actors)
            .bot_onboarding(use_cases.bot_onboarding)
            .bot_query(use_cases.bot_query)
            .bot_management(use_cases.bot_management)
            .bot_runtime(use_cases.bot_runtime)
            .bot_discovery(use_cases.bot_discovery)
            .provider_core(provider_core)
            .provider_bot_core(provider_bot_core)
            .provider_management(provider_management)
            .organization(organization_core)
            .organization_management(organization_management)
            .provider_bot_events(provider_bot_events)
            .group_management(maybe_wrap_group_management(&config, use_cases.group_management))
            .group_query(use_cases.group_query)
            .workbench_sessions(use_cases.workbench_sessions)
            .group_proposals(use_cases.group_proposals)
            .group_fusion(use_cases.group_fusion)
            .system_message(use_cases.system_message)
            .session_management(session_management.clone())
            .channel(channel_service.clone())
            .secret(default_bootstrap_secret_service())
            .build()
            .expect("services must be fully wired");

        // Start timeout scanner for service-invocation sessions
        let _timeout_handle = crate::timeout_scanner::spawn_with_url_guard(
            services.session_management.clone(),
            services.group.clone(),
            crate::timeout_scanner::DEFAULT_SCAN_INTERVAL,
            outbound_url_guard.clone(),
        );
        let _state_machine_timeout_handle = crate::state_machine_timeout_scanner::spawn(
            services.collaboration_runtime.clone(),
            crate::state_machine_timeout_scanner::DEFAULT_SCAN_INTERVAL,
            crate::state_machine_timeout_scanner::DEFAULT_BATCH_SIZE,
            crate::state_machine_timeout_scanner::DEFAULT_TIMEOUT_GRACE_MS,
        );

        let auth_config = crate::auth_wiring::resolve_auth_config(
            &config.auth,
            crate::config_loader::Environment::resolve().as_str(),
        );
        let user_identity_port = match infrastructure_plugins.db() {
            Some(db) => Some(crate::identity_wiring::db_user_identity_port(
                infrastructure_plugins.db_kind(),
                db,
            )),
            None => Some(crate::identity_wiring::memory_user_identity_port()),
        };
        let auth_chain = Arc::new(crate::auth_wiring::try_build_auth_chain_with_factories(
            &auth_config,
            bot_registry.clone(),
            user_identity_port.clone(),
            &extensions.auth_plugin_factories,
        )
        .map_err(crate::BcsError::InvalidConfig)?);
        let state = Arc::new(BcsServerState {
            config: config.clone(),
            services,
            run_channels,
            bot_connections,
            frontend_connections,
            frontend_run_channels,
            coordination_processed: Arc::new(Mutex::new(std::collections::HashMap::new())),
            leader_election,
            lifecycle,
            fuse_client,
            provider_credentials: provider_repos.provider_credentials.clone(),
            provider_stream_gray_list,
            channel_http_ingress: channel_runtime.http_ingress.clone(),
            group_metrics_snapshot,
            group_session_metrics_snapshot,
            bot_metrics_snapshot,
            direct_chat_run_snapshot,
            metrics,
            auth_chain,
            auth_config,
            user_identity_port,
            outbound_url_guard,
            admin_invocation_runs,
        });

        Ok(Self { config, state })
    }

    /// Build the OAuth `/auth/*` router when OAuth is configured.
    ///
    /// Returns `None` (routes absent → 404) unless OAuth is configured with a
    /// non-empty `jwt_secret`, a shared `UserIdentityPort` exists, and at least
    /// one provider is configured. Provider creds and `cookie_secure`/`env`/
    /// `jwt_secret` come from the resolved `auth_config` (see
    /// `auth_wiring::resolve_auth_config`). Currently only `google` is wired.
    fn build_oauth_router(&self) -> Option<Router> {
        // `auth_config.oauth` is the resolved form: present only when a
        // non-empty jwt_secret was configured (I6 gate lives in resolve).
        let resolved = self.state.auth_config.oauth.as_ref()?;
        let raw = self.config.auth.oauth.as_ref()?;
        let user_port = self.state.user_identity_port.clone()?;

        if resolved.jwt_secret.is_empty() {
            warn!("[auth.oauth] jwt_secret is empty; /auth/* not mounted");
            return None;
        }

        let base = resolved.base_url.trim();
        if !(base.starts_with("http://") || base.starts_with("https://")) {
            warn!(
                base_url = %resolved.base_url,
                "[auth.oauth] base_url must be an http(s) URL; /auth/* not mounted"
            );
            return None;
        }

        let mut providers: std::collections::HashMap<
            String,
            Arc<dyn bcs_auth_api::OAuthProvider>,
        > = std::collections::HashMap::new();

        // Build every configured provider instance via the composition-root
        // factory. A misconfigured provider (unknown kind / empty client_id) is
        // an operator error: fail fast at startup rather than silently dropping
        // it and surfacing a runtime 404.
        for (name, cfg) in &raw.providers {
            match crate::auth_wiring::build_oauth_provider(name, cfg) {
                Ok(provider) => {
                    providers.insert(name.clone(), provider);
                }
                Err(e) => {
                    panic!("Invalid OAuth provider configuration: {e}");
                }
            }
        }

        if providers.is_empty() {
            warn!("[auth.oauth] present but no OAuth providers configured; /auth/* not mounted");
            return None;
        }

        let route_state = Arc::new(bcs_http::oauth::OAuthRouteState::new(
            &resolved.jwt_secret,
            user_port,
            providers,
            resolved.clone(),
        ));

        info!(
            providers = ?route_state.providers.keys().collect::<Vec<_>>(),
            cookie_secure = resolved.cookie_secure,
            env = %resolved.env,
            "Mounting OAuth /auth/* routes"
        );
        Some(bcs_http::oauth::routes(route_state))
    }

    /// Build the Axum router.
    async fn build_router(&self) -> Router {
        let api_router = bcs_http::router::build_router(
            crate::http_adapter::build_http_app_state(Arc::clone(&self.state)).await,
        );

        let mut router = Router::new()
            // WebSocket endpoint for frontend clients (via gateway)
            .route(bcs_ws::web::FRONTEND_WS_ENDPOINT, get(ws_upgrade_handler))
            // WebSocket for bot connections
            .route(bcs_ws::bot::BOT_WS_ENDPOINT, get(bot_ws_handler));

        if let Some(metrics) = &self.state.metrics {
            router = router.route(&metrics.endpoint_path, get(metrics_handler));
        }

        let mut router = router.with_state(Arc::clone(&self.state)).merge(api_router);

        if let Some(oauth_router) = self.build_oauth_router() {
            router = router.merge(oauth_router);
        }

        let allowed_origins = Arc::new(
            self.config
                .cors
                .allowed_origins
                .iter()
                .cloned()
                .collect::<std::collections::HashSet<_>>(),
        );

        router
            .layer(middleware::from_fn_with_state(
                Arc::clone(&self.state),
                http_metrics_middleware,
            ))
            .layer(middleware::from_fn(debug_middleware))
            .layer(CatchPanicLayer::custom(
                |_: Box<dyn std::any::Any + Send>| {
                    let body = serde_json::json!({
                        "error": "Internal server error",
                        "status": 500
                    });
                    axum::response::Response::builder()
                        .status(axum::http::StatusCode::INTERNAL_SERVER_ERROR)
                        .header("content-type", "application/json")
                        .body(axum::body::Body::from(body.to_string()))
                        .unwrap()
                },
            ))
            .layer(TraceLayer::new_for_http())
            .layer(
                CorsLayer::new()
                    .allow_origin(AllowOrigin::predicate(move |origin, _| {
                        origin
                            .to_str()
                            .is_ok_and(|origin| allowed_origins.contains(origin))
                    }))
                    .allow_methods(AllowMethods::mirror_request())
                    .allow_headers(AllowHeaders::mirror_request())
                    .allow_credentials(true),
            )
    }

    async fn initialize_lifecycle(&self) -> Result<()> {
        self.state
            .lifecycle
            .lock()
            .await
            .initialize_all()
            .await
            .map_err(|error| {
                crate::BcsError::InvalidConfig(format!(
                    "service lifecycle initialize failed: {error}"
                ))
            })
    }

    /// Run the server with graceful shutdown support.
    pub async fn run(self) -> Result<()> {
        let addr: SocketAddr = format!("{}:{}", self.config.bind, self.config.port)
            .parse()
            .map_err(|e| crate::BcsError::InvalidConfig(format!("Invalid address: {}", e)))?;

        self.initialize_lifecycle().await?;

        // Spawn async chat-run TTL cleanup loop.
        {
            let a2a_chat = self.state.services.a2a_chat.clone();
            let bot_run_context = self.state.services.bot_run_context.clone();
            let retention_ms = self.config.async_chat_run_retention_ms;
            tokio::spawn(async move {
                let mut ticker = tokio::time::interval(std::time::Duration::from_secs(10));
                ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
                loop {
                    ticker.tick().await;
                    let now_ms = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_millis() as u64;
                    match a2a_chat.cleanup_expired(now_ms, retention_ms).await {
                        Ok((expired, dropped)) => {
                            if !expired.is_empty() || !dropped.is_empty() {
                                info!(
                                    expired = expired.len(),
                                    dropped = dropped.len(),
                                    "chat_run: cleanup_expired"
                                );
                            }
                        }
                        Err(err) => {
                            warn!(error = %err, "chat_run: cleanup_expired failed");
                        }
                    }
                    let removed_contexts = bot_run_context
                        .cleanup_expired(now_ms, retention_ms)
                        .await;
                    if removed_contexts > 0 {
                        info!(
                            removed = removed_contexts,
                            "bot_run_context: cleanup_expired"
                        );
                    }
                }
            });
        }

        let app = self.build_router().await;

        info!(
            bind = %self.config.bind,
            port = self.config.port,
            bots_base_dir = %self.config.bots_base_dir.display(),
            "Bot Coordination Service starting"
        );

        let listener = tokio::net::TcpListener::bind(addr)
            .await
            .map_err(crate::BcsError::IoError)?;

        let shutdown_lifecycle = self.state.lifecycle.clone();
        let final_lifecycle = self.state.lifecycle.clone();
        let shutdown_metrics = self.state.metrics.clone();
        let final_metrics = self.state.metrics.clone();

        axum::serve(
            listener,
            app.into_make_service_with_connect_info::<std::net::SocketAddr>(),
        )
            .with_graceful_shutdown(async move {
                use tokio::signal::unix::{signal, SignalKind};

                // Wait for shutdown signal (Ctrl+C or SIGTERM from kill)
                let mut sigterm = signal(SignalKind::terminate()).ok();
                let mut sigint = signal(SignalKind::interrupt()).ok();

                tokio::select! {
                    _ = tokio::signal::ctrl_c() => {}
                    _ = async { if let Some(ref mut s) = sigterm { s.recv().await } else { std::future::pending().await } } => {}
                    _ = async { if let Some(ref mut s) = sigint { s.recv().await } else { std::future::pending().await } } => {}
                }

                info!("Shutdown signal received, gracefully shutting down...");

                if let Err(error) = shutdown_lifecycle.lock().await.shutdown_all().await {
                    warn!(error = %error, "service lifecycle shutdown failed");
                }
                if let Some(metrics) = shutdown_metrics {
                    metrics.shutdown().await;
                }
            })
            .await
            .map_err(|e| crate::BcsError::InvalidConfig(e.to_string()))?;

        if let Err(error) = final_lifecycle.lock().await.shutdown_all().await {
            warn!(error = %error, "service lifecycle shutdown failed");
        }
        if let Some(metrics) = final_metrics {
            metrics.shutdown().await;
        }

        info!("Bot Coordination Service stopped");
        Ok(())
    }

    /// Run the server on a random port and return the bound address.
    /// This is useful for integration tests.
    #[cfg(any(test, feature = "test-utils"))]
    pub async fn run_on_random_port(
        self,
    ) -> Result<(std::net::SocketAddr, tokio::task::JoinHandle<Result<()>>)> {
        let addr: SocketAddr = format!("{}:0", self.config.bind)
            .parse()
            .map_err(|e| crate::BcsError::InvalidConfig(format!("Invalid address: {}", e)))?;

        self.initialize_lifecycle().await?;

        let app = self.build_router().await;

        let listener = tokio::net::TcpListener::bind(addr)
            .await
            .map_err(crate::BcsError::IoError)?;

        let bound_addr = listener.local_addr().map_err(crate::BcsError::IoError)?;
        let lifecycle = self.state.lifecycle.clone();
        let metrics = self.state.metrics.clone();

        let handle = tokio::spawn(async move {
            let result = axum::serve(
                listener,
                app.into_make_service_with_connect_info::<std::net::SocketAddr>(),
            )
                .await
                .map_err(|e| crate::BcsError::InvalidConfig(e.to_string()));
            if let Err(error) = lifecycle.lock().await.shutdown_all().await {
                warn!(error = %error, "service lifecycle shutdown failed");
            }
            if let Some(metrics) = metrics {
                metrics.shutdown().await;
            }
            result
        });

        Ok((bound_addr, handle))
    }

    /// Run the server on a random port and return the shared state for integration tests.
    #[cfg(any(test, feature = "test-utils"))]
    pub async fn run_on_random_port_with_state(
        self,
    ) -> Result<(
        std::net::SocketAddr,
        tokio::task::JoinHandle<Result<()>>,
        Arc<BcsServerState>,
    )> {
        let state = self.state.clone();
        let (addr, handle) = self.run_on_random_port().await?;
        Ok((addr, handle, state))
    }
}

struct AgentCredentialBackfill {
    registry: Arc<dyn BotRegistryCoreService>,
}

#[async_trait::async_trait]
impl bcs_ws::bot::AgentCredentialBackfillPort for AgentCredentialBackfill {
    async fn backfill(
        &self,
        bot_uuid: &str,
        agent_token: Option<String>,
        agent_code_header: Option<String>,
    ) {
        let agent_token_str = match &agent_token {
            Some(t) if !t.is_empty() => t.clone(),
            _ => return,
        };

        // agent_token: always write to memory only (not DB) for security
        self.registry
            .add_bot_info(bot_uuid, "agent_token", agent_token_str.clone())
            .await;

        let agent_code = agent_code_header.filter(|s| !s.is_empty());

        let Some(agent_code) = agent_code else {
            warn!(
                bot_uuid = %bot_uuid,
                "no agent_code resolved, skipping backfill"
            );
            return;
        };

        // agent_code: persist to DB
        if let Some(mut caps) = self.registry.load_from_storage(bot_uuid).await {
            if caps.agent_code.as_deref() == Some(&agent_code) {
                debug!(
                    bot_uuid = %bot_uuid,
                    "agent_code unchanged, skipping write"
                );
                return;
            }
            caps.agent_code = Some(agent_code.clone());
            if let Err(e) = self.registry.save_to_storage(bot_uuid, &caps).await {
                warn!(
                    bot_uuid = %bot_uuid,
                    error = %e,
                    "failed to backfill agent_code"
                );
            } else {
                let _ = self.registry.register(bot_uuid.to_string(), caps).await;
                info!(
                    bot_uuid = %bot_uuid,
                    agent_code = %agent_code,
                    "agent_code backfilled"
                );
            }
        } else {
            warn!(
                bot_uuid = %bot_uuid,
                "bot not yet onboarded, skipping agent credential backfill"
            );
        }
    }
}

/// `GroupDispatchContextPort` backed by the core `GroupCoreService`. Lives in
/// the composition root, so it may depend on the core trait the WS adapter is
/// not allowed to name.
struct CoreGroupDispatchContext {
    group: Arc<dyn GroupCoreService>,
}

#[async_trait::async_trait]
impl bcs_service_api::GroupDispatchContextPort for CoreGroupDispatchContext {
    async fn participants(&self, group_id: &str) -> Option<Vec<bcs_service_api::Participant>> {
        self.group
            .get(group_id)
            .await
            .map(|group| group.participants)
    }
}

fn bot_ws_dispatch_state(state: &Arc<BcsServerState>) -> Arc<bcs_ws::bot::BotDispatchState> {
    Arc::new(bcs_ws::bot::BotDispatchState {
        bot_runtime: state.services.bot_runtime.clone(),
        message_flow: state.services.message_flow.clone(),
        collaboration_runtime: state.services.collaboration_runtime.clone(),
        bot_run_context: state.services.bot_run_context.clone(),
        bot_connections: state.bot_connections.clone(),
        run_channels: state.run_channels.clone(),
        task_callback: None,
        session_management: state.services.session_management.clone(),
        group_dispatch: Arc::new(CoreGroupDispatchContext {
            group: state.services.group.clone(),
        }),
        callback_dispatch: Arc::new(bcs_callback::SessionCallbackDispatcher::new(
            state.services.group.clone(),
            state.outbound_url_guard.clone(),
        )),
        system_message: Some(state.services.system_message.clone()),
        coordination_processed: state.coordination_processed.clone(),
        agent_credential_backfill: Some(Arc::new(AgentCredentialBackfill {
            registry: state.services.registry.clone(),
        })),
    })
}

fn web_ws_dispatch_state(state: &Arc<BcsServerState>) -> Arc<bcs_ws::web::WebDispatchState> {
    Arc::new(bcs_ws::web::WebDispatchState {
        message_flow: state.services.message_flow.clone(),
        workbench_sessions: state.services.workbench_sessions.clone(),
        frontend_connections: state.frontend_connections.clone(),
        run_channels: state.frontend_run_channels.clone(),
    })
}

struct NoopWsLifecycleInstrumentationHook;

#[async_trait::async_trait]
impl WsLifecycleInstrumentationHook for NoopWsLifecycleInstrumentationHook {
    async fn accepted(&self, _peer: WsPeer, _endpoint: &'static str) {}

    async fn registered(&self, _peer: WsPeer, _endpoint: &'static str) {}

    async fn error(&self, _peer: WsPeer, _endpoint: &'static str, _kind: WsErrorKind) {}

    async fn closed(
        &self,
        _peer: WsPeer,
        _endpoint: &'static str,
        _close_reason: WsCloseReason,
        _duration: std::time::Duration,
    ) {
    }
}

struct NoopDirectChatRunLifecycleHook;

#[async_trait::async_trait]
impl DirectChatRunLifecycleHook for NoopDirectChatRunLifecycleHook {
    async fn event(
        &self,
        _event: DirectChatRunEvent,
        _result: MetricsResult,
        _client_kind: DirectChatClientKind,
        _reason: DirectChatRunReason,
    ) {
    }
}

fn ws_lifecycle_hook(_state: &Arc<BcsServerState>) -> Arc<dyn WsLifecycleInstrumentationHook> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if let Some(metrics) = &_state.metrics {
            return metrics.clone();
        }
    }

    Arc::new(NoopWsLifecycleInstrumentationHook)
}

fn direct_chat_run_lifecycle_hook(
    _metrics: Option<&Arc<crate::metrics::MetricsRuntime>>,
) -> Arc<dyn DirectChatRunLifecycleHook> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if let Some(metrics) = _metrics {
            return Arc::new(crate::metrics::MetricsDirectChatRunLifecycleHook::new(
                metrics.env.clone(),
            ));
        }
    }

    Arc::new(NoopDirectChatRunLifecycleHook)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn judge_llm_provider_selection_uses_openai_compatible_type() {
        let mut config = BcsConfig::default();
        assert_eq!(
            select_judge_llm_provider(&config).unwrap(),
            JudgeLlmProviderKind::None
        );

        config.llm.provider_type = LlmProviderType::OpenAiCompatible;
        assert_eq!(
            select_judge_llm_provider(&config).unwrap(),
            JudgeLlmProviderKind::OpenAiCompatible
        );
    }

    #[test]
    #[should_panic(expected = "standalone BCS server cannot use mysql")]
    fn standalone_template_service_rejects_mysql_storage() {
        let mut config = BcsConfig::default();
        config.collaboration.templates.storage_type = CollaborationTemplateStorageKind::Mysql;

        let _service = build_standalone_collaboration_template_service(&config);
    }

    #[test]
    fn configured_missing_channel_provider_fails_startup() {
        let mut config = BcsConfig::default();
        config.channels.enabled = true;
        config.channels.providers.insert(
            "missing-provider".to_string(),
            bcs_config_api::ChannelProviderConfig {
                enabled: true,
                ..Default::default()
            },
        );

        let result = build_configured_channel_providers(
            &config,
            Arc::new(MemoryChannelBindingRepo::new()),
        );

        assert!(matches!(
            result,
            Err(crate::BcsError::InvalidConfig(message))
                if message.contains("missing-provider")
        ));
    }

    #[tokio::test]
    async fn chat_run_events_registered_by_http_are_visible_to_frontend_fallback() {
        let server = BcsServer::new(BcsConfig::default());
        let (tx, mut rx) = tokio::sync::mpsc::channel(1);

        server
            .state
            .run_channels
            .register(
                "http-run".to_string(),
                "bcs-cli:caller:http-run".to_string(),
                tx,
                Some("http-chat-async".to_string()),
                None,
            )
            .await;

        let result = server
            .state
            .services
            .frontend_delivery
            .publish(bcs_service_api::FrontendDeliveryCommand {
                target: bcs_service_api::FrontendDeliveryTarget::Group {
                    group_id: "bcs-cli:caller:http-run".to_string(),
                },
                event_json: r#"{"type":"event","event":"chat"}"#.to_string(),
                delivery_kind: bcs_service_api::FrontendDeliveryKind::WorkbenchEvent,
                run_fallback: Some(bcs_service_api::RunFallbackDelivery {
                    run_id: "bot-generated-run".to_string(),
                    session_id: "bcs-cli:caller:http-run".to_string(),
                    event_json: r#"{"type":"event","event":"chat.event"}"#.to_string(),
                }),
                exclude_conn_id: None,
            })
            .await
            .unwrap();

        assert_eq!(result.delivered, 1);
        assert_eq!(
            rx.recv().await,
            Some(r#"{"type":"event","event":"chat.event"}"#.to_string())
        );
    }

    #[tokio::test]
    async fn bot_ws_dispatch_state_reuses_coordination_dedup_store_for_reconnects() {
        let server = BcsServer::new(BcsConfig::default());

        let first = bot_ws_dispatch_state(&server.state);
        let second = bot_ws_dispatch_state(&server.state);

        assert!(Arc::ptr_eq(
            &first.coordination_processed,
            &second.coordination_processed
        ));
    }
}

/// WebSocket upgrade handler for frontend clients (AI Workbench).
///
/// Bind the calling Human's actor id (`human_{staff_no}`) into the WS session
/// at the HTTP upgrade boundary. The bound id is computed once here from the
/// configured auth chain and then immutable for the lifetime of the session;
/// clients cannot rewrite their identity by sending a different
/// sender in subsequent frames.
///
/// If the cookie is missing / invalid / staff_no is empty, the session has
/// `bound_actor_id = None`; Workbench `connect` and `chat.send` then reject
/// request frames with `unauthorized`.
async fn ws_upgrade_handler(
    ws: WebSocketUpgrade,
    headers: axum::http::HeaderMap,
    State(state): State<Arc<BcsServerState>>,
) -> Response {
    // Resolve identity BEFORE on_upgrade: once the connection switches to
    // WebSocket frames the original HTTP headers are gone, so cookie
    // extraction must happen here in the request scope.
    let bound_actor_id = match state.auth_chain.authenticate(&headers).await {
        Ok(result) => result
            .principal
            .and_then(|p| p.user_id)
            .filter(|s| !s.is_empty())
            .map(|staff_no| format!("human_{}", staff_no)),
        Err(_) => None,
    };

    if let Some(ref actor_id) = bound_actor_id {
        info!(actor_id = %actor_id, "WS upgrade: bound human actor id");
    } else {
        debug!("WS upgrade: anonymous session (no staff_no in cookie)");
    }

    ws.on_upgrade(move |socket| {
        let ws_state = web_ws_dispatch_state(&state);
        let metrics_hook = ws_lifecycle_hook(&state);
        bcs_ws::web::handle_client_connection(socket, ws_state, bound_actor_id, metrics_hook)
    })
}

/// WebSocket handler for bot connections.
///
/// Token validation is handled by the bot.connect frame after upgrade:
/// - Valid token: reconnect to existing bot
/// - Invalid/missing token: treated as new bot, assigned new bot_id + token
///
/// The Authorization header and x-agentclaw-agent-code are captured before
/// the upgrade so they can be backfilled into bot_info after a successful
/// bot.connect handshake.
async fn bot_ws_handler(
    State(state): State<Arc<BcsServerState>>,
    headers: axum::http::HeaderMap,
    ws: WsUpgrade,
) -> Response {
    let agent_token = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());
    let agent_code_header = headers
        .get("x-agentclaw-agent-code")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());

    ws.on_upgrade(move |socket| {
        let ws_state = bot_ws_dispatch_state(&state);
        let metrics_hook = ws_lifecycle_hook(&state);
        bcs_ws::bot::handle_connection(
            socket,
            ws_state,
            metrics_hook,
            agent_token,
            agent_code_header,
        )
    })
}

// ============================================================================
// Error handling
// ============================================================================

impl IntoResponse for crate::BcsError {
    fn into_response(self) -> axum::response::Response {
        let (status, message) = match &self {
            Self::SessionNotFound(id) => {
                (StatusCode::NOT_FOUND, format!("Session not found: {}", id))
            }
            // Issue #4 (E.6 regression 2026-04-29): GroupNotFound previously
            // fell through to the catch-all `_ => 500` arm, so `GET /groups/{id}`
            // for any non-existent (or just-deleted) group returned HTTP 500
            // instead of 404. The error body already says "Group not found",
            // so the only fix needed is the status-code mapping.
            Self::GroupNotFound(id) => (StatusCode::NOT_FOUND, format!("Group not found: {}", id)),
            Self::BotNotFound(id) => (StatusCode::NOT_FOUND, format!("Bot not found: {}", id)),
            Self::BotNotRegistered(id) => (
                StatusCode::NOT_FOUND,
                format!("Bot '{}' is not registered", id),
            ),
            Self::BotAlreadyConnected(id) => (
                StatusCode::CONFLICT,
                format!("Bot '{}' already has an active WebSocket connection", id),
            ),
            Self::BotNotConnected(id) => (
                StatusCode::NOT_FOUND,
                format!("Bot '{}' is not connected via WebSocket", id),
            ),
            Self::InvalidRequest(msg) => {
                (StatusCode::BAD_REQUEST, format!("Invalid request: {}", msg))
            }
            Self::NotFriends { bot, driver } => (
                StatusCode::FORBIDDEN,
                format!(
                    "Bot '{}' is protected and not a friend of '{}'",
                    bot, driver
                ),
            ),
            Self::BotPrivate(id) => (
                StatusCode::FORBIDDEN,
                format!(
                    "Bot '{}' is in private mode and cannot participate in collaboration network",
                    id
                ),
            ),
            Self::InvalidSessionToken => (
                StatusCode::UNAUTHORIZED,
                "Invalid or expired session token".to_string(),
            ),
            Self::Forbidden(msg) => (StatusCode::FORBIDDEN, msg.clone()),
            Self::Unauthorized(msg) => (StatusCode::UNAUTHORIZED, msg.clone()),
            Self::WsProtocolError(msg) => (
                StatusCode::BAD_REQUEST,
                format!("WebSocket protocol error: {}", msg),
            ),
            Self::InvalidFrameFormat(msg) => (
                StatusCode::BAD_REQUEST,
                format!("Invalid frame format: {}", msg),
            ),
            Self::ProposalNotFound(id) => (
                StatusCode::NOT_FOUND,
                format!("Proposal not found or expired: {}", id),
            ),
            Self::BotDirectoryNotFound(path) => (
                StatusCode::NOT_FOUND,
                format!("Bot directory not found: {}", path),
            ),
            Self::InvalidConfig(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            Self::InvalidOperation(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            Self::TooManyGroups(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            Self::TooManyMembers(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            Self::TooManyMessages(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            _ => (StatusCode::INTERNAL_SERVER_ERROR, self.to_string()),
        };

        let variant = self.variant_name();
        if status.is_server_error() {
            tracing::error!(status = %status.as_u16(), error_type = %variant, backtrace = %std::backtrace::Backtrace::force_capture(), "{message}");
        } else if status.is_client_error() && status != StatusCode::NOT_FOUND {
            tracing::warn!(status = %status.as_u16(), error_type = %variant, "{message}");
        }

        let body = Json(serde_json::json!({
            "error": message,
            "status": status.as_u16()
        }));

        (status, body).into_response()
    }
}
