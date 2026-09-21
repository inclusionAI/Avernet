//! Shared scoped registration semantics; consumers supply an enabled, owned
//! Provider with an enabled, nonblank downlink credential but no shared endpoint,
//! and a core with no self-service allowlist.
use bcs_service_api::ServiceError;
use bcs_service_api::core::provider_registration::{
    ProviderRegistrationCoreService, ProviderRegistrationMode, RegisterProviderBot,
};

pub async fn provider_registration_core_service_contract_tests(
    core: &dyn ProviderRegistrationCoreService,
    provider_id: &str,
    owner: &str,
) {
    let modes = core.authorize(provider_id, owner).await.unwrap();
    assert!(modes.contains(&ProviderRegistrationMode::Upstream));
    assert!(modes.contains(&ProviderRegistrationMode::Gateway));
    assert!(matches!(
        core.authorize(provider_id, "not-provider-owner").await,
        Err(ServiceError::Forbidden(_))
    ));
    let request = RegisterProviderBot {
        provider_id: provider_id.into(),
        provider_bot_ref: "contract-upstream".into(),
        owner: owner.into(),
        mode: ProviderRegistrationMode::Upstream,
        bot_name: "Contract bot".into(),
        webhook_url: None,
    };
    let first = core.register(request.clone()).await.unwrap();
    assert_eq!(first.record.owner, owner);
    assert!(!first.record.bot_token.is_empty());
    assert!(!first.record.bot_token.starts_with("MOCK_"));
    assert!(first.effective_webhook_url.is_none());
    assert!(matches!(core.register(request.clone()).await, Err(ServiceError::Conflict(_))));
    let mut changed = request.clone();
    changed.bot_name = "Changed bot".into();
    assert!(matches!(
        core.register(changed).await,
        Err(ServiceError::Conflict(_))
    ));
    let mut downlink = request;
    downlink.provider_bot_ref = "contract-gateway".into();
    downlink.mode = ProviderRegistrationMode::Gateway;
    assert!(core.register(downlink.clone()).await.is_err());
    downlink.webhook_url = Some("https://bot.example.com/hook".into());
    let registered = core.register(downlink.clone()).await.unwrap();
    assert_eq!(registered.record.webhook_url, downlink.webhook_url);
    assert_eq!(registered.effective_webhook_url, downlink.webhook_url);
}
