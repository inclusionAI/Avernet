//! Shared SQL/local conformance for request-scoped registry identity operations.
use bcs_service_api::port::repo::{BotIdentity, BotIdentityUpdate, BotRepoPort};
use bcs_service_api::{ActorKind, ActorStatus, BotCapabilities};

pub async fn bot_identity_operation_port_contract_tests(repo: &dyn BotRepoPort) {
    let mut op = repo.begin_identity_operation();
    assert!(op.stored_identity().await.is_err()); // an unlocked read is not a miss
    assert!(op.lock_identity("scope-temporary").await.unwrap().is_none());
    assert!(op.stored_identity().await.unwrap().is_none());
    assert!(op.stored_identity().await.unwrap().is_none());
    op.apply(BotIdentityUpdate {
        identity: BotIdentity {
            id: "scope-temporary".into(),
            token: None,
            deleted: false,
            capabilities: BotCapabilities::default(),
            env: None,
            created_by: None,
            actor_kind: ActorKind::Bot,
            status: ActorStatus::Online,
        },
        token: "scope-temporary-token".into(),
        replace_persistent_token: false,
    })
    .await
    .unwrap();
    assert!(repo.is_connected("scope-temporary").await);
    repo.disconnect_streaming("scope-temporary").await;
    let mut op = repo.begin_identity_operation();
    assert_eq!(
        op.token_owner("scope-temporary-token")
            .await
            .unwrap()
            .as_deref(),
        Some("scope-temporary")
    );
    let memory = op.lock_identity("scope-temporary").await.unwrap().unwrap();
    assert!(!memory.connected);
    assert_eq!(
        memory.identity.token.as_deref(),
        Some("scope-temporary-token")
    );
    assert!(op.stored_identity().await.unwrap().is_none());
    drop(op);

    repo.register_with_owner_and_token(
        "scope-durable".into(),
        BotCapabilities::default(),
        "owner",
        "scope-old-token",
    )
    .await
    .unwrap();
    let mut op = repo.begin_identity_operation();
    assert!(op
        .token_owner("scope-unknown-token")
        .await
        .unwrap()
        .is_none());
    op.lock_identity("scope-durable").await.unwrap();
    let identity = op.stored_identity().await.unwrap().unwrap();
    assert_eq!(identity.token.as_deref(), Some("scope-old-token"));
    op.apply(BotIdentityUpdate {
        identity,
        token: "scope-new-token".into(),
        replace_persistent_token: true,
    })
    .await
    .unwrap();
    assert_eq!(
        repo.load_token("scope-durable").await.as_deref(),
        Some("scope-new-token")
    );
    assert!(repo.is_connected("scope-durable").await);
    assert!(repo.soft_delete("scope-durable").await);
    let mut op = repo.begin_identity_operation();
    op.lock_identity("scope-durable").await.unwrap();
    assert!(op.stored_identity().await.unwrap().unwrap().deleted);
}
