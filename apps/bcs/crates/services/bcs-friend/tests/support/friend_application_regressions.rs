use super::*;
use bcs_service_api::{CreateFriendRequestCommand, FriendRequestDecisionCommand, ListFriendRequestsCommand, FriendUseCaseError};

#[tokio::test]
async fn coverage_friend_application_enforces_receiver_before_accept_or_reject() {
    let bots = [("alice", "protected", ActorKind::Bot), ("bob", "protected", ActorKind::Bot), ("carol", "protected", ActorKind::Bot)];
    let (friends, requests, repo) = core_fixture(&bots);
    let service = Friend::new(Arc::new(StaticRegistry::new(&bots)), friends.clone(), requests, Arc::new(NoopRelationCoreService));
    let request = service.create_friend_request(CreateFriendRequestCommand {
        caller_actor_id: "alice".into(), to_bot: "bob".into(), request_auth: None,
    }).await.unwrap();
    assert_eq!(service.friend_request_receiver(&request.id).await.unwrap(), "bob");
    let decision = FriendRequestDecisionCommand { caller_actor_id: "carol".into(), request_id: request.id.clone(), request_to_bot: None, request_auth: None };
    assert!(matches!(service.accept_friend_request(decision.clone()).await, Err(FriendUseCaseError::Forbidden(_))));
    assert!(matches!(service.reject_friend_request(decision.clone()).await, Err(FriendUseCaseError::Forbidden(_))));
    assert_eq!(repo.get_request(&request.id).await.unwrap().status, FriendRequestStatus::Pending);
    assert!(!friends.are_friends("alice", "bob").await);
    service.accept_friend_request(FriendRequestDecisionCommand { caller_actor_id: "bob".into(), ..decision }).await.unwrap();
    assert!(friends.are_friends("alice", "bob").await);
    let received = service.list_friend_requests(ListFriendRequestsCommand {
        caller_actor_id: "bob".into(), direction: FriendRequestDirection::Received, status_filter: Some(FriendRequestStatus::Accepted),
    }).await.unwrap();
    assert_eq!(received.len(), 1); assert_eq!(received[0].id, request.id);
    let pending = service.create_friend_request(CreateFriendRequestCommand {
        caller_actor_id: "alice".into(), to_bot: "carol".into(), request_auth: None,
    }).await.unwrap();
    service.reject_friend_request(FriendRequestDecisionCommand { caller_actor_id: "carol".into(),
        request_id: pending.id.clone(), request_to_bot: Some("carol".into()), request_auth: None }).await.unwrap();
    assert_eq!(repo.get_request(&pending.id).await.unwrap().status, FriendRequestStatus::Rejected);
    assert!(!friends.are_friends("alice", "carol").await);
    assert!(service.friend_request_receiver("missing").await.is_err());
}

#[tokio::test]
async fn coverage_friend_application_hides_private_targets_and_forbids_other_actors_lists() {
    let bots = [("alice", "protected", ActorKind::Bot), ("private", "private", ActorKind::Bot)];
    let (friends, requests, repo) = core_fixture(&bots);
    let service = Friend::new(Arc::new(StaticRegistry::new(&bots)), friends, requests, Arc::new(NoopRelationCoreService));
    for target in ["private", "missing"] {
        assert!(matches!(service.create_friend_request(CreateFriendRequestCommand {
            caller_actor_id: "alice".into(), to_bot: target.into(), request_auth: None,
        }).await, Err(FriendUseCaseError::Service(ServiceError::BotNotFound(_)))));
        assert!(matches!(service.list_friends(ListFriendsCommand {
            caller_actor_id: "alice".into(), target_actor_id: target.into(),
        }).await, Err(FriendUseCaseError::Service(ServiceError::BotNotFound(_)))));
    }
    assert!(matches!(service.list_friends(ListFriendsCommand {
        caller_actor_id: "someone-else".into(), target_actor_id: "alice".into(),
    }).await, Err(FriendUseCaseError::Forbidden(_))));
    assert!(repo.list_requests("alice", FriendRequestDirection::Sent, None).await.is_empty());
    assert!(service.list_friends(ListFriendsCommand { caller_actor_id: "human_1".into(), target_actor_id: "human_1".into() }).await.unwrap().is_empty());
}
