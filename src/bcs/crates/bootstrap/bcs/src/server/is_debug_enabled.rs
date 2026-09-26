//! server implementation.
use super::*;


/// Check if debug mode is enabled via BCS_DEBUG env var
pub(super) fn is_debug_enabled() -> bool {
    std::env::var("BCS_DEBUG").is_ok_and(|v| v == "true")
}



/// Build a default `SecretService` for the `ServicesBuilder` step.
///
/// At builder time we do not perform async provider initialization. We seed
/// every `Services` instance with a Noop so the builder's required-field
/// invariant is satisfied; the configured backend is swapped in alongside
/// `HttpAppState` construction.
pub(super) fn default_bootstrap_secret_service() -> Arc<dyn bcs_service_api::SecretService> {
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
pub(super) async fn build_session_files_service(
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
    use bcs_storage_baas::BaasStoragePluginFactory;
    use bcs_storage_local::LocalStoragePluginFactory;

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

    let factory: Arc<dyn StoragePluginFactory> = match config.session_files.storage_backend.as_str()
    {
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
pub(super) fn build_session_files_service_blocking(
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



#[allow(clippy::too_many_arguments)]
pub(super) fn build_eventing_runtime_blocking(
    config: &BcsConfig,
    repo: Arc<dyn bcs_service_api::port::repo::EventRepoPort>,
    groups: Arc<dyn GroupCoreService>,
    sessions: Arc<dyn SessionManagementService>,
    collaboration_runtime: Arc<dyn bcs_service_api::CollaborationRuntimeService>,
    registry: Arc<dyn BotRegistryCoreService>,
    allow_local_test_endpoints: bool,
) -> crate::Result<crate::eventing_wiring::EventingRuntime> {
    std::thread::scope(|scope| {
        scope
            .spawn(|| {
                tokio::runtime::Runtime::new()
                    .expect("temp runtime for Eventing build")
                    .block_on(crate::eventing_wiring::build_eventing_runtime(
                        config,
                        repo,
                        groups,
                        sessions,
                        collaboration_runtime,
                        registry,
                        allow_local_test_endpoints,
                    ))
            })
            .join()
            .expect("Eventing build thread panicked")
    })
}



pub(super) fn local_eventing_endpoints_allowed() -> bool {
    matches!(
        crate::config_loader::Environment::resolve(),
        crate::config_loader::Environment::Local | crate::config_loader::Environment::Dev
    )
}



/// Convert a `toml::Table` (config pass-through) into a `serde_json::Map`.
pub(super) fn toml_table_to_json_map(table: &toml::Table) -> serde_json::Map<String, serde_json::Value> {
    let mut out = serde_json::Map::new();
    for (k, v) in table {
        out.insert(k.clone(), toml_value_to_json(v));
    }
    out
}



pub(super) fn toml_value_to_json(v: &toml::Value) -> serde_json::Value {
    match v {
        toml::Value::String(s) => serde_json::Value::String(s.clone()),
        toml::Value::Integer(i) => serde_json::Value::Number((*i).into()),
        toml::Value::Float(f) => serde_json::json!(f),
        toml::Value::Boolean(b) => serde_json::Value::Bool(*b),
        toml::Value::Table(t) => serde_json::Value::Object(toml_table_to_json_map(t)),
        toml::Value::Array(a) => {
            serde_json::Value::Array(a.iter().map(toml_value_to_json).collect())
        }
        toml::Value::Datetime(d) => serde_json::Value::String(d.to_string()),
    }
}



/// Spawn the Pending-sweep background task for the session-file workspace.
///
/// Mirrors the timeout/token-expiry scanner pattern: a tokio interval task
/// that calls `sweep_expired_pending()` every 300s, logs results, and
/// swallows errors so a transient backend hiccup never tears down the loop.
pub(super) fn spawn_session_files_pending_sweep(
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



pub(super) fn build_file_collaboration_template_service_with_judge_templates(
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



pub(super) struct DeferredSessionChannelOutbound {
    pub(super) slot: SessionChannelOutboundSlot,
}



#[async_trait]
impl SessionChannelOutboundPort for DeferredSessionChannelOutbound {
    async fn recover_human_input_requests(&self, run_id: &str, session_id: &str) -> ServiceResult<Vec<String>> {
        let Some(outbound) = self.slot.get() else { return Ok(Vec::new()); };
        outbound.recover_human_input_requests(run_id, session_id).await
    }
    async fn prepare_state_machine_terminal(&self, event: &StateMachineTerminalEvent) -> ServiceResult<Vec<bcs_service_api::StateMachineTerminalNotification>> {
        let Some(outbound) = self.slot.get() else { return Ok(Vec::new()); };
        outbound.prepare_state_machine_terminal(event).await
    }
    async fn validate_terminal_notification(&self, notification: &bcs_service_api::StateMachineTerminalNotification) -> ServiceResult<()> {
        let outbound = self.slot.get().ok_or_else(|| bcs_service_api::ServiceError::InternalError("terminal IM is not configured".into()))?;
        outbound.validate_terminal_notification(notification).await
    }
    async fn deliver_terminal_notification(&self, event: &StateMachineTerminalEvent, notification: &bcs_service_api::StateMachineTerminalNotification) -> ServiceResult<Option<String>> {
        let outbound = self.slot.get().ok_or_else(|| bcs_service_api::ServiceError::InternalError("terminal IM is not configured".into()))?;
        outbound.deliver_terminal_notification(event, notification).await
    }
    async fn finish_state_machine_terminal(&self, event: &StateMachineTerminalEvent) -> ServiceResult<()> {
        let Some(outbound) = self.slot.get() else { return Ok(()); };
        outbound.finish_state_machine_terminal(event).await
    }

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



pub(super) fn deferred_session_channel_outbound() -> (
    SessionChannelOutboundSlot,
    Arc<dyn SessionChannelOutboundPort>,
) {
    let slot = Arc::new(OnceLock::new());
    let outbound: Arc<dyn SessionChannelOutboundPort> =
        Arc::new(DeferredSessionChannelOutbound { slot: slot.clone() });
    (slot, outbound)
}



pub(super) struct MessageFlowStateMachineResultPublisher {
    pub(super) message_flow: Arc<dyn MessageFlowService>,
    pub(super) message_repo: Arc<dyn MessageRepoPort>,

}

impl MessageFlowStateMachineResultPublisher {

pub(super) fn new(
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
        let saved = self.message_repo
            .append_message_with_id(idempotency_key.clone(), NewMessage {
                group_id: cmd.group_id.clone(),
                session_id: cmd.session_id.clone(),
                sender_id: cmd.sender_bot_id.clone(),
                sender_type: SenderType::Bot,
                message_type: "chat".to_string(),
                content: serde_json::Value::String(cmd.content.clone()),
                client_msg_id: Some(idempotency_key.clone()),
                owner_bot_id: None,
                created_at: cmd.created_at_ms,
                run_id: cmd.run_id.clone(),
                visibility_domain: MessageVisibilityDomain::StateMachine,
                audience: Some(MessageAudience::Public),
            })
            .await
            .map_err(|error| {
                bcs_service_api::ServiceError::InternalError(format!(
                    "persist state-machine result before delivery: {error}"
                ))
            })?;
        if saved.group_id != cmd.group_id || saved.session_id != cmd.session_id || saved.run_id != cmd.run_id
            || saved.sender_id != cmd.sender_bot_id || saved.sender_type != SenderType::Bot
            || saved.message_type != "chat" || saved.content != serde_json::Value::String(cmd.content.clone())
            || saved.client_msg_id.as_ref() != Some(&idempotency_key) || saved.created_at != cmd.created_at_ms
            || saved.visibility_domain != Some(MessageVisibilityDomain::StateMachine) || saved.audience != Some(MessageAudience::Public) {
            return Err(bcs_service_api::ServiceError::Conflict("Chat result history conflicts with saved publication".into()));
        }
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
                channel_sender_identity: None,
                sender_conn_id: None,
                provider_bypass_headers: Vec::new(),
            })
            .await?;
        Ok(())
    }
}



#[derive(Default)]
pub(super) struct DeferredChannelBindingCleanupPort {
    pub(super) service: OnceLock<Arc<dyn ChannelBindingCleanupPort>>,
}



pub(super) struct FuseBotCatalogCleanupPort {
    pub(super) client: Arc<FuseClient>,
}



#[async_trait]
impl BotCatalogCleanupPort for FuseBotCatalogCleanupPort {
    async fn delete_bot(&self, bot_id: &str) -> ServiceResult<()> {
        self.client.delete_worker(bot_id).await.map_err(|error| {
            ServiceError::InternalError(format!(
                "failed to delete bot {bot_id} from bcsfuse: {error}"
            ))
        })
    }
}



pub(super) fn build_bot_catalog_cleanup(config: &BcsConfig) -> Arc<dyn BotCatalogCleanupPort> {
    if !config.bcsfuse.enabled {
        return Arc::new(NoopBotCatalogCleanupPort);
    }
    match FuseClient::new(&config.bcsfuse) {
        Ok(client) => Arc::new(FuseBotCatalogCleanupPort { client: Arc::new(client) }),
        Err(error) => {
            warn!(error = %error, "failed to initialize bcsfuse bot catalog cleanup");
            Arc::new(NoopBotCatalogCleanupPort)
        }
    }

}

impl DeferredChannelBindingCleanupPort {

pub(super) fn set(&self, service: Arc<dyn ChannelBindingCleanupPort>) {
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

    async fn delete_bindings_for_bot(&self, bot_id: &str) -> bcs_service_api::ServiceResult<u64> {
        let service = self.service.get().ok_or_else(|| {
            bcs_service_api::ServiceError::InternalError(
                "channel binding cleanup port is not initialized".to_string(),
            )
        })?;
        service.delete_bindings_for_bot(bot_id).await
    }
}



pub(super) struct ChannelRuntime {
    pub(super) service: Arc<dyn ChannelService>,
    pub(super) http_ingress: Option<Arc<ChannelHttpIngressRegistry>>,
    pub(super) lifecycles: Vec<Arc<dyn ServiceLifecycle>>,
}



#[derive(Debug, Default)]
pub(super) struct DisabledChannelService;



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

    async fn list_conversations_by_session(
        &self,
        _bcs_session_id: &str,
        _channel_type: Option<bcs_domain::ChannelType>,
    ) -> std::result::Result<
        Vec<bcs_domain::ConversationSessionMap>,
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



pub(super) fn now_ms() -> u64 {
    match SystemTime::now().duration_since(UNIX_EPOCH) {
        Ok(duration) => duration.as_millis() as u64,
        Err(_) => 0,
    }
}



pub(super) fn channel_bridge_enabled(config: &BcsConfig) -> bool {
    config.channels.enabled
}



pub(super) fn memory_channel_repos(data_dir: Option<PathBuf>) -> ChannelRepos {
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



pub(super) async fn channel_repos_with_storage(
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
pub(super) fn build_channel_runtime(
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
        channel_binding_cleanup.set(Arc::new(bcs_service_api::NoopChannelBindingCleanupPort));
        return Ok(ChannelRuntime {
            service: Arc::new(DisabledChannelService),
            http_ingress: None,
            lifecycles: Vec::new(),
        });
    }

    let (channel_bindings, channel_conversations, channel_im_participants, human_input_requests) =
        channel_repos;
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



pub(super) fn build_configured_channel_providers(
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



pub(super) fn build_file_collaboration_template_service(
    config: &BcsConfig,
) -> Arc<dyn CollaborationTemplateService> {
    build_file_collaboration_template_service_with_judge_templates(config, config.llm.is_enabled())
}



pub(super) fn build_standalone_collaboration_template_service(
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



pub(super) fn build_collaboration_template_service_with_storage(
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
pub(super) async fn debug_middleware(req: Request<Body>, next: Next) -> Response {
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
