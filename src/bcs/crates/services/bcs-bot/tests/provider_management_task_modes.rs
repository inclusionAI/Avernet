//! Coverage for the `list_provider_bots_by_task_modes` error arm: when the
//! bot control-plane core was never injected (composition root always wires it
//! for the production/memory server paths), the roster method must surface an
//! InternalError instead of touching the provider bot core.

use std::sync::Arc;

use bcs_bot::ProviderManagement;
use bcs_service_api::{
    ProviderBotTaskModesFilter, ProviderManagementService, ServiceError, TaskModeMatch,
};
use bcs_test_support::{
    NoopBotRegistryCoreService, NoopProviderBotCoreService, NoopProviderCoreService,
    NoopRelationCoreService,
};

#[tokio::test]
async fn list_provider_bots_by_task_modes_errors_when_control_plane_not_configured() {
    // The noop cores are intentionally never reached: the handler returns early
    // when no control-plane core was injected via `with_control_plane`.
    let management = ProviderManagement::new(
        Arc::new(NoopProviderCoreService) as Arc<dyn bcs_service_api::ProviderCoreService>,
        Arc::new(NoopProviderBotCoreService) as Arc<dyn bcs_service_api::ProviderBotCoreService>,
        Arc::new(NoopBotRegistryCoreService) as Arc<dyn bcs_service_api::BotRegistryCoreService>,
        Arc::new(NoopRelationCoreService) as Arc<dyn bcs_service_api::RelationCoreService>,
    );

    let result = management
        .list_provider_bots_by_task_modes(ProviderBotTaskModesFilter {
            task_claim_mode: None,
            task_dream_mode: None,
            match_mode: TaskModeMatch::Any,
            visibility: None,
            status: None,
            user_visibility: None,
        })
        .await;

    assert!(
        matches!(result, Err(ServiceError::InternalError(_))),
        "expected InternalError when the control-plane core is not configured",
    );
}
