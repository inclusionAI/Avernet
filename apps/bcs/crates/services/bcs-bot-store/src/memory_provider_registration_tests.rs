//! Controlled projection-lock ordering during ordinary multi-user admission.
#![allow(clippy::unwrap_used)]
use super::*;
use bcs_service_api::types::bot_authority::{AuthorityActor, AuthorityMutation, OwnershipInitialization};

#[tokio::test]
async fn owned_registration_waits_for_binding_before_taking_ref_index() {
    let dir = tempfile::tempdir().unwrap();
    let bots = Arc::new(MemoryBotRepo::with_base_dir(dir.path().into()));
    let bindings = Arc::new(MemoryProviderStore::new());
    let repo = MemoryBotProviderStore::new(bots, bindings.clone());
    // A single-owner insert_binding already owns this lock, and next needs refs.
    let binding_guard = bindings.bindings_by_bot.write().await;
    let record = BotProviderRecord {bot_uuid: "fresh".into(), provider_id: "provider".into(),
        provider_bot_ref: "ref".into(), connection_mode: BotConnectionMode::Gateway, webhook_url: None, is_deleted: false};
    let initialization = OwnershipInitialization {owner_user_id: "alice".into(), initial_manager_user_ids: vec!["bob".into()],
        mutation: AuthorityMutation {mutation_id: "init".into(), actor: AuthorityActor::Provider {provider_id: "provider".into()}}};
    let mut registration = Box::pin(repo.create_provider_admin_bot_with_ownership(record, BotCapabilities::default(),
        "alice", "token", initialization));
    std::future::poll_fn(|cx| {
        assert!(registration.as_mut().poll(cx).is_pending());
        std::task::Poll::Ready(())
    }).await;
    assert!(bindings.binding_ref_index.try_write().is_ok(), "registration must not pin refs before bindings");
    drop(binding_guard);
    registration.await.unwrap();
}
