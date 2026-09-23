//! Collaboration template service + group message history service.
//!
//! Split out from server.rs as part of the V1 API auth plugin chain (Task 1) refactor. Behavior preserved exactly.

use super::*;

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

pub(super) fn create_group_message_history_service(
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
    pending_messages: Arc<dyn bcs_service_api::PendingGroupMessagePort>,
    history_attachment_ttl: u64,
    persisted_state_machine_history: bool,
    state_machine_cutoff_timestamp: u64,
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
        pending_messages,
        cutoff_timestamp,
        manager_worker_cutoff_timestamp,
        new_participant_visible_limit,
        default_page_limit,
        max_page_limit,
        history_attachment_ttl,
    ).with_persisted_state_machine_history(persisted_state_machine_history, state_machine_cutoff_timestamp))
}
