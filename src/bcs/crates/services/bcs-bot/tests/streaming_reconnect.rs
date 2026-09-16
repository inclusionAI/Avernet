//! A reconnect must never succeed while the previous streaming slot is occupied.
use std::sync::Arc;

use bcs_bot::core::BotCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::{
    BotConnectParams, BotRegistryCoreService, BotRepoPort, ConnectError, ConnectionKind,
};

fn params(token: Option<String>) -> BotConnectParams {
    BotConnectParams {
        bot_id: Some("reconnect-bot".into()),
        token,
        protocol_version: Some(2),
        client_kind: None,
    }
}

#[tokio::test]
async fn occupied_streaming_slot_rejects_reconnect_until_disconnect() {
    let dir = tempfile::tempdir().unwrap();
    let repo = Arc::new(MemoryBotRepo::with_base_dir(dir.path().to_path_buf()));
    let core = BotCore::with_repo(repo.clone());
    let first = core.connect_bot(params(None), ConnectionKind::Streaming).await.unwrap();

    let rejected = core
        .connect_bot(params(Some(first.token.clone())), ConnectionKind::Streaming)
        .await;
    assert!(matches!(rejected, Err(ConnectError::AlreadyConnected(id)) if id == first.bot_uuid));
    assert!(repo.is_connected(&first.bot_uuid).await);
    assert_eq!(repo.find_bot_by_token(&first.token).await, Some(first.bot_uuid.clone()));

    repo.disconnect_streaming(&first.bot_uuid).await;
    assert!(!repo.is_connected(&first.bot_uuid).await);
    let (left, right) = tokio::join!(
        core.connect_bot(params(Some(first.token.clone())), ConnectionKind::Streaming),
        core.connect_bot(params(Some(first.token.clone())), ConnectionKind::Streaming),
    );
    let (accepted, rejected) = match (left, right) {
        (Ok(accepted), Err(rejected)) | (Err(rejected), Ok(accepted)) => (accepted, rejected),
        other => panic!("exactly one reconnect must succeed: {other:?}"),
    };
    assert!(matches!(rejected, ConnectError::AlreadyConnected(_)));
    assert!(!accepted.is_new);
    assert_eq!(accepted.bot_uuid, first.bot_uuid);
    assert_eq!(accepted.token, first.token);
    assert!(repo.is_connected(&accepted.bot_uuid).await);
}
