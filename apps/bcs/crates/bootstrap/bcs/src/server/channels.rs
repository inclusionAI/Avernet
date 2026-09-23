//! Channel (IM bridge) runtime construction stitches.
//!
//! Split out from server.rs as part of the V1 API auth plugin chain (Task 1) refactor. Behavior preserved exactly.

use super::*;

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

pub(super) fn register_channel_lifecycles(
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
