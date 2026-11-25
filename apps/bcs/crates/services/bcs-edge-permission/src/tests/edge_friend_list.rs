use super::*;

fn friends_command(caller: &str, target: &str) -> ListFriendsCommand {
    ListFriendsCommand { caller_actor_id: caller.into(), target_actor_id: target.into() }
}

#[tokio::test]
async fn friend_list_uses_edges_without_legacy_mirrors_for_bots_and_humans() {
    for caller in ["botA", "human_alice"] {
        let fx = fixture(&[TestBot::bot("botA"), TestBot::owned("botB", "owner1")]).await;
        assert!(fx.facade.list_friends(friends_command(caller, caller)).await.unwrap().is_empty(),
            "legacy-only list entries must not leak into edge-backed reads");
        let pending = fx.facade.create_friend_request(create(caller, "botB")).await.unwrap();
        fx.facade.accept_friend_request(decision("botB", &pending.id)).await.unwrap();
        // Fixture's writeback only records calls: there are no legacy mirrors.
        let friends = fx.facade.list_friends(friends_command(caller, caller)).await.unwrap();
        assert_eq!(friends.len(), 1);
        assert_eq!(friends[0].bot_uuid, "botB");
        assert_eq!(friends[0].name.as_deref(), Some("botB"));
        assert_eq!(friends[0].dynamic_status.status,
            if friends[0].is_online { "active" } else { "offline" });
        let reverse = fx.facade.list_friends(friends_command("botB", "botB")).await.unwrap();
        assert_eq!(reverse.len(), usize::from(caller == "botA"),
            "legacy Bot list must still exclude Human peers");
    }
}

#[tokio::test]
async fn friend_list_preserves_self_only_authorization_and_hidden_targets() {
    let fx = fixture(&[
        TestBot::bot("botA"), TestBot::bot("botB"),
        TestBot { visibility: "private", ..TestBot::bot("hidden") },
    ]).await;
    assert!(matches!(fx.facade.list_friends(friends_command("botA", "botB")).await,
        Err(FriendUseCaseError::Forbidden(_))));
    for target in ["hidden", "missing"] {
        assert!(matches!(fx.facade.list_friends(friends_command("botA", target)).await,
            Err(FriendUseCaseError::Service(ServiceError::BotNotFound(_)))));
    }
}

#[tokio::test]
async fn friend_list_propagates_edge_storage_failures() {
    let (eg, pp, rq, bc, db) = assemble().await;
    let facade = EdgeConnectFriendService::new(
        Arc::new(service(&eg, &pp, &rq, &bc)), Arc::new(BotCore::memory()),
    );
    db.execute(DbStatement::new("DROP TABLE edge_grants")).await.unwrap();
    assert!(facade.list_friends(friends_command("botA", "botA")).await.is_err());
}

struct RecordingListDb {
    inner: Arc<dyn DbPlugin>,
    rows_read: std::sync::Mutex<Vec<usize>>,
    fail_second_page: std::sync::atomic::AtomicBool,
}

#[async_trait]
impl DbPlugin for RecordingListDb {
    async fn query(&self, statement: DbStatement) -> bcs_db_api::DbResult<Vec<bcs_db_api::DbRow>> {
        if self.fail_second_page.load(std::sync::atomic::Ordering::Relaxed)
            && self.rows_read.lock().unwrap().len() == 1 {
            return Err(bcs_db_api::DbError::InvalidInput("injected second-page failure".into()));
        }
        let rows = self.inner.query(statement).await?;
        self.rows_read.lock().unwrap().push(rows.len());
        Ok(rows)
    }
    async fn execute(&self, statement: DbStatement) -> bcs_db_api::DbResult<bcs_db_api::DbExecuteResult> {
        self.inner.execute(statement).await
    }
    async fn transaction(&self, steps: Vec<bcs_db_api::DbTransactionStep>) -> bcs_db_api::DbResult<Vec<bcs_db_api::DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }
    async fn health_check(&self) -> bcs_db_api::DbResult<bcs_db_api::DbHealth> {
        self.inner.health_check().await
    }
}

#[tokio::test]
async fn friend_list_pages_edges_and_omits_missing_peers_without_partial_success() {
    let (eg, pp, rq, bc, db) = assemble().await;
    let registry = Arc::new(BotCore::memory());
    for i in 0..103 {
        let id = format!("bot{i:03}");
        if i != 0 && i != 102 {
            registry.register(id.clone(), BotCapabilities {
                name: Some(format!("Friend {i}")), summary: Some("summary".into()),
                ..Default::default()
            }).await.unwrap();
        }
        let profile = pp.ensure_default_profile(&id, "dev").await.unwrap();
        eg.insert_grant(EdgeGrant {
            edge_id: 0, env: "dev".into(), from_id: "human_alice".into(), to_id: id,
            grant_kind: GrantKind::PermissionProfile, grant_ref_id: profile,
            rules: None, status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any, originator_policy_data: None,
        }).await.unwrap();
    }
    registry.register_streaming_connection("bot001".into()).await.unwrap();
    registry.update_actor_status("bot001", bcs_service_api::ActorStatus::Online).await.unwrap();
    registry.register_streaming_connection("bot002".into()).await.unwrap();
    registry.update_actor_status("bot002", bcs_service_api::ActorStatus::Hidden).await.unwrap();
    let recording = Arc::new(RecordingListDb {
        inner: db, rows_read: std::sync::Mutex::new(Vec::new()),
        fail_second_page: std::sync::atomic::AtomicBool::new(false),
    });
    let edges: Arc<dyn EdgeGrantRepoPort> = Arc::new(bcs_edge_permission_store::DbEdgeGrantStore::sqlite(recording.clone()));
    let connect = Arc::new(service(&edges, &pp, &rq, &bc));
    let facade = EdgeConnectFriendService::new(connect.clone(), registry.clone());
    let entries = facade.list_friends(friends_command("human_alice", "human_alice")).await.unwrap();
    assert_eq!(entries.len(), 101, "missing peers do not end pagination prematurely");
    assert_eq!(entries[0].bot_uuid, "bot001");
    assert_eq!(entries[100].bot_uuid, "bot101");
    assert_eq!(entries[0].name.as_deref(), Some("Friend 1"));
    assert_eq!(entries[0].summary.as_deref(), Some("summary"));
    assert!(entries[0].is_online);
    assert_eq!(entries[0].is_online, registry.is_effectively_online("bot001").await);
    assert!(!entries[1].is_online, "connected but Hidden is not effectively online");
    assert_eq!(entries[1].is_online, registry.is_effectively_online("bot002").await);
    assert_eq!(*recording.rows_read.lock().unwrap(), vec![100, 3],
        "one bounded query per page, no per-peer default-profile reads");

    recording.rows_read.lock().unwrap().clear();
    recording.fail_second_page.store(true, std::sync::atomic::Ordering::Relaxed);
    assert!(facade.list_friends(friends_command("human_alice", "human_alice")).await.is_err(),
        "a second-page error must not return the first page as successful data");
    recording.fail_second_page.store(false, std::sync::atomic::Ordering::Relaxed);
    connect.revoke_friend("human_alice", "bot001", None).await.unwrap();
    let entries = facade.list_friends(friends_command("human_alice", "human_alice")).await.unwrap();
    assert_eq!(entries.len(), 100);
    assert!(entries.iter().all(|entry| entry.bot_uuid != "bot001"));
}
