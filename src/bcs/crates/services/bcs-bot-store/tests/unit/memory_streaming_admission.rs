use super::*;
use bcs_service_api::port::repo::{BotIdentity, BotIdentityUpdate};

#[tokio::test]
async fn local_scope_preserves_stale_facts_and_replaces_token_indices() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(dir.path().into());
    repo.register_streaming_connection("temporary".into())
        .await
        .unwrap();
    repo.disconnect_streaming("temporary").await;
    repo.bots
        .write()
        .await
        .get_mut("temporary")
        .unwrap()
        .last_heartbeat = Instant::now() - BOT_EXPIRY - Duration::from_secs(1);
    let mut op = repo.begin_identity_operation();
    let memory = op.lock_identity("temporary").await.unwrap().unwrap();
    assert!(memory.last_heartbeat.elapsed() > BOT_EXPIRY);
    assert!(!memory.connected);
    assert!(op.stored_identity().await.unwrap().is_none());
    assert!(op.stored_identity().await.unwrap().is_none());
    op.apply(BotIdentityUpdate {
        identity: memory.identity,
        token: "replacement".into(),
        replace_persistent_token: false,
    })
    .await
    .unwrap();
    assert_eq!(repo.token_to_bot.read().await.len(), 1);
    assert_eq!(
        repo.load_token("temporary").await.as_deref(),
        Some("replacement")
    );
}

#[tokio::test]
async fn local_scope_returns_deleted_facts_instead_of_a_business_rejection() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(dir.path().into());
    repo.register_with_owner_and_token(
        "saved".into(),
        BotCapabilities::default(),
        "owner",
        "real-token",
    )
    .await
    .unwrap();
    assert!(repo.soft_delete("saved").await);
    let mut op = repo.begin_identity_operation();
    op.lock_identity("saved").await.unwrap();
    assert!(op.stored_identity().await.unwrap().unwrap().deleted);
}

#[tokio::test]
async fn corrupt_file_is_an_error_not_a_memoized_miss() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(dir.path().into());
    let path = repo.bot_info_path("broken");
    fs::create_dir_all(path.parent().unwrap()).await.unwrap();
    fs::write(&path, "{broken").await.unwrap();
    let mut op = repo.begin_identity_operation();
    op.lock_identity("broken").await.unwrap();
    assert!(op.stored_identity().await.is_err());
    assert!(repo.bots.read().await.is_empty());
    fs::remove_file(&path).await.unwrap();
    assert!(op.stored_identity().await.unwrap().is_none());
}

#[tokio::test]
async fn local_failed_token_write_preserves_memory() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(dir.path().into());
    repo.register_with_owner_and_token(
        "saved".into(),
        BotCapabilities::default(),
        "owner",
        "old-token",
    )
    .await
    .unwrap();
    let mut op = repo.begin_identity_operation();
    op.lock_identity("saved").await.unwrap();
    let identity: BotIdentity = op.stored_identity().await.unwrap().unwrap();
    let path = repo.bot_info_path("saved");
    fs::remove_file(&path).await.unwrap();
    fs::create_dir(&path).await.unwrap();
    assert!(op
        .apply(BotIdentityUpdate {
            identity,
            token: "new-token".into(),
            replace_persistent_token: true
        })
        .await
        .is_err());
    assert!(!repo.is_connected("saved").await);
    assert_eq!(repo.load_token("saved").await.as_deref(), Some("old-token"));
}
