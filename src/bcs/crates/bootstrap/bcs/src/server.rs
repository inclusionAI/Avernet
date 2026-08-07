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

use crate::Result;
use crate::auth_wiring::AuthPluginFactory;
use crate::config::{
    BcsConfig, CollaborationTemplateStorageKind, GatewayPrincipalConfig, GroupSessionWsConfig,
    LlmConfig, LlmProviderType,
};
use crate::lifecycle::LifecycleOrchestrator;
use crate::plugins::{
    DbPluginKind, InfrastructurePlugins, LeaderElectionRegistration,
    build_registered_channel_provider, build_registered_leader_election,
    build_registered_llm_provider, build_registered_security_gateway,
    build_registered_user_directory,
};
use bcs_app_bot::{BotServiceConfig, BotServiceImpl};
use bcs_app_group::{GroupServiceConfig, GroupServiceImpl};
use bcs_app_invitation::{InvitationFriendshipServiceConfig, InvitationFriendshipServiceImpl};
use bcs_app_session::{
    GroupSessionConnectionServiceImpl, SessionServiceConfig, SessionServiceImpl,
};
use bcs_api_http::{ApiState, PrincipalVerifier};
use bcs_bot::{Bot, BotControlPlaneCore, BotCore, ProviderBotEvents, ProviderCore, ProviderManagement};
use bcs_api_http::v1::gateway_principal::{
    GatewayPrincipalTokenVerifier, GatewayPrincipalTrust,
};
use bcs_bot_store::{DbProviderStore, MemoryBotRepo, MemoryProviderStore, PersistentBotRepo};
use bcs_channel::{BcsChannelService, ChannelServiceInboundSink};
use bcs_channel_api::{ChannelHttpIngressRegistry, ChannelProvider, ChannelProviderRegistry};
use bcs_channel_store::{
    DbChannelBindingStore, DbConversationSessionStore, DbHumanInputRequestStore,
    DbImParticipantStore, MemoryChannelBindingRepo, MemoryConversationSessionRepo,
    MemoryHumanInputRequestRepo, MemoryImParticipantRepo,
};
use bcs_collaboration_runtime::CollaborationRuntime;
use bcs_collaboration_store::{
    DbCollaborationTemplateRepo, MemoryCollaborationStore, MySqlCollaborationStore,
};
use bcs_collaboration_template::{CollaborationTemplateServiceImpl, FileCollaborationTemplateRepo};
use bcs_db_api::DbSqlFlavor;
use bcs_domain::{NewMessage, SenderType};
use bcs_friend::{FriendCore, FriendRequestCore};
use bcs_friend_store::{
    DbFriendRequestStore, DbFriendStore, MemoryFriendRepo, MemoryFriendRequestRepo,
};
use bcs_fuse_client::FuseClient;
use bcs_fusion::{FuseClientService, FuseWorkerProfileService, LocalFusionService};
use bcs_group::{
    GroupConfig, GroupCore, GroupManagement, GroupManagementWithRuntimeCleanup,
};
use bcs_group_store::{MemoryGroupRepo, MySqlGroupStore};
use bcs_http::{
    admin_invocation_terminal::AdminInvocationTerminalObserver,
    state::AdminInvocationStore,
};
use bcs_judge::{LlmJudgeService, NoopJudgeEvaluator};
use bcs_jwt::GroupSessionJwtService;
use bcs_leader_election::StandaloneLeaderElection;
use bcs_llm_api::LlmChatCompletionPort;
use bcs_llm_anthropic::AnthropicLlmClient;
use bcs_llm_openai_compatible::OpenAiCompatibleLlmClient;
use bcs_message::MessageService;
use bcs_message_flow::{A2aChat, BcsGroupFusion, BcsGroupMessageHistory, BcsMessageFlow};
use bcs_message_store::{MemoryMessageRepo, MySqlMessageStore};
use bcs_organization::{OrganizationCore, OrganizationManagement};
use bcs_organization_store::{DbOrganizationStore, MemoryOrganizationRepo};
use bcs_proposal::{GroupProposalUseCases, GroupProposalUseCasesConfig, ProposalStore};
use bcs_relation::RelationCore;
use bcs_relation_store::DbRelationStore;
use bcs_route_security::OutboundUrlGuard;
use bcs_routing::MessageRouter;
use bcs_routing::security::SecurityInterceptor;
use bcs_security_gateway_api::SecurityGatewayPort;
use bcs_security_gateway_local::NoopSecurityGateway;
use bcs_secret_local::InMemorySecretAccess;
use bcs_service_api::application::v1::GroupSessionConnectionService;
use bcs_service_api::interceptor::InterceptorChain;
use bcs_service_api::lifecycle::ServiceLifecycle;
use bcs_service_api::port::{GroupSessionTokenPort, SecretAccessPort};
use bcs_service_api::{
    A2aChatRunService, A2aChatService, BotActor, BotDeliveryPort, BotDeliveryTarget,
    BotControlPlaneRepoPort, BotRegistryCoreService, CallerContext,
    BotMetricsSnapshotPort, BotRunContextPort, BotTerminalObserverPort, ChannelBindingCleanupPort,
    ChannelService, CollaborationTemplateService,
    DirectChatClientKind, DirectChatRunEvent, DirectChatRunLifecycleHook,
    DirectChatRunReason, DirectChatRunSnapshotPort, FrontendDeliveryPort, GroupCoreService,
    GroupHistoryBotRequestPort, GroupManagementService, GroupMessageHistoryService,
    GroupMetricsSnapshotPort, GroupRepoPort, GroupSessionMetricsSnapshotPort,
    HumanInputReadyEvent, JudgeEvaluatorPort, LeaderElectionPort, MessageFlowService, MetricsResult,
    OrganizationCoreService, OrganizationManagementService, OrganizationRepoPort,
    FriendCoreService, FriendRequestCoreService, InviteService, ProviderBotBindingRepoPort,
    ProviderBotCoreService, ProviderBotEventService, ProviderCoreService,
    ProviderCredentialRepoPort, ProviderManagementService, ProviderRepoPort, ProviderStreamGrayList,
    ServiceResult, SessionChannelDeliveryOutcome, SessionChannelOutboundPort,
    RelationCoreService, SessionManagementService, StateMachineResultPublishCommand,
    StateMachineResultPublisherPort, SystemMessageService,
    StateMachineTerminalEvent, WebSendCommand, WsCloseReason, WsErrorKind,
    WsLifecycleInstrumentationHook, WsPeer, RoutingCoreService,
    port::repo::{
        ChannelBindingRepoPort, ConversationSessionRepoPort, HumanInputRequestRepoPort,
        ImParticipantRepoPort, MessageRepoPort, SessionRepoPort,
    },
};
use bcs_services_container::{Services, ServicesBuilder};
use bcs_session::{SessionManagementServiceImpl, SessionManagementWithRuntimeCleanup};
use bcs_session_store::{MemorySessionRepo, MySqlSessionStore};
use bcs_system_message::{
    SystemMessageDispatcherImpl, SystemMessageServiceImpl,
    producers::bot_hidden_notice::BotHiddenNoticeProducer,
    producers::bot_joined::BotJoinedMessageProducer, producers::bot_left::BotLeftMessageProducer,
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
/// At builder time we do not perform async provider initialization. We seed
/// every `Services` instance with a Noop so the builder's required-field
/// invariant is satisfied; the configured backend is swapped in alongside
/// `HttpAppState` construction.
fn default_bootstrap_secret_service() -> Arc<dyn bcs_service_api::SecretService> {
    use bcs_secret::DefaultSecretService;
    use bcs_secret_local::NoopSecretAccess;
    Arc::new(DefaultSecretService::new(Arc::new(NoopSecretAccess)))
}

/// Build the session-file workspace service for the bootstrap `Services` bundle.
///
/// `db` selects the repo backend:
/// - `Some(db)` → `MySqlSessionFileStore::with_flavor(db, env, db_flavor)`; the
///   flavor MUST accompany `db` (it tells the store which SQL dialect to use
///   when projecting `created_at`/`updated_at` from `gmt_create`/`gmt_modified`).
/// - `None` → `MemorySessionFileRepo::new()` (standalone/dev mode).
///
/// The `env` passed here MUST match the env the repo writes into the `env`
/// column of `bcs_session_files`; the service uses the same env to scope
/// object keys via [`bcs_session_file::authz::derive_key`].
///
/// Share token secret is independent of `invite.token_secret`: if
/// `session_files.share.token_secret` is unset, bootstrap logs a warning and
/// generates a random 32-byte secret that does NOT survive a restart (prod
/// must set it explicitly). Mirrors the invite secret fallback contract.
async fn build_session_files_service(
    config: &BcsConfig,
    env: String,
    db: Option<Arc<dyn bcs_db_api::DbPlugin>>,
    db_flavor: Option<DbSqlFlavor>,
    session_repo: Arc<dyn SessionRepoPort>,
) -> Arc<dyn bcs_service_api::application::session_files::SessionFileService> {
    use bcs_service_api::port::repo::SessionFileRepoPort;
    use bcs_session_file::{SessionFileServiceConfig, SessionFileServiceImpl};
    use bcs_session_file_store::{MemorySessionFileRepo, MySqlSessionFileStore};
    use bcs_storage_api::StoragePlugin;
    use bcs_storage_api::factory::{StorageBackendConfig, StoragePluginFactory};
    use bcs_storage_local::LocalStoragePluginFactory;
    use bcs_storage_baas::BaasStoragePluginFactory;

    // Backend-agnostic storage assembly: select a factory by storage_backend,
    // build the plugin from the backend pass-through table. server.rs is
    // otherwise ignorant of the backend roster (adding OSS/NAS later is one
    // factory arm here + its crate). See design-baas-plugin §「落地前置改造」.

    // Prefer the configured external endpoint, then bind:port, mirroring
    // `proposal_base_url` above.
    let bcs_base_url = config
        .bcs_endpoint
        .clone()
        .unwrap_or_else(|| format!("http://{}:{}", config.bind, config.port));

    let factory: Arc<dyn StoragePluginFactory> = match config.session_files.storage_backend.as_str() {
        "local" => Arc::new(LocalStoragePluginFactory),
        "baas" => Arc::new(BaasStoragePluginFactory),
        other => panic!("unknown storage_backend '{other}'"),
    };

    let backend_cfg = StorageBackendConfig {
        env: env.clone(),
        max_file_size: config.session_files.max_file_size,
        multipart_threshold: config.session_files.multipart_threshold,
        share_link_ttl: config.session_files.share_link_ttl,
        bcs_base_url: bcs_base_url.clone(),
        bots_base_dir: config.bots_base_dir.display().to_string(),
        backend: toml_table_to_json_map(&config.session_files.backend),
    };
    let storage: Arc<dyn StoragePlugin> = factory
        .build(&backend_cfg)
        .await
        .expect("storage backend build failed at bootstrap");

    let file_repo: Arc<dyn SessionFileRepoPort> = match db {
        Some(db) => {
            let flavor = db_flavor.expect("`db` present implies `db_flavor` present");
            Arc::new(MySqlSessionFileStore::with_flavor(db, env.clone(), flavor))
        }
        None => Arc::new(MemorySessionFileRepo::new()),
    };

    let share_secret = config
        .session_files
        .share
        .token_secret
        .as_deref()
        .map(|s| s.as_bytes().to_vec())
        .unwrap_or_else(|| {
            warn!(
                "session_files.share.token_secret not configured — generating random \
                 32-byte secret (share tokens will not survive restart)"
            );
            (0..32).map(|_| fastrand::u8(..)).collect()
        });

    Arc::new(SessionFileServiceImpl::new(SessionFileServiceConfig {
        storage,
        repo: file_repo,
        session_repo,
        env,
        max_size: config.session_files.max_file_size,
        multipart_threshold: config.session_files.multipart_threshold,
        bcs_base_url,
        share_secret,
        share_default_ttl: config.session_files.share.default_ttl_seconds,
        share_link_ttl: config.session_files.share_link_ttl,
        share_base_url: config.session_files.share.share_base_url.clone(),
    }))
}

/// Blocking bridge for sync entry points (`Default::default()` and
/// `new_with_outbound_url_guards`) that cannot `.await`.  Spawns a
/// dedicated OS thread to hold the temp tokio runtime so this works even
/// when the calling thread already runs a tokio runtime (e.g. tests).
/// The production path (`new_with_infrastructure`) is already async and
/// calls [`build_session_files_service`] directly, without this overhead.
fn build_session_files_service_blocking(
    config: &BcsConfig,
    env: String,
    db: Option<Arc<dyn bcs_db_api::DbPlugin>>,
    db_flavor: Option<DbSqlFlavor>,
    session_repo: Arc<dyn SessionRepoPort>,
) -> Arc<dyn bcs_service_api::application::session_files::SessionFileService> {
    std::thread::scope(|s| {
        s.spawn(|| {
            tokio::runtime::Runtime::new()
                .expect("temp runtime for storage build")
                .block_on(build_session_files_service(
                    config,
                    env,
                    db,
                    db_flavor,
                    session_repo,
                ))
        })
        .join()
        .expect("storage build thread panicked")
    })
}

/// Convert a `toml::Table` (config pass-through) into a `serde_json::Map`.
fn toml_table_to_json_map(table: &toml::Table) -> serde_json::Map<String, serde_json::Value> {
    let mut out = serde_json::Map::new();
    for (k, v) in table {
        out.insert(k.clone(), toml_value_to_json(v));
    }
    out
}

fn toml_value_to_json(v: &toml::Value) -> serde_json::Value {
    match v {
        toml::Value::String(s) => serde_json::Value::String(s.clone()),
        toml::Value::Integer(i) => serde_json::Value::Number((*i).into()),
        toml::Value::Float(f) => serde_json::json!(f),
        toml::Value::Boolean(b) => serde_json::Value::Bool(*b),
        toml::Value::Table(t) => serde_json::Value::Object(toml_table_to_json_map(t)),
        toml::Value::Array(a) => serde_json::Value::Array(a.iter().map(toml_value_to_json).collect()),
        toml::Value::Datetime(d) => serde_json::Value::String(d.to_string()),
    }
}

/// Spawn the Pending-sweep background task for the session-file workspace.
///
/// Mirrors the timeout/token-expiry scanner pattern: a tokio interval task
/// that calls `sweep_expired_pending()` every 300s, logs results, and
/// swallows errors so a transient backend hiccup never tears down the loop.
fn spawn_session_files_pending_sweep(
    service: Arc<dyn bcs_service_api::application::session_files::SessionFileService>,
) {
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(300));
        interval.tick().await; // consume the immediate first tick
        loop {
            interval.tick().await;
            match service.sweep_expired_pending().await {
                Ok(n) if n > 0 => info!(swept = n, "session file pending sweep"),
                Ok(_) => {}
                Err(e) => warn!(error = ?e, "session file pending sweep error"),
            }
        }
    });
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
type SessionChannelOutboundSlot = Arc<OnceLock<Arc<dyn SessionChannelOutboundPort>>>;

struct DeferredSessionChannelOutbound {
    slot: SessionChannelOutboundSlot,
}

#[async_trait]
impl SessionChannelOutboundPort for DeferredSessionChannelOutbound {
    async fn validate_human_input_channel(
        &self,
        group_id: &str,
        channel_type: &str,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        let Some(outbound) = self.slot.get() else {
            return Ok(SessionChannelDeliveryOutcome::NotApplicable);
        };
        outbound
            .validate_human_input_channel(group_id, channel_type)
            .await
    }

    async fn publish_human_input_ready(
        &self,
        event: HumanInputReadyEvent,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        let Some(outbound) = self.slot.get() else {
            return Ok(SessionChannelDeliveryOutcome::NotApplicable);
        };
        outbound.publish_human_input_ready(event).await
    }

    async fn publish_state_machine_terminal(
        &self,
        event: StateMachineTerminalEvent,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        let Some(outbound) = self.slot.get() else {
            return Ok(SessionChannelDeliveryOutcome::NotApplicable);
        };
        outbound.publish_state_machine_terminal(event).await
    }
}

fn deferred_session_channel_outbound() -> (
    SessionChannelOutboundSlot,
    Arc<dyn SessionChannelOutboundPort>,
) {
    let slot = Arc::new(OnceLock::new());
    let outbound: Arc<dyn SessionChannelOutboundPort> =
        Arc::new(DeferredSessionChannelOutbound { slot: slot.clone() });
    (slot, outbound)
}

struct MessageFlowStateMachineResultPublisher {
    message_flow: Arc<dyn MessageFlowService>,
    message_repo: Arc<dyn MessageRepoPort>,
}

impl MessageFlowStateMachineResultPublisher {
    fn new(
        message_flow: Arc<dyn MessageFlowService>,
        message_repo: Arc<dyn MessageRepoPort>,
    ) -> Self {
        Self {
            message_flow,
            message_repo,
        }
    }
}

#[async_trait]
impl StateMachineResultPublisherPort for MessageFlowStateMachineResultPublisher {
    async fn publish_state_machine_result(
        &self,
        cmd: StateMachineResultPublishCommand,
    ) -> ServiceResult<()> {
        let idempotency_key = format!("state-machine-result:{}", cmd.run_id);
        self.message_repo
            .append_message(NewMessage {
                group_id: cmd.group_id.clone(),
                session_id: cmd.session_id.clone(),
                sender_id: cmd.sender_bot_id.clone(),
                sender_type: SenderType::Bot,
                message_type: "chat".to_string(),
                content: serde_json::Value::String(cmd.content.clone()),
                client_msg_id: Some(idempotency_key.clone()),
                owner_bot_id: None,
                created_at: now_ms(),
                run_id: cmd.run_id.clone(),
            })
            .await
            .map_err(|error| {
                bcs_service_api::ServiceError::InternalError(format!(
                    "persist state-machine result before delivery: {error}"
                ))
            })?;
        self.message_flow
            .handle_web_send(WebSendCommand {
                caller: CallerContext::Bot(BotActor {
                    bot_uuid: cmd.sender_bot_id.clone(),
                }),
                group_id: cmd.group_id,
                session_id: Some(cmd.session_id),
                from_actor_id: cmd.sender_bot_id,
                from_name: None,
                message: cmd.content,
                mentions: Vec::new(),
                attachments: None,
                thinking: None,
                idempotency_key: Some(idempotency_key),
                source_im_message_id: None,
                sender_conn_id: None,
                provider_bypass_headers: Vec::new(),
            })
            .await?;
        Ok(())
    }
}

#[derive(Default)]
struct DeferredChannelBindingCleanupPort {
    service: OnceLock<Arc<dyn ChannelBindingCleanupPort>>,
}

impl DeferredChannelBindingCleanupPort {
    fn set(&self, service: Arc<dyn ChannelBindingCleanupPort>) {
        if self.service.set(service).is_err() {
            warn!("channel binding cleanup port already initialized");
        }
    }
}

#[async_trait]
impl ChannelBindingCleanupPort for DeferredChannelBindingCleanupPort {
    async fn delete_bindings_for_group(
        &self,
        group_id: &str,
    ) -> bcs_service_api::ServiceResult<u64> {
        let service = self.service.get().ok_or_else(|| {
            bcs_service_api::ServiceError::InternalError(
                "channel binding cleanup port is not initialized".to_string(),
            )
        })?;
        service.delete_bindings_for_group(group_id).await
    }
}

type ChannelRepos = (
    Arc<dyn ChannelBindingRepoPort>,
    Arc<dyn ConversationSessionRepoPort>,
    Arc<dyn ImParticipantRepoPort>,
    Arc<dyn HumanInputRequestRepoPort>,
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
        Err(
            bcs_service_api::application::channel::ChannelUseCaseError::InvalidParams(
                "channel bridge is disabled".to_string(),
            ),
        )
    }

    async fn list_bindings(
        &self,
    ) -> std::result::Result<
        Vec<bcs_domain::ChannelBinding>,
        bcs_service_api::application::channel::ChannelUseCaseError,
    > {
        Ok(Vec::new())
    }

    async fn list_bindings_by_target(
        &self,
        _target: bcs_domain::BindingTarget,
        _channel_type: Option<bcs_domain::ChannelType>,
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
    let env = bcs_config::resolve_env_str();
    match data_dir {
        Some(dir) => (
            Arc::new(MemoryChannelBindingRepo::with_data_dir(dir.clone(), env)),
            Arc::new(MemoryConversationSessionRepo::with_data_dir(dir.clone())),
            Arc::new(MemoryImParticipantRepo::with_data_dir(dir.clone())),
            Arc::new(MemoryHumanInputRequestRepo::with_data_dir(dir)),
        ),
        None => (
            Arc::new(MemoryChannelBindingRepo::new(env)),
            Arc::new(MemoryConversationSessionRepo::new()),
            Arc::new(MemoryImParticipantRepo::new()),
            Arc::new(MemoryHumanInputRequestRepo::new()),
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
    let env = bcs_config::resolve_env_str();
    match infrastructure_plugins.db_kind() {
        DbPluginKind::LocalSqlite => {
            info!("Initializing SQLite channel storage");
            Ok((
                Arc::new(DbChannelBindingStore::sqlite(db_plugin.clone(), env)),
                Arc::new(DbConversationSessionStore::sqlite(db_plugin.clone())),
                Arc::new(DbImParticipantStore::sqlite(db_plugin.clone())),
                Arc::new(DbHumanInputRequestStore::sqlite(db_plugin)),
            ))
        }
        DbPluginKind::Mysql => {
            info!("Initializing MySQL channel storage");
            Ok((
                Arc::new(DbChannelBindingStore::mysql(db_plugin.clone(), env)),
                Arc::new(DbConversationSessionStore::mysql(db_plugin.clone())),
                Arc::new(DbImParticipantStore::mysql(db_plugin.clone())),
                Arc::new(DbHumanInputRequestStore::mysql(db_plugin)),
            ))
        }
        DbPluginKind::External(provider) => Err(crate::BcsError::StorageInitError(format!(
            "external database plugin '{provider}' has no channel storage wiring"
        ))),
    }
}

#[allow(clippy::too_many_arguments)]
fn build_channel_runtime(
    config: &BcsConfig,
    channel_slot: ChannelSlot,
    channel_binding_cleanup: Arc<DeferredChannelBindingCleanupPort>,
    session_channel_outbound_slot: SessionChannelOutboundSlot,
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
        channel_binding_cleanup.set(Arc::new(
            bcs_service_api::NoopChannelBindingCleanupPort,
        ));
        return Ok(ChannelRuntime {
            service: Arc::new(DisabledChannelService),
            http_ingress: None,
            lifecycles: Vec::new(),
        });
    }

    let (
        channel_bindings,
        channel_conversations,
        channel_im_participants,
        human_input_requests,
    ) = channel_repos;
    let providers = build_configured_channel_providers(config, channel_bindings.clone())?;
    let provider_registry = Arc::new(
        ChannelProviderRegistry::new(providers.clone())
            .map_err(|error| crate::BcsError::InvalidConfig(error.to_string()))?,
    );
    let channel_service_impl = Arc::new(BcsChannelService::new(
        channel_bindings,
        channel_conversations,
        channel_im_participants,
        human_input_requests,
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
    let channel_service_port: Arc<dyn ChannelService> = channel_service_impl.clone();
    let session_channel_outbound: Arc<dyn SessionChannelOutboundPort> =
        channel_service_impl.clone();
    channel_binding_cleanup.set(channel_service_impl);
    if channel_slot.set(channel_service_port.clone()).is_err() {
        warn!("message-flow channel slot already initialized");
    }
    if session_channel_outbound_slot
        .set(session_channel_outbound)
        .is_err()
    {
        warn!("state-machine channel outbound slot already initialized");
    }
    let sink: Arc<dyn bcs_channel_api::ChannelInboundSink> =
        Arc::new(ChannelServiceInboundSink::new(channel_service_port.clone()));
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
        service: channel_service_port,
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
            Ok(
                build_file_collaboration_template_service_with_judge_templates(
                    config,
                    judge_templates_enabled,
                ),
            )
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

    /// Gateway-signed Principal verifier retained for the V1 HTTP adapter composition.
    pub gateway_principal_verifier: Arc<dyn PrincipalVerifier>,

    /// Invite-token HMAC secret resolved once for every HTTP surface.
    pub invite_token_secret: Vec<u8>,

    /// Completed V1 HTTP adapter state assembled from the same runtime services as legacy HTTP.
    pub openapi_v1: ApiState,

    /// Configured secret source used for the session-bound Workbench credential.
    pub group_session_secret_access: Arc<dyn SecretAccessPort>,

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
            .field(
                "group_session_metrics_snapshot",
                &"<GroupSessionMetricsSnapshotPort>",
            )
            .field("bot_metrics_snapshot", &"<BotMetricsSnapshotPort>")
            .field("direct_chat_run_snapshot", &"<DirectChatRunSnapshotPort>")
            .field("metrics", &"<MetricsRuntime>")
            .field("auth_chain", &"<AuthPluginChain>")
            .field("auth_config", &self.auth_config)
            .field("gateway_principal_verifier", &"<PrincipalVerifier>")
            .field("invite_token_secret", &"<redacted>")
            .field("openapi_v1", &"<ApiState>")
            .field("group_session_secret_access", &"<SecretAccessPort>")
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
    organization_candidates: Arc<dyn bcs_service_api::OrganizationCandidateReadPort>,
}

fn memory_provider_repos() -> ProviderRepoBundle {
    let store = Arc::new(MemoryProviderStore::new());
    ProviderRepoBundle {
        provider_repo: store.clone(),
        provider_credentials: store.clone(),
        provider_bindings: store.clone(),
        organization_candidates: store,
    }
}

fn db_sql_flavor(db_kind: &DbPluginKind) -> DbSqlFlavor {
    match db_kind {
        DbPluginKind::LocalSqlite => DbSqlFlavor::Sqlite,
        DbPluginKind::Mysql => DbSqlFlavor::Mysql,
        DbPluginKind::External(provider) => {
            panic!(
                "external database plugin '{}' has no SQL flavor wiring",
                provider
            )
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
            panic!(
                "external database plugin '{}' has no provider store wiring",
                provider
            )
        }
    };
    ProviderRepoBundle {
        provider_repo: store.clone(),
        provider_credentials: store.clone(),
        provider_bindings: store.clone(),
        organization_candidates: store,
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
        provider_repos.organization_candidates.clone(),
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

    let provider_config = directory
        .providers
        .get(provider)
        .cloned()
        .unwrap_or_default();
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

fn gateway_principal_signing_key(material: Option<&str>) -> crate::Result<&str> {
    material
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| {
            crate::BcsError::InvalidConfig(
                "Gateway Principal signing key is required".to_string(),
            )
        })
}

fn build_gateway_principal_verifier(
    config: &GatewayPrincipalConfig,
    material: Option<&str>,
) -> crate::Result<Arc<dyn PrincipalVerifier>> {
    config.validate().map_err(crate::BcsError::InvalidConfig)?;
    let signing_key = gateway_principal_signing_key(material)?;
    let trust = GatewayPrincipalTrust::new(
        config.issuer.clone(),
        config.audience.clone(),
        config.key_id.clone(),
    )
    .map_err(|error| crate::BcsError::InvalidConfig(error.to_string()))?;
    let verifier = GatewayPrincipalTokenVerifier::new(signing_key.as_bytes(), trust)
        .map_err(|error| crate::BcsError::InvalidConfig(error.to_string()))?;
    Ok(Arc::new(verifier))
}

fn build_gateway_principal_verifier_from_process(
    config: &GatewayPrincipalConfig,
) -> crate::Result<Arc<dyn PrincipalVerifier>> {
    let material = std::env::var(&config.signing_key_env).ok();
    build_gateway_principal_verifier(config, material.as_deref())
}

async fn build_gateway_principal_verifier_from_secret_access(
    config: &GatewayPrincipalConfig,
    secret_access: Arc<dyn SecretAccessPort>,
) -> crate::Result<Arc<dyn PrincipalVerifier>> {
    config.validate().map_err(crate::BcsError::InvalidConfig)?;
    let secret_name = config
        .signing_key_secret
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty());

    if let Some(secret_name) = secret_name {
        let record = secret_access.get_secret(secret_name).await.map_err(|_| {
            crate::BcsError::InvalidConfig(format!(
                "Gateway Principal signing key secret '{secret_name}' is required"
            ))
        })?;
        return build_gateway_principal_verifier(config, Some(record.value.as_str()));
    }

    build_gateway_principal_verifier_from_process(config)
}

const GROUP_SESSION_WS_TEST_SIGNING_KEY: &str =
    "test-only-group-session-key-at-least-32-bytes";

fn group_session_test_secret_access(config: &GroupSessionWsConfig) -> Arc<dyn SecretAccessPort> {
    Arc::new(InMemorySecretAccess::with_entries([(
        config.signing_key_secret.trim().to_string(),
        String::new(),
        GROUP_SESSION_WS_TEST_SIGNING_KEY.to_string(),
    )]))
}

fn build_secret_access_blocking(config: &BcsConfig) -> crate::Result<Arc<dyn SecretAccessPort>> {
    std::thread::scope(|scope| {
        scope
            .spawn(|| {
                tokio::runtime::Runtime::new()
                    .expect("temp runtime for secret provider build")
                    .block_on(crate::http_adapter::build_secret_access(config))
            })
            .join()
            .expect("secret provider build thread panicked")
    })
}

async fn build_group_session_token_port(
    config: &GroupSessionWsConfig,
    secret_access: Arc<dyn SecretAccessPort>,
) -> crate::Result<Arc<dyn GroupSessionTokenPort>> {
    let secret_name = config.signing_key_secret.trim();
    let secret = secret_access
        .get_secret(secret_name)
        .await
        .map_err(|_| {
            crate::BcsError::InvalidConfig(format!(
                "group_session_ws.signing_key_secret '{secret_name}' is required"
            ))
        })?;
    let tokens = GroupSessionJwtService::new(&secret.value).map_err(|_| {
        crate::BcsError::InvalidConfig(format!(
            "group_session_ws.signing_key_secret '{secret_name}' must resolve to non-empty material"
        ))
    })?;
    Ok(Arc::new(tokens))
}

async fn build_group_session_connection_service(
    sessions: Arc<dyn bcs_service_api::application::v1::SessionService>,
    config: &GroupSessionWsConfig,
    secret_access: Arc<dyn SecretAccessPort>,
) -> crate::Result<Arc<dyn GroupSessionConnectionService>> {
    let tokens = build_group_session_token_port(config, secret_access).await?;
    Ok(Arc::new(GroupSessionConnectionServiceImpl::new(
        sessions, tokens,
    )))
}

fn resolve_invite_token_secret(config: &BcsConfig) -> Vec<u8> {
    config
        .invite
        .token_secret
        .as_deref()
        .map(str::as_bytes)
        .map(ToOwned::to_owned)
        .unwrap_or_else(|| {
            tracing::warn!(
                "invite.token_secret not configured — generating random secret (tokens will not survive restart)"
            );
            (0..32).map(|_| fastrand::u8(..)).collect()
        })
}

#[allow(clippy::too_many_arguments)]
fn build_openapi_v1_state(
    config: &BcsConfig,
    invite_token_secret: Vec<u8>,
    control_plane_repo: Arc<dyn BotControlPlaneRepoPort>,
    provider_repos: &ProviderRepoBundle,
    registry: Arc<dyn BotRegistryCoreService>,
    groups: Arc<dyn GroupCoreService>,
    friends: Arc<dyn FriendCoreService>,
    friend_requests: Arc<dyn FriendRequestCoreService>,
    relation: Arc<dyn RelationCoreService>,
    sessions: Arc<dyn SessionManagementService>,
    group_management: Arc<dyn GroupManagementService>,
    collaboration_runtime: Arc<dyn bcs_service_api::CollaborationRuntimeService>,
    session_repo: Arc<dyn SessionRepoPort>,
    message_repo: Arc<dyn MessageRepoPort>,
    system_message: Arc<dyn SystemMessageService>,
    principal_verifier: Arc<dyn PrincipalVerifier>,
) -> ApiState {
    let relation_env = crate::env::resolve_env();
    let control_plane = Arc::new(BotControlPlaneCore::new(
        control_plane_repo,
        provider_repos.provider_repo.clone(),
        provider_repos.provider_bindings.clone(),
    ));
    let bot_service = Arc::new(BotServiceImpl::new(
        control_plane,
        registry.clone(),
        friends.clone(),
        BotServiceConfig {
            env: relation_env.clone(),
        },
    ));
    let group_service = Arc::new(
        GroupServiceImpl::new(
            groups.clone(),
            registry.clone(),
            friends.clone(),
            relation.clone(),
            sessions.clone(),
            group_management,
            GroupServiceConfig {
                relation_env: relation_env.clone(),
            },
        )
        .with_collaboration_runtime(collaboration_runtime),
    );
    let session_service = Arc::new(SessionServiceImpl::new(
        sessions.clone(),
        groups.clone(),
        registry.clone(),
        friends.clone(),
        relation,
        session_repo,
        message_repo,
        SessionServiceConfig { relation_env },
    ));
    let invitation_groups = groups.clone();
    let invitation_sessions = sessions.clone();
    let invite: Arc<dyn InviteService> = Arc::new(bcs_group::application::invite::InviteServiceImpl {
        registry: registry.clone(),
        group: groups,
        session: sessions,
        system_message,
        token_secret: invite_token_secret.clone(),
        default_ttl_seconds: config.invite.default_ttl_seconds,
        base_url: config.invite.base_url.clone(),
        group_link_url: config.invite.group_link_url.clone(),
        session_link_url: config.invite.session_link_url.clone(),
    });
    let invitation_service = Arc::new(InvitationFriendshipServiceImpl::new(
        friends,
        friend_requests,
        invitation_groups,
        invitation_sessions,
        registry,
        invite,
        invite_token_secret,
        InvitationFriendshipServiceConfig {
            default_ttl_seconds: config.invite.default_ttl_seconds,
        },
    ));

    ApiState::new(
        group_service,
        session_service.clone(),
        session_service,
        invitation_service.clone(),
        invitation_service,
        principal_verifier,
    )
    .with_bot_service(bot_service)
}

pub(crate) fn gateway_principal_verifier_for_tests() -> Arc<dyn PrincipalVerifier> {
    build_gateway_principal_verifier(
        &GatewayPrincipalConfig::default(),
        Some("test-only-gateway-principal-signing-key"),
    )
    .expect("default Gateway Principal test verifier")
}

#[cfg(test)]
mod gateway_principal_tests {
    use super::*;
    use bcs_secret_local::InMemorySecretAccess;

    fn trust_config() -> crate::config::GatewayPrincipalConfig {
        crate::config::GatewayPrincipalConfig::default()
    }

    #[test]
    fn configured_invite_token_secret_is_preserved() {
        let mut config = BcsConfig::default();
        config.invite.token_secret = Some("configured-invite-secret".to_string());

        assert_eq!(
            resolve_invite_token_secret(&config),
            b"configured-invite-secret"
        );
    }

    #[test]
    fn gateway_principal_material_must_be_explicit_and_non_blank() {
        for material in [None, Some(""), Some("   ")] {
            assert!(matches!(
                gateway_principal_signing_key(material),
                Err(crate::BcsError::InvalidConfig(message))
                    if message.contains("Gateway Principal signing key")
            ));
        }
    }

    #[test]
    fn explicit_gateway_principal_material_is_accepted() {
        assert_eq!(
            gateway_principal_signing_key(Some("explicit-test-key"))
                .expect("explicit material"),
            "explicit-test-key"
        );
    }

    #[tokio::test]
    async fn gateway_principal_signing_key_can_come_from_secret_access() {
        let mut config = trust_config();
        config.signing_key_secret =
            Some("other_manual_teamclawgw_principal_signing_key".to_string());
        let access: Arc<dyn bcs_service_api::port::SecretAccessPort> = Arc::new(
            InMemorySecretAccess::with_entries([(
                "other_manual_teamclawgw_principal_signing_key",
                "teamclawgw".to_string(),
                "mist-test-signing-key".to_string(),
            )]),
        );

        let result = build_gateway_principal_verifier_from_secret_access(&config, access).await;

        if let Err(error) = result {
            let message = error.to_string();
            assert!(!message.contains("mist-test-signing-key"));
            panic!("Mist-backed Gateway Principal signing key must be accepted: {message}");
        }
    }

    #[test]
    fn blank_gateway_principal_trust_or_lookup_config_is_rejected() {
        for field in ["issuer", "audience", "key_id", "signing_key_env"] {
            let mut config = trust_config();
            match field {
                "issuer" => config.issuer = " ".to_string(),
                "audience" => config.audience = " ".to_string(),
                "key_id" => config.key_id = " ".to_string(),
                "signing_key_env" => config.signing_key_env = " ".to_string(),
                _ => unreachable!("known trust config field"),
            }
            assert!(matches!(
                build_gateway_principal_verifier(&config, Some("explicit-test-key")),
                Err(crate::BcsError::InvalidConfig(_))
            ));
        }
    }

    #[tokio::test]
    async fn group_session_websocket_signing_key_is_required_and_non_empty() {
        let missing: Arc<dyn bcs_service_api::port::SecretAccessPort> =
            Arc::new(InMemorySecretAccess::new());
        let config = GroupSessionWsConfig::default();
        let missing_error = match build_group_session_token_port(&config, missing).await {
            Ok(_) => panic!("missing group-session WebSocket key must fail"),
            Err(error) => error,
        };
        assert!(matches!(missing_error, crate::BcsError::InvalidConfig(_)));
        assert!(
            missing_error
                .to_string()
                .contains("group_session_ws.signing_key_secret 'bcn-group-session-ws-jwt' is required")
        );

        let empty: Arc<dyn bcs_service_api::port::SecretAccessPort> = Arc::new(
            InMemorySecretAccess::with_entries([(
                config.signing_key_secret.clone(),
                String::new(),
                "   ".to_string(),
            )]),
        );
        let empty_error = match build_group_session_token_port(&config, empty).await {
            Ok(_) => panic!("empty group-session WebSocket key must fail"),
            Err(error) => error,
        };
        assert!(matches!(empty_error, crate::BcsError::InvalidConfig(_)));
        assert!(!empty_error.to_string().contains("   "));
    }

    #[tokio::test]
    async fn explicit_group_session_websocket_signing_key_is_accepted() {
        let secret_material = "test-only-group-session-key-at-least-32-bytes";
        let config = GroupSessionWsConfig {
            signing_key_secret: "other_manual_teamclawgw_principal_signing_key".to_string(),
        };
        let access: Arc<dyn bcs_service_api::port::SecretAccessPort> = Arc::new(
            InMemorySecretAccess::with_entries([(
                config.signing_key_secret.clone(),
                String::new(),
                secret_material.to_string(),
            )]),
        );

        let result = build_group_session_token_port(&config, access).await;

        if let Err(error) = result {
            let message = error.to_string();
            assert!(!message.contains(secret_material));
            panic!("explicit group-session WebSocket key must be accepted: {message}");
        }
    }
}

impl Default for BcsServerState {
    fn default() -> Self {
        let config = BcsConfig::default();
        let group_session_secret_access = build_secret_access_blocking(&config)
            .expect("Secret provider configuration must be valid");
        let invite_token_secret = resolve_invite_token_secret(&config);
        let gateway_principal_verifier = build_gateway_principal_verifier_from_process(
            &config.gateway_principal,
        )
        .expect("Gateway Principal verifier configuration must be valid");
        let outbound_url_guard = outbound_url_guard_from_config(&config);
        let admin_invocation_runs = Arc::new(AdminInvocationStore::default());
        let provider_repos = memory_provider_repos();
        let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(config.bots_base_dir.clone()));
        let control_plane_repo: Arc<dyn BotControlPlaneRepoPort> = bot_repo.clone();
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
        let user_directory =
            create_user_directory_plugin(&config).expect("default user directory config is valid");
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
        let friend_request_store: Arc<FriendRequestCore> = Arc::new(
            FriendRequestCore::with_repo(
                Arc::new(MemoryFriendRequestRepo::new()),
                friend_store.clone(),
                bot_registry.clone(),
            ),
        );
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
        let frontend_connections = Arc::new(WorkbenchConnectionRegistry::with_bot_query(
            bot_use_cases.clone(),
        ));
        let run_channels = Arc::new(RunChannelManager::new());
        let frontend_run_channels = run_channels.clone();
        let ws_bot_delivery: Arc<dyn BotDeliveryPort> = bot_connections.clone();
        let provider_transport = Arc::new(
            bcs_provider_http::HttpProviderTransport::with_url_guard(outbound_url_guard.clone()),
        );
        let provider_stream_gray_list = create_provider_stream_gray_list(&config);
        let raw_bot_delivery: Arc<dyn BotDeliveryPort> = Arc::new(
            bcs_provider_http::BotTransportMux::new(ws_bot_delivery, provider_transport.clone()),
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
        let session_file_service = build_session_files_service_blocking(
            &config,
            crate::env::resolve_env(),
            None,
            None,
            session_repo.clone(),
        );
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
            session_file_service.clone(),
            config.session_files.share.history_attachment_ttl_seconds,
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
                .register(BotJoinedMessageProducer::new(group_message_history.clone()))
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
        let channel_binding_cleanup = Arc::new(DeferredChannelBindingCleanupPort::default());
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
        .with_channel_binding_cleanup(channel_binding_cleanup.clone())
        .with_outbound_url_guard(outbound_url_guard.clone())
        .with_bot_runtime(bot_use_cases.clone()));
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
        let judge_evaluator: Arc<dyn JudgeEvaluatorPort> = Arc::new(NoopJudgeEvaluator::default());
        let (session_channel_outbound_slot, session_channel_outbound) =
            deferred_session_channel_outbound();
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
            .with_session_channel_outbound(session_channel_outbound)
            .with_result_publisher(Arc::new(
                MessageFlowStateMachineResultPublisher::new(
                    message_flow.clone(),
                    message_repo.clone(),
                ),
            ))
            .with_message_repo(message_repo.clone())
            .with_frontend_delivery(frontend_delivery.clone()),
        );
        let session_management = Arc::new(SessionManagementWithRuntimeCleanup::new(
            session_management.clone(),
            collaboration_runtime.clone(),
        ));
        let group_management = maybe_wrap_group_management(
            &config,
            Arc::new(GroupManagementWithRuntimeCleanup::new(
                group_management_impl.clone(),
                collaboration_runtime.clone(),
            )),
        );
        let openapi_v1 = build_openapi_v1_state(
            &config,
            invite_token_secret.clone(),
            control_plane_repo,
            &provider_repos,
            bot_registry.clone(),
            sessions.clone(),
            friend_store.clone(),
            friend_request_store,
            relation_store.clone(),
            session_management.clone(),
            group_management.clone(),
            collaboration_runtime.clone(),
            session_repo.clone(),
            message_repo.clone(),
            system_message.clone(),
            gateway_principal_verifier.clone(),
        );
        let channel_runtime = build_channel_runtime(
            &config,
            channel_slot,
            channel_binding_cleanup,
            session_channel_outbound_slot,
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
            .session_files(session_file_service)
            .build()
            .expect("services must be fully wired");

        // Start timeout scanner for service-invocation sessions
        let _timeout_handle = crate::timeout_scanner::spawn_with_url_guard(
            services.session_management.clone(),
            services.group.clone(),
            crate::timeout_scanner::DEFAULT_SCAN_INTERVAL,
            outbound_url_guard.clone(),
        );
        // Start JWT token expiry scanner
        let _token_expiry_handle = crate::token_expiry_scanner::spawn(
            bot_connections.clone(),
            services.bot_runtime.clone(),
            crate::token_expiry_scanner::DEFAULT_SCAN_INTERVAL,
        );

        // Start Pending-sweep for session-file workspace
        spawn_session_files_pending_sweep(services.session_files.clone());

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
            gateway_principal_verifier,
            invite_token_secret,
            openapi_v1,
            group_session_secret_access,
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
        let mut config = BcsConfig::default();
        config.bots_base_dir = std::env::temp_dir().join(format!(
            "bcs-default-state-test-{}",
            uuid::Uuid::new_v4()
        ));
        Arc::try_unwrap(BcsServer::new_allowing_private_outbound_for_tests(config).state)
            .expect("test server state has one owner")
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

    let provider_config = election
        .providers
        .get(provider)
        .cloned()
        .unwrap_or_default();

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
    channel_binding_cleanup: Arc<dyn ChannelBindingCleanupPort>,
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
            .register(BotJoinedMessageProducer::new(group_message_history.clone()))
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
    .with_channel_binding_cleanup(channel_binding_cleanup)
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
            chain.set_block_hook(Arc::new(
                crate::metrics::MetricsDeliveryPolicyBlockHook::new(Arc::from(
                    bcs_config::resolve_env_str(),
                )),
            ));
        }
    }

    let sg = &config.security_gateway;
    let provider = sg.provider.trim();
    let gateway: Arc<dyn SecurityGatewayPort> = if provider.is_empty() || provider == "noop" {
        info!(
            provider = "noop",
            dry_run = sg.dry_run,
            "Initializing noop security gateway interceptor"
        );
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
    Anthropic,
}

fn select_judge_llm_provider(config: &BcsConfig) -> crate::Result<JudgeLlmProviderKind> {
    match &config.llm.provider_type {
        LlmProviderType::None => Ok(JudgeLlmProviderKind::None),
        LlmProviderType::OpenAiCompatible => Ok(JudgeLlmProviderKind::OpenAiCompatible),
        LlmProviderType::Anthropic => Ok(JudgeLlmProviderKind::Anthropic),
        LlmProviderType::Other(provider) => Err(crate::BcsError::InvalidConfig(format!(
            "llm.type = '{}' is not available in this binary",
            provider
        ))),
    }
}

fn create_public_judge_evaluator(config: &BcsConfig) -> crate::Result<Arc<dyn JudgeEvaluatorPort>> {
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
        JudgeLlmProviderKind::Anthropic => {
            let llm_config = resolve_llm_config(config);
            let llm_client = AnthropicLlmClient::new(llm_config.clone()).map_err(|error| {
                crate::BcsError::InvalidConfig(format!("invalid llm config: {error}"))
            })?;
            info!(
                model = %llm_config.model,
                base_url = %llm_config.base_url,
                structured_output = ?llm_config.structured_output,
                "Anthropic LLM judge enabled"
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
    fn judge_llm_provider_selection_uses_public_provider_types() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::OpenAiCompatible;

        assert_eq!(
            select_judge_llm_provider(&config).unwrap(),
            JudgeLlmProviderKind::OpenAiCompatible
        );

        config.llm.provider_type = LlmProviderType::Anthropic;
        assert_eq!(
            select_judge_llm_provider(&config).unwrap(),
            JudgeLlmProviderKind::Anthropic
        );
    }

    #[test]
    fn anthropic_llm_provider_requires_api_key() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::Anthropic;
        config.llm.base_url = "https://api.anthropic.com/v1".to_string();
        config.llm.api_key_env = None;
        config.llm.api_key = None;

        let error = match create_judge_evaluator(&config, &BcsServerExtensions::default()) {
            Ok(_) => panic!("anthropic provider without an API key should fail"),
            Err(error) => error,
        };

        assert!(error.to_string().contains("anthropic api_key is required"));
    }

    #[test]
    fn anthropic_llm_provider_builds_judge_evaluator() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::Anthropic;
        config.llm.base_url = "https://api.anthropic.com/v1".to_string();
        config.llm.api_key_env = None;
        config.llm.api_key = Some(Secret::new("anthropic-key".to_string()));

        create_judge_evaluator(&config, &BcsServerExtensions::default())
            .expect("valid anthropic provider should build a judge evaluator");
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
    session_file: Arc<dyn bcs_service_api::application::session_files::SessionFileService>,
    history_attachment_ttl: u64,
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
        session_file,
        cutoff_timestamp,
        manager_worker_cutoff_timestamp,
        new_participant_visible_limit,
        default_page_limit,
        max_page_limit,
        history_attachment_ttl,
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
        let group_session_secret_access = build_secret_access_blocking(&config)
            .expect("Secret provider configuration must be valid");
        let gateway_principal_verifier = std::thread::scope(|scope| {
            scope
                .spawn(|| {
                    tokio::runtime::Runtime::new()
                        .expect("temp runtime for Gateway Principal verifier build")
                        .block_on(build_gateway_principal_verifier_from_secret_access(
                            &config.gateway_principal,
                            group_session_secret_access.clone(),
                        ))
                })
                .join()
                .expect("Gateway Principal verifier build thread panicked")
        })
        .expect("Gateway Principal verifier configuration must be valid");
        Self::new_with_outbound_url_guards(
            config,
            outbound_url_guard.clone(),
            outbound_url_guard.clone(),
            outbound_url_guard,
            group_session_secret_access,
            gateway_principal_verifier,
        )
    }

    pub fn new_allowing_private_outbound_for_tests(config: BcsConfig) -> Self {
        let group_session_secret_access = group_session_test_secret_access(&config.group_session_ws);
        Self::new_with_outbound_url_guards(
            config,
            OutboundUrlGuard::allowing_private_networks_for_tests(),
            OutboundUrlGuard::allowing_private_networks_for_tests(),
            OutboundUrlGuard::allowing_private_networks_for_tests(),
            group_session_secret_access,
            gateway_principal_verifier_for_tests(),
        )
    }

    fn new_with_outbound_url_guards(
        config: BcsConfig,
        provider_webhook_url_guard: OutboundUrlGuard,
        provider_request_url_guard: OutboundUrlGuard,
        callback_url_guard: OutboundUrlGuard,
        group_session_secret_access: Arc<dyn SecretAccessPort>,
        gateway_principal_verifier: Arc<dyn PrincipalVerifier>,
    ) -> Self {
        let invite_token_secret = resolve_invite_token_secret(&config);
        let admin_invocation_runs = Arc::new(AdminInvocationStore::default());
        // Create service implementations (synchronous, in-memory mode)
        let provider_repos = memory_provider_repos();
        let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(config.bots_base_dir.clone()));
        let control_plane_repo: Arc<dyn BotControlPlaneRepoPort> = bot_repo.clone();
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
            bcs_provider_http::BotTransportMux::new(ws_bot_delivery, provider_transport.clone()),
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
        let session_file_service = build_session_files_service_blocking(
            &config,
            crate::env::resolve_env(),
            None,
            None,
            session_repo.clone(),
        );
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
            session_file_service.clone(),
            config.session_files.share.history_attachment_ttl_seconds,
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
        let channel_binding_cleanup = Arc::new(DeferredChannelBindingCleanupPort::default());
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
            channel_binding_cleanup.clone(),
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
        let (session_channel_outbound_slot, session_channel_outbound) =
            deferred_session_channel_outbound();
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
            .with_session_channel_outbound(session_channel_outbound)
            .with_result_publisher(Arc::new(
                MessageFlowStateMachineResultPublisher::new(
                    message_flow.clone(),
                    message_repo.clone(),
                ),
            ))
            .with_message_repo(message_repo.clone())
            .with_frontend_delivery(frontend_delivery.clone()),
        );
        let session_management = Arc::new(SessionManagementWithRuntimeCleanup::new(
            session_management.clone(),
            collaboration_runtime.clone(),
        ));
        let group_management = maybe_wrap_group_management(
            &config,
            Arc::new(GroupManagementWithRuntimeCleanup::new(
                use_cases.group_management,
                collaboration_runtime.clone(),
            )),
        );
        let openapi_v1 = build_openapi_v1_state(
            &config,
            invite_token_secret.clone(),
            control_plane_repo,
            &provider_repos,
            bot_registry.clone(),
            sessions.clone(),
            friend_store.clone(),
            friend_request_store,
            relation_store.clone(),
            session_management.clone(),
            group_management.clone(),
            collaboration_runtime.clone(),
            session_repo.clone(),
            message_repo.clone(),
            use_cases.system_message.clone(),
            gateway_principal_verifier.clone(),
        );

        // Build services bundle
        let message_flow = maybe_wrap_message_flow(&config, message_flow);
        provider_transport.set_ingest(message_flow.clone(), bot_run_context.clone());
        let channel_runtime = build_channel_runtime(
            &config,
            channel_slot,
            channel_binding_cleanup,
            session_channel_outbound_slot,
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
            .organization_management(organization_management)
            .provider_bot_events(provider_bot_events)
            .group_management(group_management)
            .group_query(use_cases.group_query)
            .workbench_sessions(use_cases.workbench_sessions)
            .group_proposals(use_cases.group_proposals)
            .group_fusion(use_cases.group_fusion)
            .system_message(use_cases.system_message)
            .session_management(session_management.clone())
            .channel(channel_service.clone())
            .secret(default_bootstrap_secret_service())
            .session_files(session_file_service)
            .build()
            .expect("services must be fully wired");

        // Start timeout scanner for service-invocation sessions
        let _timeout_handle = crate::timeout_scanner::spawn_with_url_guard(
            services.session_management.clone(),
            services.group.clone(),
            crate::timeout_scanner::DEFAULT_SCAN_INTERVAL,
            callback_url_guard.clone(),
        );
        // Start Pending-sweep for session-file workspace
        spawn_session_files_pending_sweep(services.session_files.clone());

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
            gateway_principal_verifier,
            invite_token_secret,
            openapi_v1,
            group_session_secret_access,
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

        let invite_token_secret = resolve_invite_token_secret(&config);
        let group_session_secret_access = crate::http_adapter::build_secret_access(&config).await?;
        let gateway_principal_verifier = build_gateway_principal_verifier_from_secret_access(
            &config.gateway_principal,
            group_session_secret_access.clone(),
        )
        .await?;
        info!(
            issuer = %config.gateway_principal.issuer,
            audience = %config.gateway_principal.audience,
            key_id = %config.gateway_principal.key_id,
            "Gateway Principal verifier initialized"
        );
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
                        crate::BcsError::StorageInitError(format!("run sqlite migrations: {}", err))
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
        let control_plane_repo: Arc<dyn BotControlPlaneRepoPort> = bot_repo.clone();
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
                    panic!(
                        "external database plugin '{}' has no group store wiring",
                        provider
                    )
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
                    panic!(
                        "external database plugin '{}' has no friend store wiring",
                        provider
                    )
                }
            };
            let friend_store: Arc<dyn bcs_service_api::FriendCoreService> =
                Arc::new(FriendCore::with_repo(friend_repo).with_relation(relation_svc.clone()));
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
                    bot_connections.clone() as Arc<dyn bcs_service_api::BotConnectionControlPort>
                );
        if let Some(user_directory) = user_directory.clone() {
            bot_runtime_for_session = bot_runtime_for_session.with_user_directory(user_directory);
        }
        let bot_runtime_for_session: Arc<dyn bcs_service_api::BotRuntimeConnectionService> =
            Arc::new(bot_runtime_for_session);
        let frontend_connections = Arc::new(WorkbenchConnectionRegistry::new());
        let run_channels = Arc::new(RunChannelManager::new());
        let frontend_run_channels = run_channels.clone();
        let ws_bot_delivery: Arc<dyn BotDeliveryPort> = bot_connections.clone();
        let provider_transport = Arc::new(
            bcs_provider_http::HttpProviderTransport::with_url_guard(outbound_url_guard.clone()),
        );
        let provider_stream_gray_list = create_provider_stream_gray_list(&config);
        let raw_bot_delivery: Arc<dyn BotDeliveryPort> = Arc::new(
            bcs_provider_http::BotTransportMux::new(ws_bot_delivery, provider_transport.clone()),
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
        let (session_repo, session_management, group_session_metrics_snapshot, message_repo): (
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
                DbPluginKind::Mysql => {
                    Arc::new(MySqlSessionStore::new(db_plugin.clone(), env.clone()))
                }
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
        let session_file_service = build_session_files_service(
            &config,
            crate::env::resolve_env(),
            infrastructure_plugins.db(),
            Some(db_flavor),
            session_repo.clone(),
        )
        .await;
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
            session_file_service.clone(),
            config.session_files.share.history_attachment_ttl_seconds,
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
        let channel_binding_cleanup = Arc::new(DeferredChannelBindingCleanupPort::default());
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
            channel_binding_cleanup.clone(),
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
        let (session_channel_outbound_slot, session_channel_outbound) =
            deferred_session_channel_outbound();
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
                .with_session_channel_outbound(session_channel_outbound)
                .with_result_publisher(Arc::new(
                    MessageFlowStateMachineResultPublisher::new(
                        message_flow.clone(),
                        message_repo.clone(),
                    ),
                ))
                .with_message_repo(message_repo.clone())
                .with_frontend_delivery(frontend_delivery.clone()),
            )
        };
        let session_management = Arc::new(SessionManagementWithRuntimeCleanup::new(
            session_management.clone(),
            collaboration_runtime.clone(),
        ));
        let group_management = maybe_wrap_group_management(
            &config,
            Arc::new(GroupManagementWithRuntimeCleanup::new(
                use_cases.group_management,
                collaboration_runtime.clone(),
            )),
        );
        let openapi_v1 = build_openapi_v1_state(
            &config,
            invite_token_secret.clone(),
            control_plane_repo,
            &provider_repos,
            bot_registry.clone(),
            sessions.clone(),
            friend_svc.clone(),
            friend_request_svc,
            relation_svc.clone(),
            session_management.clone(),
            group_management.clone(),
            collaboration_runtime.clone(),
            session_repo.clone(),
            message_repo.clone(),
            use_cases.system_message.clone(),
            gateway_principal_verifier.clone(),
        );

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
            channel_binding_cleanup,
            session_channel_outbound_slot,
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
            .organization_management(organization_management)
            .provider_bot_events(provider_bot_events)
            .group_management(group_management)
            .group_query(use_cases.group_query)
            .workbench_sessions(use_cases.workbench_sessions)
            .group_proposals(use_cases.group_proposals)
            .group_fusion(use_cases.group_fusion)
            .system_message(use_cases.system_message)
            .session_management(session_management.clone())
            .channel(channel_service.clone())
            .secret(default_bootstrap_secret_service())
            .session_files(session_file_service)
            .build()
            .expect("services must be fully wired");

        // Start timeout scanner for service-invocation sessions
        let _timeout_handle = crate::timeout_scanner::spawn_with_url_guard(
            services.session_management.clone(),
            services.group.clone(),
            crate::timeout_scanner::DEFAULT_SCAN_INTERVAL,
            outbound_url_guard.clone(),
        );
        // Start Pending-sweep for session-file workspace
        spawn_session_files_pending_sweep(services.session_files.clone());

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
        let auth_chain = Arc::new(
            crate::auth_wiring::try_build_auth_chain_with_factories(
                &auth_config,
                bot_registry.clone(),
                user_identity_port.clone(),
                &extensions.auth_plugin_factories,
            )
            .map_err(crate::BcsError::InvalidConfig)?,
        );
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
            gateway_principal_verifier,
            invite_token_secret,
            openapi_v1,
            group_session_secret_access,
            user_identity_port,
            outbound_url_guard,
            admin_invocation_runs,
        });

        Ok(Self { config, state })
    }

    /// Build the `/auth/*` router.
    ///
    /// Two mounting paths:
    /// 1. **Full OAuth** — when `[auth.oauth]` is configured with a non-empty
    ///    `jwt_secret`, an `http(s)` `base_url`, and at least one provider:
    ///    mounts the complete OAuth protocol routes with the auth chain
    ///    injected as the `/auth/user` fallback.
    /// 2. **Identity-only** — when no usable OAuth config exists but an auth
    ///    chain (e.g. the `local` mock plugin) is present: mounts just
    ///    `GET /auth/user`, resolved solely via the chain. This lets deployments
    ///    use `/auth/user` without configuring any OAuth provider.
    ///
    /// `jwt_secret` comes from the resolved `auth_config` (see
    /// `auth_wiring::resolve_auth_config`). Currently only `google` is wired.
    fn build_auth_router(&self) -> Option<Router> {
        let auth_chain = Arc::clone(&self.state.auth_chain);

        // --- Case 1: full OAuth configuration ------------------------------
        // `auth_config.oauth` is the resolved form: present only when a
        // non-empty jwt_secret was configured (I6 gate lives in resolve).
        if let (Some(resolved), Some(raw)) =
            (self.state.auth_config.oauth.as_ref(), self.config.auth.oauth.as_ref())
        {
            if !resolved.jwt_secret.is_empty() {
                let base = resolved.base_url.trim();
                if base.starts_with("http://") || base.starts_with("https://") {
                    let mut providers: std::collections::HashMap<
                        String,
                        Arc<dyn bcs_auth_api::OAuthProvider>,
                    > = std::collections::HashMap::new();

                    // Build every configured provider instance via the
                    // composition-root factory. A misconfigured provider
                    // (unknown kind / empty client_id) is an operator error:
                    // fail fast at startup rather than silently dropping it and
                    // surfacing a runtime 404.
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

                    if !providers.is_empty() {
                        if let Some(user_port) = self.state.user_identity_port.clone() {
                            let route_state = Arc::new(bcs_http::oauth::OAuthRouteState::new(
                                &resolved.jwt_secret,
                                user_port,
                                providers,
                                resolved.clone(),
                                Some(auth_chain),
                            ));

                            info!(
                                providers = ?route_state.providers.keys().collect::<Vec<_>>(),
                                cookie_secure = resolved.cookie_secure,
                                env = %resolved.env,
                                "Mounting OAuth /auth/* routes"
                            );
                            return Some(bcs_http::oauth::routes(route_state));
                        }
                    } else {
                        warn!(
                            "[auth.oauth] present but no OAuth providers configured; \
                             mounting identity-only /auth/user"
                        );
                    }
                } else {
                    warn!(
                        base_url = %resolved.base_url,
                        "[auth.oauth] base_url must be an http(s) URL"
                    );
                }
            } else {
                warn!("[auth.oauth] jwt_secret is empty");
            }
        }

        // --- Case 2: identity-only (chain-backed, no OAuth) -----------------
        if let Some(user_port) = self.state.user_identity_port.clone() {
            let route_state = Arc::new(bcs_http::oauth::OAuthRouteState::new_chain_only(
                user_port,
                auth_chain,
            ));
            info!("Mounting identity-only /auth/user (no OAuth providers configured)");
            return Some(bcs_http::oauth::identity_routes(route_state));
        }

        None
    }

    /// Build the Axum router.
    async fn build_router(&self) -> crate::Result<Router> {
        let api_router = bcs_http::router::build_router(
            crate::http_adapter::build_http_app_state(Arc::clone(&self.state)).await,
        );
        let group_session_connections = build_group_session_connection_service(
            self.state.openapi_v1.session_service.clone(),
            &self.config.group_session_ws,
            self.state.group_session_secret_access.clone(),
        )
        .await?;
        let group_session_websocket_router = bcs_ws::web::group_session_websocket_router(
            group_session_connections.clone(),
            web_ws_dispatch_state(&self.state, Some(group_session_connections.clone())),
            ws_lifecycle_hook(&self.state),
        );

        let mut router = Router::new()
            // WebSocket endpoint for frontend clients (via gateway)
            .route(bcs_ws::web::FRONTEND_WS_ENDPOINT, get(ws_upgrade_handler))
            // WebSocket for bot connections
            .route(bcs_ws::bot::BOT_WS_ENDPOINT, get(bot_ws_handler));

        if let Some(metrics) = &self.state.metrics {
            router = router.route(&metrics.endpoint_path, get(metrics_handler));
        }

        let mut router = router
            .with_state(Arc::clone(&self.state))
            .merge(api_router)
            .merge(bcs_api_http::router(self.state.openapi_v1.clone()))
            .merge(bcs_api_http::group_session_connection_router(
                group_session_connections,
                self.state.gateway_principal_verifier.clone(),
            ))
            .merge(group_session_websocket_router);

        if let Some(oauth_router) = self.build_auth_router() {
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

        Ok(router
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
            .layer(
                TraceLayer::new_for_http()
                    .make_span_with(bcs_http::gateway_trace::BcnMakeSpan)
                    .on_response(bcs_http::gateway_trace::BcnOnResponse),
            )
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
            ))
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

    fn spawn_state_machine_timeout_scanner(&self) -> tokio::task::JoinHandle<()> {
        crate::state_machine_timeout_scanner::spawn(
            self.state.leader_election.clone(),
            self.state.services.collaboration_runtime.clone(),
            crate::state_machine_timeout_scanner::DEFAULT_SCAN_INTERVAL,
            crate::state_machine_timeout_scanner::DEFAULT_BATCH_SIZE,
            crate::state_machine_timeout_scanner::DEFAULT_TIMEOUT_GRACE_MS,
        )
    }

    /// Run the server with graceful shutdown support.
    pub async fn run(self) -> Result<()> {
        let addr: SocketAddr = format!("{}:{}", self.config.bind, self.config.port)
            .parse()
            .map_err(|e| crate::BcsError::InvalidConfig(format!("Invalid address: {}", e)))?;

        self.initialize_lifecycle().await?;
        let _state_machine_timeout_handle = self.spawn_state_machine_timeout_scanner();

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
                    let removed_contexts =
                        bot_run_context.cleanup_expired(now_ms, retention_ms).await;
                    if removed_contexts > 0 {
                        info!(
                            removed = removed_contexts,
                            "bot_run_context: cleanup_expired"
                        );
                    }
                }
            });
        }

        let app = self.build_router().await?;

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
        let _state_machine_timeout_handle = self.spawn_state_machine_timeout_scanner();

        let app = self.build_router().await?;

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

fn web_ws_dispatch_state(
    state: &Arc<BcsServerState>,
    group_session_connections: Option<Arc<dyn GroupSessionConnectionService>>,
) -> Arc<bcs_ws::web::WebDispatchState> {
    Arc::new(bcs_ws::web::WebDispatchState {
        message_flow: state.services.message_flow.clone(),
        collaboration_runtime: state.services.collaboration_runtime.clone(),
        workbench_sessions: state.services.workbench_sessions.clone(),
        group_session_connections,
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
    use axum::body::to_bytes;
    use bcs_protocol::{BcsFrame, EventFrame, RequestFrame};
    use bcs_service_api::RegisterProviderCommand;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;
    use tokio::time::{Duration, timeout};
    use tower::ServiceExt;

    #[derive(Default)]
    struct RecordingSessionChannelOutbound {
        events: tokio::sync::Mutex<Vec<HumanInputReadyEvent>>,
    }

    #[tokio::test]
    async fn new_allowing_private_outbound_for_tests_seeds_configured_group_session_secret() {
        let tmp = tempfile::TempDir::new().expect("temp dir");
        let mut config = BcsConfig::default();
        config.session_files.backend.insert(
            "data_dir".to_string(),
            toml::Value::String(tmp.path().to_string_lossy().into_owned()),
        );
        config.group_session_ws.signing_key_secret =
            "custom-group-session-ws-test-secret".to_string();

        let server = BcsServer::new_allowing_private_outbound_for_tests(config);

        let _ = server
            .build_router()
            .await
            .expect("test constructor should seed group-session signing material under configured name");
    }

    #[tokio::test]
    #[serial_test::serial]
    async fn public_constructor_does_not_install_test_group_session_signing_key() {
        let tmp = tempfile::TempDir::new().expect("temp dir");
        let mut config = BcsConfig::default();
        config.gateway_principal.signing_key_env =
            "BCS_TEST_GATEWAY_PRINCIPAL_SIGNING_KEY".to_string();
        config.secret.provider = "noop".to_string();
        config.session_files.backend.insert(
            "data_dir".to_string(),
            toml::Value::String(tmp.path().to_string_lossy().into_owned()),
        );

        unsafe {
            std::env::set_var(
                "BCS_TEST_GATEWAY_PRINCIPAL_SIGNING_KEY",
                "test-only-gateway-principal-signing-key",
            );
        }
        let server = BcsServer::new(config);
        unsafe {
            std::env::remove_var("BCS_TEST_GATEWAY_PRINCIPAL_SIGNING_KEY");
        }
        let error = match server.build_router().await {
            Ok(_) => panic!("public constructor must not install the fixed test signing key"),
            Err(error) => error,
        };

        assert!(
            error
                .to_string()
                .contains("group_session_ws.signing_key_secret 'bcn-group-session-ws-jwt' is required")
        );
    }

    #[async_trait]
    impl SessionChannelOutboundPort for RecordingSessionChannelOutbound {
        async fn publish_human_input_ready(
            &self,
            event: HumanInputReadyEvent,
        ) -> ServiceResult<SessionChannelDeliveryOutcome> {
            self.events.lock().await.push(event);
            Ok(SessionChannelDeliveryOutcome::Delivered)
        }
    }

    #[tokio::test]
    async fn deferred_session_channel_outbound_is_inert_until_initialized() {
        let (slot, deferred) = deferred_session_channel_outbound();
        let event = HumanInputReadyEvent {
            event_id: "event-1".to_string(),
            group_id: "group-1".to_string(),
            session_id: "session-1".to_string(),
            run_id: "run-1".to_string(),
            node_id: "review".to_string(),
            display_name: "Review".to_string(),
            instruction: "Review the draft".to_string(),
            assignee_actor_id: "human-1".to_string(),
            channel_type: "dingtalk".to_string(),
            notification_mode: bcs_domain::HumanInputNotificationMode::DirectAssignee,
            fixed_group_conversation_id: None,
            response_ref: "run-1/review".to_string(),
            upstream_artifacts: Vec::new(),
            judge_outcomes: vec!["approved".to_string()],
            timeout_deadline_ms: Some(60_000),
        };

        assert_eq!(
            deferred
                .publish_human_input_ready(event.clone())
                .await
                .expect("uninitialized outbound"),
            SessionChannelDeliveryOutcome::NotApplicable
        );

        let recording = Arc::new(RecordingSessionChannelOutbound::default());
        assert!(slot.set(recording.clone()).is_ok());
        assert_eq!(
            deferred
                .publish_human_input_ready(event)
                .await
                .expect("initialized outbound"),
            SessionChannelDeliveryOutcome::Delivered
        );
        assert_eq!(recording.events.lock().await.len(), 1);
    }

    async fn response_json(response: Response) -> serde_json::Value {
        let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        serde_json::from_slice(&body).unwrap()
    }

    async fn read_http_request(socket: &mut tokio::net::TcpStream) -> Vec<u8> {
        let mut request = Vec::new();
        loop {
            let mut chunk = [0; 1024];
            let count = socket.read(&mut chunk).await.unwrap();
            assert!(count > 0, "callback closed before request completed");
            request.extend_from_slice(&chunk[..count]);
            let Some(headers_end) = request.windows(4).position(|window| window == b"\r\n\r\n")
            else {
                continue;
            };
            let headers_end = headers_end + 4;
            let headers = String::from_utf8_lossy(&request[..headers_end]);
            let content_length = headers
                .lines()
                .find_map(|line| {
                    line.split_once(':').and_then(|(name, value)| {
                        name.eq_ignore_ascii_case("content-length")
                            .then(|| value.trim().parse::<usize>().unwrap())
                    })
                })
                .unwrap_or(0);
            if request.len() >= headers_end + content_length {
                return request;
            }
        }
    }

    struct AdminRunTestOrganization {
        provider_id: String,
    }

    impl AdminRunTestOrganization {
        fn organization(&self, code: &str) -> bcs_domain::Organization {
            bcs_domain::Organization {
                env: "local".to_string(),
                code: code.to_string(),
                name: "Admin WS Org".to_string(),
                description: None,
                managing_provider_id: self.provider_id.clone(),
                disabled: false,
                created_at: 1,
                updated_at: 1,
            }
        }

        fn member(&self, code: &str, bot_uuid: &str) -> bcs_domain::OrganizationMember {
            bcs_domain::OrganizationMember {
                env: "local".to_string(),
                organization_code: code.to_string(),
                bot_uuid: bot_uuid.to_string(),
                role: None,
                disabled: false,
                created_at: 1,
                updated_at: 1,
            }
        }
    }

    #[async_trait]
    impl OrganizationCoreService for AdminRunTestOrganization {
        async fn create(
            &self,
            _managing_provider_id: &str,
            code: &str,
            _name: &str,
            _description: Option<&str>,
        ) -> bcs_service_api::ServiceResult<bcs_domain::Organization> {
            Ok(self.organization(code))
        }

        async fn get_for_manager(
            &self,
            _managing_provider_id: &str,
            code: &str,
        ) -> bcs_service_api::ServiceResult<bcs_domain::Organization> {
            Ok(self.organization(code))
        }

        async fn list_for_manager(
            &self,
            _managing_provider_id: &str,
            _include_disabled: bool,
        ) -> bcs_service_api::ServiceResult<Vec<bcs_domain::Organization>> {
            Ok(Vec::new())
        }

        async fn update_for_manager(
            &self,
            _managing_provider_id: &str,
            code: &str,
            _name: Option<&str>,
            _description: Option<Option<&str>>,
            _disabled: Option<bool>,
        ) -> bcs_service_api::ServiceResult<bcs_domain::Organization> {
            Ok(self.organization(code))
        }

        async fn put_member(
            &self,
            _managing_provider_id: &str,
            organization_code: &str,
            bot_uuid: &str,
            _role: Option<&str>,
        ) -> bcs_service_api::ServiceResult<bcs_domain::OrganizationMember> {
            Ok(self.member(organization_code, bot_uuid))
        }

        async fn delete_member(
            &self,
            _managing_provider_id: &str,
            _organization_code: &str,
            _bot_uuid: &str,
        ) -> bcs_service_api::ServiceResult<()> {
            Ok(())
        }

        async fn get_member_for_manager(
            &self,
            _managing_provider_id: &str,
            organization_code: &str,
            bot_uuid: &str,
        ) -> bcs_service_api::ServiceResult<Option<bcs_domain::OrganizationMember>> {
            Ok(Some(self.member(organization_code, bot_uuid)))
        }

        async fn list_members_for_manager(
            &self,
            _managing_provider_id: &str,
            _organization_code: &str,
            _include_disabled: bool,
            _role: Option<&str>,
        ) -> bcs_service_api::ServiceResult<Vec<bcs_domain::OrganizationMember>> {
            Ok(Vec::new())
        }

        async fn candidate_bots(
            &self,
            _managing_provider_id: &str,
            _query: bcs_service_api::OrganizationCandidateQuery,
        ) -> bcs_service_api::ServiceResult<Vec<bcs_service_api::OrganizationCandidateBot>> {
            Ok(Vec::new())
        }

        async fn require_effective_member(
            &self,
            organization_code: &str,
            bot_uuid: &str,
        ) -> bcs_service_api::ServiceResult<bcs_domain::OrganizationMember> {
            Ok(self.member(organization_code, bot_uuid))
        }

        async fn list_effective_members(
            &self,
            _organization_code: &str,
            _role: Option<&str>,
        ) -> bcs_service_api::ServiceResult<Vec<bcs_domain::OrganizationMember>> {
            Ok(Vec::new())
        }

        async fn require_runtime_member(
            &self,
            organization_code: &str,
            bot_uuid: &str,
        ) -> bcs_service_api::ServiceResult<bcs_domain::OrganizationMember> {
            Ok(self.member(organization_code, bot_uuid))
        }

        async fn list_runtime_members(
            &self,
            _organization_code: &str,
            _role: Option<&str>,
        ) -> bcs_service_api::ServiceResult<Vec<bcs_domain::OrganizationMember>> {
            Ok(Vec::new())
        }

        async fn authorize_pair(
            &self,
            organization_code: &str,
            sender_bot_uuid: &str,
            target_bot_uuid: &str,
        ) -> bcs_service_api::ServiceResult<bcs_service_api::AuthorizedOrganizationPair> {
            Ok(bcs_service_api::AuthorizedOrganizationPair {
                organization: self.organization(organization_code),
                sender: self.member(organization_code, sender_bot_uuid),
                target: self.member(organization_code, target_bot_uuid),
            })
        }
    }

    struct AdminRunTestA2aRuns;

    #[async_trait]
    impl A2aChatRunService for AdminRunTestA2aRuns {
        async fn run_blocking_chat(
            &self,
            _cmd: bcs_service_api::BlockingA2aChatCommand,
        ) -> bcs_service_api::ServiceResult<bcs_service_api::BlockingA2aChatOutcome> {
            unreachable!("admin run test only uses async chat")
        }

        async fn start_async_chat(
            &self,
            cmd: bcs_service_api::AsyncA2aChatCommand,
        ) -> bcs_service_api::ServiceResult<bcs_service_api::AsyncA2aChatAccepted> {
            Ok(bcs_service_api::AsyncA2aChatAccepted {
                run_id: cmd.run_id,
                bot_uuid: cmd.target_bot_id,
                session_id: cmd.session_key,
                status: "dispatched".to_string(),
                expires_at_ms: u64::MAX,
            })
        }

        async fn get_run(
            &self,
            cmd: bcs_service_api::ChatRunQueryCommand,
        ) -> bcs_service_api::ServiceResult<bcs_service_api::A2aRunStatus> {
            Ok(bcs_service_api::A2aRunStatus {
                run_id: cmd.run_id,
                status: "running".to_string(),
                response: None,
            })
        }

        async fn cancel_run(
            &self,
            _cmd: bcs_service_api::ChatRunCancelCommand,
        ) -> bcs_service_api::ServiceResult<bcs_service_api::A2aRunStatus> {
            unreachable!("admin run test does not cancel")
        }
    }

    #[test]
    fn judge_llm_provider_selection_uses_public_provider_types() {
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

        config.llm.provider_type = LlmProviderType::Anthropic;
        assert_eq!(
            select_judge_llm_provider(&config).unwrap(),
            JudgeLlmProviderKind::Anthropic
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
            Arc::new(MemoryChannelBindingRepo::new("test")),
        );

        assert!(matches!(
            result,
            Err(crate::BcsError::InvalidConfig(message))
                if message.contains("missing-provider")
        ));
    }

    #[tokio::test]
    async fn chat_run_events_registered_by_http_are_visible_to_frontend_fallback() {
        let tmp = tempfile::TempDir::new().expect("temp dir");
        let mut config = BcsConfig::default();
        config.session_files.backend.insert(
            "data_dir".to_string(),
            toml::Value::String(tmp.path().to_string_lossy().into_owned()),
        );
        let server = BcsServer::new_allowing_private_outbound_for_tests(config);
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
        let tmp = tempfile::TempDir::new().expect("temp dir");
        let mut config = BcsConfig::default();
        config.session_files.backend.insert(
            "data_dir".to_string(),
            toml::Value::String(tmp.path().to_string_lossy().into_owned()),
        );
        let server = BcsServer::new_allowing_private_outbound_for_tests(config);

        let first = bot_ws_dispatch_state(&server.state);
        let second = bot_ws_dispatch_state(&server.state);

        assert!(Arc::ptr_eq(
            &first.coordination_processed,
            &second.coordination_processed
        ));
    }

    #[tokio::test]
    async fn detached_admin_run_observes_websocket_terminal_and_callbacks_once() {
        let callback_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let callback_url = format!(
            "http://{}/admin-terminal",
            callback_listener.local_addr().unwrap()
        );
        let _tmp = tempfile::TempDir::new().expect("temp dir");
        let mut config = BcsConfig::default();
        config.session_files.backend.insert(
            "data_dir".to_string(),
            toml::Value::String(_tmp.path().to_string_lossy().into_owned()),
        );
        config.async_chat_run_timeout_ms = 5_000;
        let server = BcsServer::new_allowing_private_outbound_for_tests(config);

        let provider = server
            .state
            .services
            .provider_management
            .register_provider(RegisterProviderCommand {
                name: "Admin Provider".to_string(),
                webhook_url: callback_url.clone(),
                admin_callback_url: Some(callback_url),
                auth_mode: bcs_domain::ProviderAuthMode::StaticBearer,
                created_by: "admin-owner".to_string(),
                protocol_version: None,
                coordination: None,
            })
            .await
            .unwrap();

        let dispatch_state = bot_ws_dispatch_state(&server.state);
        let (bot_tx, mut bot_rx) = tokio::sync::mpsc::channel(16);
        let mut registered_bot_id = None;
        let connect = BcsFrame::Request(RequestFrame::new(
            "connect-1",
            "bot.connect",
            Some(serde_json::json!({
                "bot_id": "ws-admin-target",
                "protocol_version": 1
            })),
        ));
        bcs_ws::bot::dispatch_frame(
            &dispatch_state,
            &serde_json::to_string(&connect).unwrap(),
            &bot_tx,
            &mut registered_bot_id,
        )
        .await
        .unwrap();
        assert_eq!(registered_bot_id.as_deref(), Some("ws-admin-target"));
        let _connect_response = bot_rx.recv().await.unwrap();

        let mut http_state = crate::http_adapter::build_http_app_state(server.state.clone()).await;
        http_state.services.organization_management = Arc::new(OrganizationManagement::new(
            http_state.services.provider_core.clone(),
            Arc::new(AdminRunTestOrganization {
                provider_id: provider.provider_id.clone(),
            }),
        ));
        http_state.services.a2a_chat_runs = Arc::new(AdminRunTestA2aRuns);
        let app = bcs_http::router::build_router(http_state);

        let create_response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/organizations/admin-ws-org/admin-runs")
                    .header("content-type", "application/json")
                    .header("x-bcn-provider-id", &provider.provider_id)
                    .header(
                        "authorization",
                        format!("Bearer {}", provider.provider_admin_token),
                    )
                    .body(Body::from(
                        serde_json::json!({
                            "target_bot_uuid": "ws-admin-target",
                            "message": {
                                "role": "user",
                                "content": [{"type": "text", "text": "run detached"}]
                            },
                            "detach": true,
                            "run_timeout_ms": 5_000
                        })
                        .to_string(),
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(create_response.status(), StatusCode::OK);
        let create_body = response_json(create_response).await;
        let run_id = create_body["data"]["run_id"].as_str().unwrap().to_string();

        let terminal = BcsFrame::Event(EventFrame::new(
            "chat.event",
            Some(serde_json::json!({
                "run_id": run_id,
                "bcs_group_id": "",
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "detached websocket result"}],
                    "timestamp": 1
                }
            })),
            Some(1),
        ));
        bcs_ws::bot::dispatch_frame(
            &dispatch_state,
            &serde_json::to_string(&terminal).unwrap(),
            &bot_tx,
            &mut registered_bot_id,
        )
        .await
        .unwrap();
        bcs_ws::bot::dispatch_frame(
            &dispatch_state,
            &serde_json::to_string(&terminal).unwrap(),
            &bot_tx,
            &mut registered_bot_id,
        )
        .await
        .unwrap();

        let get_response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("GET")
                    .uri(format!(
                        "/organizations/admin-ws-org/admin-runs/{run_id}"
                    ))
                    .header("x-bcn-provider-id", &provider.provider_id)
                    .header(
                        "authorization",
                        format!("Bearer {}", provider.provider_admin_token),
                    )
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(get_response.status(), StatusCode::OK);
        let get_body = response_json(get_response).await;
        assert_eq!(get_body["data"]["status"], "completed");
        assert_eq!(
            get_body["data"]["message"]["content"][0]["text"],
            "detached websocket result"
        );

        let (mut callback_socket, _) = timeout(Duration::from_secs(2), callback_listener.accept())
            .await
            .unwrap()
            .unwrap();
        let callback_request = read_http_request(&mut callback_socket).await;
        callback_socket
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
            .await
            .unwrap();
        let callback_request = String::from_utf8_lossy(&callback_request);
        assert!(callback_request.contains("detached websocket result"));
        assert!(callback_request.contains(&run_id));
        assert!(
            timeout(Duration::from_millis(100), callback_listener.accept())
                .await
                .is_err()
        );
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
        let ws_state = web_ws_dispatch_state(&state, None);
        let metrics_hook = ws_lifecycle_hook(&state);
        bcs_ws::web::handle_client_connection(
            socket,
            ws_state,
            bcs_ws::web::WorkbenchConnectionAuth::UserBound {
                actor_id: bound_actor_id,
            },
            metrics_hook,
        )
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
