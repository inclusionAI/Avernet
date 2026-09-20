//! Shared conformance for the independent registration journal (Rule 25).

use bcs_service_api::port::repo::provider_registration::ProviderRegistrationRepoPort;
use bcs_service_api::types::provider_registration::{
    ProviderRegistrationMode, ProviderRegistrationRecord,
};

fn candidate(index: usize) -> ProviderRegistrationRecord {
    ProviderRegistrationRecord {
        provider_id: "Provider".into(),
        provider_bot_ref: "Ref".into(),
        owner: format!("owner-{index}"),
        mode: ProviderRegistrationMode::Upstream,
        bot_name: format!("Bot {index}"),
        bot_uuid: format!("bot-{index}"),
        bot_token: format!("test-runtime-credential-{index}"),
        webhook_url: None,
        completed: false,
    }
}

/// Run against an empty, environment-scoped repository. The driver constructs
/// its concrete implementation and applies its schema before invoking this
/// harness. Record assertions deliberately avoid printing runtime credentials.
pub async fn provider_registration_repo_port_contract_tests<
    T: ProviderRegistrationRepoPort + ?Sized,
>(
    repo: &T,
) {
    assert!(repo.get("Provider", "Ref").await.unwrap().is_none());
    assert!(repo.complete("Provider", "Ref").await.is_err());
    let original = candidate(0);
    assert!(repo.reserve(original.clone()).await.unwrap() == original);
    assert!(repo.get("Provider", "Ref").await.unwrap().unwrap() == original);

    let mut competing = candidate(1);
    competing.mode = ProviderRegistrationMode::Gateway;
    competing.webhook_url = Some("https://bot.example.com/hook".into());
    competing.completed = true;
    assert!(repo.reserve(competing).await.unwrap() == original);

    let mut collision = candidate(0);
    collision.provider_bot_ref = "Other".into();
    assert!(repo.reserve(collision).await.is_err());
    assert!(repo.get("Provider", "Other").await.unwrap().is_none());

    repo.complete("Provider", "Ref").await.unwrap();
    repo.complete("Provider", "Ref").await.unwrap();
    let mut completed = original;
    completed.completed = true;
    assert!(repo.get("Provider", "Ref").await.unwrap().unwrap() == completed);
    assert!(repo.reserve(candidate(2)).await.unwrap() == completed);
    assert!(repo.complete("Provider", "missing").await.is_err());

    for (index, provider, reference) in [(3, "provider", "Ref"), (4, "Provider", "ref")] {
        let mut record = candidate(index);
        record.provider_id = provider.into();
        record.provider_bot_ref = reference.into();
        assert!(repo.reserve(record.clone()).await.unwrap() == record);
        assert!(repo.get(provider, reference).await.unwrap().unwrap() == record);
    }
    let mut binary_uuid = candidate(5);
    binary_uuid.bot_uuid = "BOT-0".into();
    binary_uuid.provider_bot_ref = "binary-uuid".into();
    assert!(repo.reserve(binary_uuid.clone()).await.unwrap() == binary_uuid);

    let mut bounds = candidate(6);
    bounds.provider_id = "P".repeat(128);
    bounds.provider_bot_ref = "R".repeat(128);
    bounds.owner = "owner".repeat(128);
    assert!(repo.reserve(bounds.clone()).await.unwrap() == bounds);
}
