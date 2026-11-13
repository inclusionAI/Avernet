//! Controlled lock interleaving; no timing-dependent stress loop.
#![allow(clippy::unwrap_used)]
use super::*;
use bcs_service_api::port::repo::BotRepoPort;

#[tokio::test]
async fn mine_waiting_for_authority_does_not_hold_bots() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(dir.path().into());
    repo.register_with_owner_and_token("bot".into(), BotCapabilities::default(), "alice", "token")
        .await.unwrap();
    // Initialization owns authority first. Poll the actual mine query until it
    // blocks on that lock, then attempt the next lock initialization needs.
    let authority = repo.authority.write().await;
    let mut mine = Box::pin(repo.list_uninitialized_bots_by_creator("alice", 100));
    std::future::poll_fn(|cx| {
        assert!(mine.as_mut().poll(cx).is_pending());
        std::task::Poll::Ready(())
    }).await;
    assert!(repo.bots.try_write().is_ok(), "mine must not hold bots while waiting for authority");
    drop(authority);
    assert_eq!(mine.await.unwrap().len(), 1);
}
