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
