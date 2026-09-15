use super::*;
use bcs_service_api::{BotConnectParams, ConnectError};

fn params(id: Option<&str>, token: Option<&str>) -> BotConnectParams {
    BotConnectParams {
        bot_id: id.map(str::to_owned),
        token: token.map(str::to_owned),
        ..Default::default()
    }
}

#[tokio::test]
async fn local_temporary_registration_obeys_expiry_and_token_ownership() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(dir.path().into());
    let first = repo
        .connect_streaming(params(Some("temporary"), None))
        .await
        .unwrap();
    repo.disconnect_streaming("temporary").await;
    assert!(matches!(
        repo.connect_streaming(params(Some("temporary"), None))
            .await,
        Err(ConnectError::AlreadyRegistered(_))
    ));
    let same = repo
        .connect_streaming(params(None, Some(&first.token)))
        .await
        .unwrap();
    assert!(!same.is_new);
    assert_eq!(same.token, first.token);
    repo.disconnect_streaming("temporary").await;
    repo.bots
        .write()
        .await
        .get_mut("temporary")
        .unwrap()
        .last_heartbeat = Instant::now() - BOT_EXPIRY - Duration::from_secs(1);
    let replacement = repo
        .connect_streaming(params(Some("temporary"), Some(&first.token)))
        .await
        .unwrap();
    assert!(replacement.is_new);
    assert_ne!(replacement.token, first.token);
    assert!(!repo.token_to_bot.read().await.contains_key(&first.token));
    assert_eq!(
        repo.find_bot_by_token(&replacement.token).await.as_deref(),
        Some("temporary")
    );
}

#[tokio::test]
async fn local_persisted_identity_survives_restart_and_cannot_be_claimed_without_token() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(dir.path().into());
    repo.register_with_owner_and_token(
        "saved".into(),
        BotCapabilities {
            name: Some("Saved name".into()),
            ..Default::default()
        },
        "owner",
        "saved-token",
    )
    .await
    .unwrap();
    let cold = MemoryBotRepo::with_base_dir(dir.path().into());
    assert!(matches!(
        cold.connect_streaming(params(Some("saved"), None)).await,
        Err(ConnectError::AlreadyRegistered(_))
    ));
    let result = cold
        .connect_streaming(params(None, Some("saved-token")))
        .await
        .unwrap();
    assert!(!result.is_new);
    assert_eq!(
        cold.get("saved")
            .await
            .unwrap()
            .capabilities
            .name
            .as_deref(),
        Some("Saved name")
    );
    assert!(cold.soft_delete("saved").await);
    assert!(matches!(
        cold.connect_streaming(params(Some("saved"), Some("saved-token")))
            .await,
        Err(ConnectError::AlreadyRegistered(_))
    ));
}

#[tokio::test]
async fn local_mock_promotion_is_durable_and_preserves_hidden_status() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(dir.path().into());
    repo.register_with_owner_and_token(
        "mock".into(),
        BotCapabilities::default(),
        "owner",
        "MOCK_test",
    )
    .await
    .unwrap();
    repo.update_actor_status("mock", bcs_service_api::ActorStatus::Hidden)
        .await
        .unwrap();
    let result = repo
        .connect_streaming(params(Some("mock"), None))
        .await
        .unwrap();
    assert!(result.is_new);
    assert!(!bcs_service_api::is_mock_token(&result.token));
    assert_eq!(
        repo.bots.read().await["mock"].status,
        bcs_service_api::ActorStatus::Hidden
    );
    let cold = MemoryBotRepo::with_base_dir(dir.path().into());
    let restored = cold
        .connect_streaming(params(None, Some(&result.token)))
        .await
        .unwrap();
    assert!(!restored.is_new);
    assert_eq!(restored.bot_uuid, "mock");
}

#[tokio::test]
async fn corrupt_local_identity_never_becomes_a_new_registration() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(dir.path().into());
    let path = repo.bot_info_path("broken");
    fs::create_dir_all(path.parent().unwrap()).await.unwrap();
    fs::write(path, "{broken").await.unwrap();
    assert!(matches!(
        repo.connect_streaming(params(Some("broken"), None)).await,
        Err(ConnectError::InternalError(_))
    ));
    assert!(repo.bots.read().await.is_empty());
}
