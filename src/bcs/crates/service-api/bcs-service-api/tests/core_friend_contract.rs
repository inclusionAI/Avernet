use bcs_service_api::{FriendCoreService, ServiceError};
use bcs_test_support::NoopFriendCoreService;

#[tokio::test]
async fn noop_friend_queries_are_empty_and_fail_closed() {
    let service = NoopFriendCoreService::default();

    assert!(service.list_friends("bot-1").await.is_empty());
    assert!(!service.are_friends("bot-1", "bot-2").await);
    service.are_all_friends("bot-1", &[]).await.unwrap();

    let err = service
        .are_all_friends("bot-1", &["bot-2".to_string(), "bot-3".to_string()])
        .await
        .unwrap_err();
    assert!(matches!(
        err,
        ServiceError::NotFriends(non_friends)
            if non_friends == vec!["bot-2".to_string(), "bot-3".to_string()]
    ));
}

#[tokio::test]
async fn noop_friend_mutations_are_benign_and_stateless() {
    let service = NoopFriendCoreService::default();

    service.add_friendship("bot-1", "bot-2").await.unwrap();
    assert_eq!(service.remove_all_friendships("bot-1").await.unwrap(), 0);
    assert!(service.list_friends("bot-1").await.is_empty());
    assert!(!service.are_friends("bot-1", "bot-2").await);
}
