use super::*;
#[tokio::test]
async fn db_failure_must_not_be_an_empty_success() {
    let (eg, pp, rq, bc, db) = assemble().await;
    let connect = Arc::new(service(&eg, &pp, &rq, &bc));
    let facade = EdgeConnectFriendService::new(
        connect,
        Arc::new(BotCore::memory()),
    );
    db.execute(DbStatement::new("DROP TABLE permission_requests"))
        .await.unwrap();
    for direction in [FriendRequestDirection::Received, FriendRequestDirection::Sent, FriendRequestDirection::All] {
        let result = facade.list_friend_requests(ListFriendRequestsCommand {
            caller_actor_id: "botB".into(), direction, status_filter: None,
        }).await;
        assert!(result.is_err(), "storage failure returned: {result:?}");
    }
}

#[tokio::test]
async fn non_connect_request_cannot_be_rejected_as_friend_request() {
    let fx = fixture(&[TestBot::bot("botA"), TestBot::owned("botB", "owner1")]).await;
    let pending = fx.facade.create_friend_request(create("botA", "botB")).await.unwrap();
    let mut rules = fx.requests.get(&pending.id, "dev").await.unwrap();
    rules.request_id = "rules-only-request".into();
    rules.request_kind = RequestKind::Rules;
    fx.requests.insert(rules).await.unwrap();
    let result = fx.facade.reject_friend_request(decision("botB", "rules-only-request")).await;
    let row = fx.requests.get("rules-only-request", "dev").await.unwrap();
    assert!(result.is_err(), "friend endpoint returned {result:?} and changed Rules request to {:?}", row.status);
}

#[tokio::test]
async fn accepted_human_friend_must_remain_in_legacy_list() {
    for strategy in ["APPROVAL", "OPEN"] {
        let (eg, pp, rq, bc, db) = assemble().await;
        let registry = Arc::new(BotCore::memory());
        registry.register("botB".into(), BotCapabilities { visibility: "protected".into(), ..Default::default() }).await.unwrap();
        seed_bot(&db, "botB", "protected", "protected", strategy, "online", Some("owner1")).await;
        let friend_repo = Arc::new(bcs_friend::MemoryFriendRepo::new());
        let relation = Arc::new(bcs_relation::RelationCore::memory());
        let writeback = Arc::new(bcs_friend::FriendshipWritebackService::new(friend_repo, relation));
        let connect = Arc::new(service_with_writeback(&eg, &pp, &rq, &bc, writeback));
        let facade = EdgeConnectFriendService::new(connect.clone(), registry);
        let pending = facade.create_friend_request(create("human_alice", "botB")).await.unwrap();
        facade.accept_friend_request(decision("botB", &pending.id)).await.unwrap();
        assert_eq!(connect.list_friends("human_alice").await.unwrap().len(), 1, "new store has friendship");
        let legacy = facade.list_friends(ListFriendsCommand {
            caller_actor_id: "human_alice".into(), target_actor_id: "human_alice".into(),
        }).await.unwrap();
        assert_eq!(legacy.len(), 1, "accepted friendship disappeared from legacy list: {legacy:?}");
        connect.revoke_friend("human_alice", "botB", None).await.unwrap();
        // A delayed retry of a previously approved request must not undo revoke.
        facade.accept_friend_request(decision("botB", &pending.id)).await.unwrap();
        assert!(!eg.has_friend_edge("human_alice", "botB", "dev").await);
        assert!(facade.list_friends(ListFriendsCommand {
            caller_actor_id: "human_alice".into(), target_actor_id: "human_alice".into(),
        }).await.unwrap().is_empty());
    }
}

#[tokio::test]
async fn all_non_connect_kinds_are_rejected_before_any_decision() {
    let fx = fixture(&[TestBot::bot("botA"), TestBot::owned("botB", "owner1")]).await;
    let pending = fx.facade.create_friend_request(create("botA", "botB")).await.unwrap();
    for kind in [RequestKind::Rules, RequestKind::PermissionProfile, RequestKind::Revoke] {
        for status in [RequestStatus::Pending, RequestStatus::Approved] {
            let mut row = fx.requests.get(&pending.id, "dev").await.unwrap();
            row.request_id = format!("non-connect-{kind:?}-{status:?}");
            row.request_kind = kind;
            row.status = status;
            let id = row.request_id.clone();
            fx.requests.insert(row).await.unwrap();
            for hint in [None, Some("botB".to_string())] {
                let mut command = decision("botB", &id);
                command.request_to_bot = hint;
                assert!(matches!(fx.facade.accept_friend_request(command.clone()).await,
                    Err(FriendUseCaseError::Service(ServiceError::FriendRequestNotFound(_)))));
                assert!(matches!(fx.facade.reject_friend_request(command).await,
                    Err(FriendUseCaseError::Service(ServiceError::FriendRequestNotFound(_)))));
                assert_eq!(fx.requests.get(&id, "dev").await.unwrap().status, status);
            }
        }
    }
    assert!(fx.writeback.calls().is_empty(), "non-Connect decisions must not create friendships");
}

#[tokio::test]
async fn receiver_hint_cannot_override_actual_receiver() {
    let fx = fixture(&[TestBot::bot("botA"), TestBot::owned("botB", "owner1")]).await;
    let pending = fx.facade.create_friend_request(create("botA", "botB")).await.unwrap();
    let mut forged = decision("botA", &pending.id);
    forged.request_to_bot = Some("botA".into());
    assert!(matches!(fx.facade.accept_friend_request(forged.clone()).await, Err(FriendUseCaseError::Forbidden(_))));
    assert!(matches!(fx.facade.reject_friend_request(forged).await, Err(FriendUseCaseError::Forbidden(_))));
    assert_eq!(fx.requests.get(&pending.id, "dev").await.unwrap().status, RequestStatus::Pending);
}

#[tokio::test]
async fn human_mirror_failure_is_reported_and_idempotent_create_repairs_it() {
    let (eg, pp, rq, bc, db) = assemble().await;
    seed_bot(&db, "botB", "protected", "protected", "OPEN", "online", Some("owner1")).await;
    let failing = service_with_writeback(&eg, &pp, &rq, &bc, Arc::new(FailingWriteback));
    assert!(failing.create_connect("human_alice", "botB", None, None).await.is_err());
    assert!(eg.has_friend_edge("human_alice", "botB", "dev").await);
    let recorder = Arc::new(RecordingWriteback::default());
    let recovered = service_with_writeback(&eg, &pp, &rq, &bc, recorder.clone());
    let result = recovered.create_connect("human_alice", "botB", None, None).await.unwrap();
    assert_eq!(result.status, ConnectStatus::Approved);
    assert_eq!(recorder.calls(), vec!["add:human_alice:botB"]);
    assert!(failing.revoke_friend("human_alice", "botB", None).await.is_err());
    recovered.revoke_friend("human_alice", "botB", None).await.unwrap();
    assert_eq!(recorder.calls(), vec!["add:human_alice:botB", "remove:human_alice:botB"]);
}

#[tokio::test]
async fn legacy_request_ids_are_invalid_and_do_not_block_new_requests() {
    let (eg, pp, rq, bc, db) = assemble().await;
    let registry = Arc::new(BotCore::memory());
    for id in ["botA", "botB"] {
        registry.register(id.into(), BotCapabilities { visibility: "protected".into(), ..Default::default() }).await.unwrap();
        seed_bot(&db, id, "protected", "protected", "APPROVAL", "online", None).await;
    }
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_friend_requests (request_id TEXT NOT NULL, env TEXT NOT NULL, \
         from_bot TEXT NOT NULL, to_bot TEXT NOT NULL, status TEXT NOT NULL, \
         gmt_create TEXT NOT NULL, gmt_modified TEXT NOT NULL)"
    )).await.unwrap();
    db.execute(DbStatement::new(
        "INSERT INTO bcs_friend_requests VALUES ('legacy-external-id', 'dev', 'botA', 'botB', \
         'pending', '2026-11-01 00:00:00', '2026-11-02 00:00:00')"
    )).await.unwrap();
    let facade = EdgeConnectFriendService::new(Arc::new(service(&eg, &pp, &rq, &bc)), registry);
    assert!(matches!(facade.friend_request_receiver("legacy-external-id").await,
        Err(FriendUseCaseError::Service(ServiceError::FriendRequestNotFound(_)))));
    for hint in [None, Some("botB".to_string())] {
        let mut command = decision("botB", "legacy-external-id");
        command.request_to_bot = hint;
        assert!(matches!(facade.accept_friend_request(command.clone()).await,
            Err(FriendUseCaseError::Service(ServiceError::FriendRequestNotFound(_)))));
        assert!(matches!(facade.reject_friend_request(command).await,
            Err(FriendUseCaseError::Service(ServiceError::FriendRequestNotFound(_)))));
    }
    for direction in [FriendRequestDirection::Received, FriendRequestDirection::Sent, FriendRequestDirection::All] {
        assert!(list(&facade, "botB", direction, None).await.is_empty());
    }
    let fresh = facade.create_friend_request(create("botA", "botB")).await.unwrap();
    assert_ne!(fresh.id, "legacy-external-id");
    facade.accept_friend_request(decision("botB", &fresh.id)).await.unwrap();
    assert_eq!(rq.get(&fresh.id, "dev").await.unwrap().status, RequestStatus::Approved);
    assert!(rq.get("legacy-external-id", "dev").await.is_none());
    assert!(eg.has_friend_edge("botA", "botB", "dev").await);
    let old = db.query(DbStatement::new("SELECT status FROM bcs_friend_requests")).await.unwrap();
    assert_eq!(old[0].get_string("status").unwrap().as_deref(), Some("pending"));
}
