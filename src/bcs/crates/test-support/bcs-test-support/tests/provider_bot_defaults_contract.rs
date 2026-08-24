//! Contract tests asserting the trait DEFAULT implementations of the
//! provider-bot update flow fail closed with a "not configured" error.
//!
//! These default bodies live in `bcs-service-api`:
//! - `BotRegistryCoreService::update_capabilities` (core/registry.rs)
//! - `ProviderBotCoreService::update_provider_bot` (core/provider.rs)
//!
//! The noop structs here inherit them (they override only the methods they
//! need), so invoking the defaults through the noops exercises those
//! default-body lines, which otherwise never run when a real store impl is
//! wired into tests.

use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, ProviderBotCoreService, ProviderManagementService,
    ServiceError, ServiceResult, UpdateProviderBotCommand,
};
use bcs_test_support::{
    NoopBotRegistryCoreService, NoopProviderBotCoreService, NoopProviderManagementService,
};

#[tokio::test]
async fn noop_registry_update_capabilities_fails_closed_by_default() {
    let registry = NoopBotRegistryCoreService;
    // The store impls override this; the trait default must fail closed.
    let result = registry
        .update_capabilities(
            "bot-1",
            BotCapabilities {
                name: Some("Bot".to_string()),
                ..Default::default()
            },
        )
        .await;
    assert_not_configured(result, "capability replacement is not configured");
}

#[tokio::test]
async fn noop_provider_bot_core_update_provider_bot_fails_closed_by_default() {
    let core = NoopProviderBotCoreService;
    let result = core
        .update_provider_bot(
            "provider-1",
            "admin-token",
            "ref-1",
            Some("Name".to_string()),
            None,
            Some(Vec::new()),
            Some(Vec::new()),
            Some(Vec::new()),
            None,
        )
        .await;
    assert_not_configured(result, "provider bot updates are not configured");
}

#[tokio::test]
async fn noop_provider_management_update_provider_bot_disables_the_service() {
    // NoopProviderManagementService explicitly returns "service not configured"
    // for every method (the application trait has no defaults), so consumers
    // wiring only the noop get a clear error rather than a silent no-op.
    let service = NoopProviderManagementService;
    let result = service
        .update_provider_bot(UpdateProviderBotCommand {
            provider_id: "provider-1".to_string(),
            provider_admin_token: "admin-token".to_string(),
            provider_bot_ref: "ref-1".to_string(),
            name: Some("Name".to_string()),
            summary: None,
            domains: None,
            skills: None,
            scopes: None,
            visibility: None,
        })
        .await;
    assert_not_configured(result, "provider management service is not configured");
}

fn assert_not_configured<T>(result: ServiceResult<T>, expected_message: &str) {
    assert!(matches!(
        result,
        Err(ServiceError::InvalidOperation {
            message,
            request_id: None,
        }) if message == expected_message
    ));
}
