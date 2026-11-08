//! Operation-boundary delivery admission construction
//! (bot-authority spec §8.2 — 已准入输出不追溯).
//!
//! Built BEFORE the message-flow builder so every accepted business
//! operation freezes its per-connection output permits at accept
//! time: the admission service resolves each candidate connection's
//! delivery view once per operation (never per frame), the Workbench
//! registry consults it per new operation/event, and the output
//! admission port hands frozen permits plus the operation's complete
//! output-stream set to the adapter (Run terminal ≠ operation
//! complete).

use std::sync::Arc;

use bcs_ws::shared::RunChannelManager;
use bcs_ws::web::WorkbenchConnectionRegistry;

/// The shared per-operation permit store wiring of the protected
/// Workbench delivery (spec §8.2/§8.4 — process-local permits):
/// the fanout registry and the run-channel manager share ONE store,
/// and the manager retires saturated slow connections through the
/// registry (spec §9).
pub(super) fn create_protected_workbench(
    scope_changes_enabled: bool,
) -> (Arc<WorkbenchConnectionRegistry>, Arc<RunChannelManager>) {
    let output_permits = Arc::new(bcs_ws::shared::OutputPermitStore::new());
    let frontend_connections = Arc::new(
        WorkbenchConnectionRegistry::new()
            .with_scope_changes_enabled(scope_changes_enabled)
            .with_output_permits(output_permits.clone()),
    );
    let run_channels = Arc::new(
        RunChannelManager::new()
            .with_output_permits(output_permits)
            .with_connection_retirement(frontend_connections.clone()),
    );
    (frontend_connections, run_channels)
}

/// The operation-boundary admission trio shared across the server
/// state: the coarse session-authorization core (also consumed by
/// other use cases), the per-operation delivery admission service,
/// and the port the message flow calls at accept time.
pub(super) struct OutputAdmissionWiring {
    pub session_authorization_core:
        Arc<dyn bcs_service_api::core::SessionAuthorizationCoreService>,
    pub delivery_admission:
        Arc<dyn bcs_service_api::application::v1::DeliveryAdmissionService>,
    pub output_admission: Arc<dyn bcs_service_api::OutputAdmissionPort>,
}

pub(super) fn create_output_admission(
    groups: Arc<dyn bcs_service_api::GroupCoreService>,
    session_repo: Arc<dyn bcs_service_api::port::repo::SessionRepoPort>,
    bot_authority_hook: Arc<dyn bcs_service_api::port::bot_authority::BotAuthorityPort>,
    frontend_connections: &Arc<WorkbenchConnectionRegistry>,
) -> OutputAdmissionWiring {
    let session_authorization_core: Arc<
        dyn bcs_service_api::core::SessionAuthorizationCoreService,
    > = Arc::new(bcs_session::SessionAuthorizationCoreImpl::new(session_repo));
    let delivery_admission: Arc<dyn bcs_service_api::application::v1::DeliveryAdmissionService> =
        Arc::new(bcs_app_session::DeliveryAdmissionServiceImpl::new(
            groups,
            session_authorization_core.clone(),
            Some(bot_authority_hook),
        ));
    frontend_connections.set_delivery_admission(delivery_admission.clone());
    let output_admission: Arc<dyn bcs_service_api::OutputAdmissionPort> =
        Arc::new(bcs_ws::web::WorkbenchOutputAdmission::new(
            frontend_connections.clone(),
            delivery_admission.clone(),
        ));
    OutputAdmissionWiring {
        session_authorization_core,
        delivery_admission,
        output_admission,
    }
}

/// Keep storage selection and its validation at the composition root. All Run
/// producers receive this same decorated port; MessageFlow shares its tracker.
pub(super) fn create_configured_run_context(
    config: &crate::BcsConfig,
    cache_kind: crate::plugins::CachePluginKind,
    cache: Arc<dyn bcs_cache_api::CachePlugin>,
    key_prefix: String,
) -> crate::Result<Arc<bcs_message_flow::TrackedBotRunContext>> {
    use bcs_service_api::BotRunContextPort;
    if config.bot_run_context_store == "redis"
        && cache_kind != crate::plugins::CachePluginKind::Redis {
        return Err(crate::BcsError::InvalidConfig(
            "bot_run_context_store='redis' requires a Redis cache backend \
             ([cache.redis]); the resolved cache is not Redis — refusing to \
             start with silently process-local run context".to_string(),
        ));
    }
    let inner: Arc<dyn BotRunContextPort> = if config.bot_run_context_store == "redis" {
        Arc::new(bcs_message_flow::RedisBotRunContextStore::new(
            cache, key_prefix, config.async_chat_run_retention_ms,
        ))
    } else {
        Arc::new(bcs_message_flow::MemoryBotRunContextStore::new())
    };
    Ok(Arc::new(bcs_message_flow::TrackedBotRunContext::new(inner)))
}

#[cfg(test)]
mod run_context_tests {
    use super::*;

    #[test]
    fn redis_context_still_requires_a_real_redis_backend() {
        let mut config = crate::BcsConfig::default();
        config.bot_run_context_store = "redis".into();
        let result = create_configured_run_context(&config, crate::plugins::CachePluginKind::LocalMemory,
            Arc::new(bcs_cache_local::InMemoryCachePlugin::new()), "test:".into());
        assert!(matches!(result, Err(crate::BcsError::InvalidConfig(message))
            if message.contains("requires a Redis cache backend")));
    }
}
