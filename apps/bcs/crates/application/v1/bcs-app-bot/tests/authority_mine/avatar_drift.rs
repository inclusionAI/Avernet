use super::*;

async fn seed(fixture: &Fixture) {
    for id in ["bot-a", "bot-b", "bot-c"] {
        fixture.add_controlled_bot(id, "mine-owner", ActorStatus::Online).await;
    }
    // Created-desc pins the page compositions
    // (a 3000 > b 2000 > c 1000) regardless of the
    // registration clock resolution.
    fixture.set_created_at("bot-a", 3_000).await;
    fixture.set_created_at("bot-b", 2_000).await;
    fixture.set_created_at("bot-c", 1_000).await;
}

fn corrupt_avatar(fixture: &Fixture, id: &str) {
    let dir = fixture._temp.path().join(id);
    std::fs::create_dir_all(&dir).expect("profile directory");
    std::fs::write(dir.join("bot.json"), "invalid JSON").expect("corrupt profile");
}

#[tokio::test]
async fn persistent_deletion_drift_skips_corrupt_avatar_and_returns_conflict() {
    let fixture = Fixture::new_with_drift(Some((i64::MAX, vec!["bot-a".into()])), None).await;
    seed(&fixture).await;
    // A disappears from backfill while B remains with an unreadable profile.
    corrupt_avatar(&fixture, "bot-b");
    let error = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 0, 2)
        .await.expect_err("persistent drift");
    assert_eq!(error.code(), "bot_list_changed");
    assert_eq!(fixture.counting_repo.observations(), 2);
    assert!(fixture.counting_repo.avatar_batches.lock().expect("batches").is_empty());
}

#[tokio::test]
async fn changed_retry_composition_skips_corrupt_avatar_and_returns_conflict() {
    let fixture = Fixture::new_with_role_flip(None, Some((2, "bot-a".into())), None).await;
    seed(&fixture).await;
    // First page A/B drifts; second page B/C is locally stable but differs
    // from the first composition. Neither page may read B's corrupt avatar.
    corrupt_avatar(&fixture, "bot-b");
    let error = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 0, 2)
        .await.expect_err("composition changed");
    assert_eq!(error.code(), "bot_list_changed");
    assert_eq!(fixture.counting_repo.observations(), 2);
    assert!(fixture.counting_repo.avatar_batches.lock().expect("batches").is_empty());
}

#[tokio::test]
async fn recovered_role_drift_reads_avatars_once_for_the_accepted_page() {
    let fixture = Fixture::new_with_role_flip(
        None, Some((2, "bot-a".into())), Some((3, "bot-a".into())),
    ).await;
    seed(&fixture).await;
    let page = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 0, 2)
        .await.expect("recovered page");
    assert_eq!(physicals(&page), vec!["bot-a", "bot-b"]);
    assert_eq!(fixture.counting_repo.observations(), 2);
    let batches = fixture.counting_repo.avatar_batches.lock().expect("batches");
    assert_eq!(*batches, vec![vec!["bot-a".to_string(), "bot-b".to_string()]]);
}

#[tokio::test]
async fn accepted_retry_page_still_propagates_avatar_read_failure() {
    let fixture = Fixture::new_with_role_flip(
        None, Some((2, "bot-a".into())), Some((3, "bot-a".into())),
    ).await;
    seed(&fixture).await;
    corrupt_avatar(&fixture, "bot-b");
    let error = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 0, 2)
        .await.expect_err("accepted page avatar failure");
    assert_eq!(error.code(), "internal_error");
    assert_eq!(fixture.counting_repo.observations(), 2, "retry precedes avatar read");
    assert_eq!(fixture.counting_repo.avatar_batches.lock().expect("batches").len(), 1);
}
