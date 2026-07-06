use bcs_ws::bot::BotConnectionRegistry;
use tokio::sync::mpsc;

#[tokio::test]
async fn set_token_expires_at_stores_value() {
    let registry = BotConnectionRegistry::new();
    let (tx, _rx) = mpsc::channel(1);
    registry.connect("bot-1".to_string(), tx).await;

    registry.set_token_expires_at("bot-1", 1700000000).await;

    // Verify via collect_expiring: token not expired yet
    let expired = registry.collect_expiring(1699999999, 0).await;
    assert!(expired.is_empty());

    // Now it's expired
    let expired = registry.collect_expiring(1700000001, 0).await;
    assert_eq!(expired, vec!["bot-1".to_string()]);
}

#[tokio::test]
async fn set_token_expires_at_nonexistent_bot_is_noop() {
    let registry = BotConnectionRegistry::new();
    // Should not panic
    registry.set_token_expires_at("ghost", 1700000000).await;
    let expired = registry.collect_expiring(1800000000, 0).await;
    assert!(expired.is_empty());
}

#[tokio::test]
async fn collect_expiring_with_early_disconnect() {
    let registry = BotConnectionRegistry::new();
    let (tx, _rx) = mpsc::channel(1);
    registry.connect("bot-1".to_string(), tx).await;
    registry.set_token_expires_at("bot-1", 1000).await;

    // now=900, early=50 → 900+50=950 >= 1000? No → not expiring yet
    let expiring = registry.collect_expiring(900, 50).await;
    assert!(expiring.is_empty());

    // now=960, early=50 → 960+50=1010 >= 1000? Yes → expiring soon
    let expiring = registry.collect_expiring(960, 50).await;
    assert_eq!(expiring, vec!["bot-1".to_string()]);
}

#[tokio::test]
async fn collect_expiring_skips_none_expires() {
    let registry = BotConnectionRegistry::new();
    let (tx1, _rx1) = mpsc::channel(1);
    let (tx2, _rx2) = mpsc::channel(1);
    registry.connect("bot-with-exp".to_string(), tx1).await;
    registry.connect("bot-no-exp".to_string(), tx2).await;

    registry.set_token_expires_at("bot-with-exp", 1000).await;
    // bot-no-exp has no token_expires_at set

    let expired = registry.collect_expiring(2000, 0).await;
    assert_eq!(expired, vec!["bot-with-exp".to_string()]);
}

#[tokio::test]
async fn disconnect_removes_connections() {
    let registry = BotConnectionRegistry::new();
    let (tx1, _rx1) = mpsc::channel(1);
    let (tx2, _rx2) = mpsc::channel(1);
    let (tx3, _rx3) = mpsc::channel(1);
    registry.connect("bot-a".to_string(), tx1).await;
    registry.connect("bot-b".to_string(), tx2).await;
    registry.connect("bot-c".to_string(), tx3).await;

    registry.disconnect("bot-a").await;
    registry.disconnect("bot-c").await;

    assert!(!registry.is_connected("bot-a").await);
    assert!(registry.is_connected("bot-b").await);
    assert!(!registry.is_connected("bot-c").await);
}

#[tokio::test]
async fn disconnect_drops_sender_closes_channel() {
    let registry = BotConnectionRegistry::new();
    let (tx, mut rx) = mpsc::channel::<String>(1);
    registry.connect("bot-x".to_string(), tx).await;

    registry.disconnect("bot-x").await;

    // Receiver should get None (channel closed)
    assert!(rx.recv().await.is_none());
}

#[tokio::test]
async fn disconnect_nonexistent_bot_is_noop() {
    let registry = BotConnectionRegistry::new();
    // Should not panic
    registry.disconnect("ghost").await;
}

#[tokio::test]
async fn collect_expiring_multiple_bots() {
    let registry = BotConnectionRegistry::new();
    let (tx1, _rx1) = mpsc::channel(1);
    let (tx2, _rx2) = mpsc::channel(1);
    let (tx3, _rx3) = mpsc::channel(1);
    registry.connect("expired-1".to_string(), tx1).await;
    registry.connect("expired-2".to_string(), tx2).await;
    registry.connect("still-valid".to_string(), tx3).await;

    registry.set_token_expires_at("expired-1", 1000).await;
    registry.set_token_expires_at("expired-2", 900).await;
    registry.set_token_expires_at("still-valid", 5000).await;

    let mut expired = registry.collect_expiring(2000, 0).await;
    expired.sort();
    assert_eq!(expired, vec!["expired-1".to_string(), "expired-2".to_string()]);
}
