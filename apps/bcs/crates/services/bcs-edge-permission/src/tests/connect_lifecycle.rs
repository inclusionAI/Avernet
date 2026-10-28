use super::*;

    #[tokio::test]
    async fn human_to_bot_manual_returns_pending_one_request_no_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:man", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("human_1", "x:man", Some("hi".into()), None)
            .await
            .expect("manual → Pending");
        assert_eq!(res.status, ConnectStatus::Pending);
        assert_eq!(res.request_ids.len(), 1, "exactly one pending request");
        assert!(res.edge_ids.is_empty());
        // default profile should NOT have been seeded (no edge built).
        assert!(pp.get_active_default("x:man", "dev").await.is_none());
        let id = res.request_ids[0].clone();
        let r = rq.get(&id, "dev").await.expect("pending request exists");
        assert_eq!(r.status, RequestStatus::Pending);
        assert_eq!(r.from_id, "human_1");
        assert_eq!(r.to_id, "x:man");
        assert!(r.edge_id.is_none());
    }
    #[tokio::test]
    async fn human_to_bot_auto_approves_one_edge_one_request() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:auto1", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("human_1", "x:auto1", None, None)
            .await
            .expect("auto → Approved");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 1, "Human→Bot: exactly 1 edge");
        assert_eq!(res.request_ids.len(), 1, "Human→Bot: 1 request");

        // The edge caller→to_bot references to_bot's default profile.
        let active = eg.list_active_grants("human_1", "x:auto1", "dev").await;
        assert_eq!(active.len(), 1);
        let default_id = eg.get_default_profile_id("x:auto1", "dev").await;
        assert_eq!(active[0].grant_ref_id, default_id.unwrap());
        assert!(eg.has_friend_edge("human_1", "x:auto1", "dev").await);

        // The request is approved, decided_by=auto, backfilled with edge_id.
        let r = rq.get(&res.request_ids[0], "dev").await.expect("approved req");
        assert_eq!(r.status, RequestStatus::Approved);
        assert_eq!(r.decided_by.as_deref(), Some("auto"));
        assert_eq!(r.edge_id, Some(res.edge_ids[0]));
    }
    #[tokio::test]
    async fn bot_to_bot_auto_approves_two_edges_two_requests() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:botA", "protected", "protected", "OPEN", "online", Some("85020")).await;
        seed_bot(&db, "x:botB", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("x:botA", "x:botB", None, None)
            .await
            .expect("Bot↔Bot auto → Approved");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert_eq!(res.edge_ids.len(), 2, "Bot↔Bot: 2 edges");
        assert_eq!(res.request_ids.len(), 2, "Bot↔Bot: 2 requests");

        let fwd = eg.list_active_grants("x:botA", "x:botB", "dev").await;
        let rev = eg.list_active_grants("x:botB", "x:botA", "dev").await;
        assert_eq!(fwd.len(), 1);
        assert_eq!(rev.len(), 1);
        assert_eq!(
            fwd[0].grant_ref_id,
            eg.get_default_profile_id("x:botB", "dev").await.unwrap()
        );
        assert_eq!(
            rev[0].grant_ref_id,
            eg.get_default_profile_id("x:botA", "dev").await.unwrap()
        );
        assert!(eg.has_friend_edge("x:botA", "x:botB", "dev").await);
    }
    #[tokio::test]
    async fn already_friends_is_idempotent_approved() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:idem", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let first = svc
            .create_connect("human_1", "x:idem", None, None)
            .await
            .expect("first connect");
        assert_eq!(first.status, ConnectStatus::Approved);
        let second = svc
            .create_connect("human_1", "x:idem", None, None)
            .await
            .expect("second connect idempotent");
        assert_eq!(second.status, ConnectStatus::Approved);
        assert!(second.edge_ids.is_empty(), "no new edge on idempotent");
        assert!(second.request_ids.is_empty(), "no new request on idempotent");
        // Exactly one edge overall.
        let active = eg.list_active_grants("human_1", "x:idem", "dev").await;
        assert_eq!(active.len(), 1);
    }
    #[tokio::test]
    async fn pending_connect_is_idempotent_pending() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pend", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let first = svc
            .create_connect("human_1", "x:pend", None, None)
            .await
            .expect("first manual");
        assert_eq!(first.status, ConnectStatus::Pending);
        let second = svc
            .create_connect("human_1", "x:pend", None, None)
            .await
            .expect("second idempotent");
        assert_eq!(second.status, ConnectStatus::Pending);
        // Returns the SAME pending request id (no duplicate insert).
        assert_eq!(first.request_ids, second.request_ids);
    }
    #[tokio::test]
    async fn approve_pending_human_to_bot_builds_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:appr", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:appr", None, None)
            .await
            .expect("manual pending");
        assert_eq!(pending.status, ConnectStatus::Pending);
        let rid = pending.request_ids[0].clone();

        let edge_ids = svc.approve(&rid, "85020", None).await.expect("approve ok");
        assert_eq!(edge_ids.len(), 1, "Human→Bot approve: 1 edge");
        // Original pending request is now approved + edge_id backfilled.
        let r = rq.get(&rid, "dev").await.expect("request still exists");
        assert_eq!(r.status, RequestStatus::Approved);
        assert_eq!(r.edge_id, Some(edge_ids[0]));
        assert!(eg.has_friend_edge("human_1", "x:appr", "dev").await);
        assert!(eg.list_active_grants("x:appr", "human_1", "dev").await.is_empty());
        let bot_friends = svc.list_friends("x:appr").await.expect("bot friends");
        assert_eq!(bot_friends.len(), 1);
        assert_eq!(bot_friends[0].actor_id, "human_1");
        assert_eq!(bot_friends[0].kind, ActorKind::Human);
        let human_friends = svc.list_friends("human_1").await.expect("human friends");
        assert_eq!(human_friends.len(), 1);
        assert_eq!(human_friends[0].actor_id, "x:appr");
        svc.revoke_friend("x:appr", "human_1", None).await.expect("bot removes friend");
        assert!(svc.list_friends("x:appr").await.expect("bot friends").is_empty());
        assert!(svc.list_friends("human_1").await.expect("human friends").is_empty());
    }
    #[tokio::test]
    async fn friend_page_service_preserves_single_edge_and_propagates_store_errors() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:paged", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        for human in ["human_2", "human_1"] {
            svc.create_connect(human, "x:paged", None, None).await.unwrap();
        }
        let query = FriendListQuery { target_type: Some(ActorKind::Human), offset: 1, limit: 1 };
        let page = svc.list_friends_paginated("x:paged", query).await.unwrap();
        assert_eq!(page.total, 2);
        assert_eq!(page.items.len(), 1);
        assert_eq!(page.items[0].actor_id, "human_2");
        assert_eq!(page.items[0].kind, ActorKind::Human);
        assert!(eg.list_active_grants("x:paged", "human_2", "dev").await.is_empty());
        let bots = svc.list_friends_paginated("x:paged", FriendListQuery {
            target_type: Some(ActorKind::Bot), offset: 0, limit: 20,
        }).await.unwrap();
        assert_eq!(bots.total, 0);
        assert!(bots.items.is_empty());
        svc.revoke_friend("x:paged", "human_1", None).await.unwrap();
        let empty = svc.list_friends_paginated("x:paged", query).await.unwrap();
        assert_eq!(empty.total, 1);
        assert!(empty.items.is_empty());
        db.execute(DbStatement::new("DROP TABLE edge_grants")).await.unwrap();
        assert!(matches!(svc.list_friends_paginated("x:paged", query).await,
            Err(ServiceError::InternalError(_))));
    }
    #[tokio::test]
    async fn approve_does_not_duplicate_request_rows() {
        // The approve path must decide the existing pending row in place —
        // NOT insert a second approved snapshot row (only the create_connect
        // auto path inserts snapshots). Total connect request rows for this
        // Human→Bot connect must remain 1 after approve.
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:nodupe", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:nodupe", None, None)
            .await
            .expect("manual pending");
        let rid = pending.request_ids[0].clone();

        svc.approve(&rid, "85020", None).await.expect("approve ok");

        // The bot's inbox (to_id=x:nodupe) should contain exactly 1 request
        // for this connect (the original, now approved) — not 2.
        let inbox = rq.list_inbox("x:nodupe", "dev", None).await;
        let ours: Vec<&PermissionRequest> = inbox
            .iter()
            .filter(|r| r.from_id == "human_1")
            .collect();
        assert_eq!(ours.len(), 1, "approve must not duplicate the request row");
        assert_eq!(ours[0].status, RequestStatus::Approved);
    }
    #[tokio::test]
    async fn approve_bot_to_bot_approves_both_and_builds_two_edges() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:bbA", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        seed_bot(&db, "x:bbB", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("x:bbA", "x:bbB", None, None)
            .await
            .expect("manual pending Bot↔Bot");
        assert_eq!(pending.request_ids.len(), 2);
        let fwd_id = pending.request_ids[0].clone();

        let edge_ids = svc.approve(&fwd_id, "owner", None).await.expect("approve ok");
        assert_eq!(edge_ids.len(), 2, "Bot↔Bot approve: 2 edges");

        // BOTH pending requests are now approved (single accept, §4.1).
        let fwd = rq.get(&fwd_id, "dev").await.expect("fwd present");
        let rev = rq
            .get(&pending.request_ids[1], "dev")
            .await
            .expect("rev present");
        assert_eq!(fwd.status, RequestStatus::Approved);
        assert_eq!(rev.status, RequestStatus::Approved);
        assert!(eg.has_friend_edge("x:bbA", "x:bbB", "dev").await);
    }
    #[tokio::test]
    async fn reject_does_not_build_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:rej", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:rej", None, None)
            .await
            .expect("manual pending");
        let rid = pending.request_ids[0].clone();
        svc.reject(&rid, "85020", Some("no thanks".into()))
            .await
            .expect("reject ok");
        let r = rq.get(&rid, "dev").await.expect("request present");
        assert_eq!(r.status, RequestStatus::Rejected);
        assert!(r.edge_id.is_none());
        let active = eg.list_active_grants("human_1", "x:rej", "dev").await;
        assert!(active.is_empty(), "no edge built on reject");
    }
    #[tokio::test]
    async fn reject_bot_to_bot_rejects_both() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:rbA", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        seed_bot(&db, "x:rbB", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("x:rbA", "x:rbB", None, None)
            .await
            .expect("pending");
        svc.reject(&pending.request_ids[0], "owner", None)
            .await
            .expect("reject ok");
        let fwd = rq.get(&pending.request_ids[0], "dev").await.expect("fwd");
        let rev = rq.get(&pending.request_ids[1], "dev").await.expect("rev");
        assert_eq!(fwd.status, RequestStatus::Rejected);
        assert_eq!(rev.status, RequestStatus::Rejected);
    }
    #[tokio::test]
    async fn cancel_only_pending() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:canc", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:canc", None, None)
            .await
            .expect("pending");
        let rid = pending.request_ids[0].clone();
        svc.cancel(&rid).await.expect("cancel ok");
        let r = rq.get(&rid, "dev").await.expect("request still exists");
        assert_eq!(r.status, RequestStatus::Cancelled);
        // cancelling an already-cancelled request is idempotent (B4e): Ok, not
        // an error. Spec says "已 rejected/cancelled 幂等".
        svc.cancel(&rid).await.expect("idempotent cancel ok");
        let r2 = rq.get(&rid, "dev").await.expect("request still exists");
        assert_eq!(r2.status, RequestStatus::Cancelled);
    }
    #[tokio::test]
    async fn sync_add_friendship_bot_to_bot_writes_two_default_edges_idempotently() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);

        svc.sync_add_friendship("x:syncA", "x:syncB").await.expect("sync add");
        svc.sync_add_friendship("x:syncA", "x:syncB").await.expect("sync add idempotent");

        let a_to_b = eg.list_active_grants("x:syncA", "x:syncB", "dev").await;
        let b_to_a = eg.list_active_grants("x:syncB", "x:syncA", "dev").await;
        assert_eq!(a_to_b.len(), 1);
        assert_eq!(b_to_a.len(), 1);
        assert_eq!(
            a_to_b[0].grant_ref_id,
            pp.get_active_default("x:syncB", "dev").await.unwrap().permission_profile_id
        );
        assert_eq!(
            b_to_a[0].grant_ref_id,
            pp.get_active_default("x:syncA", "dev").await.unwrap().permission_profile_id
        );
    }
    #[tokio::test]
    async fn sync_add_friendship_normalizes_bot_human_to_human_bot_edge() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);

        svc.sync_add_friendship("x:human_target", "human_1").await.expect("sync add");

        let human_to_bot = eg.list_active_grants("human_1", "x:human_target", "dev").await;
        let bot_to_human = eg.list_active_grants("x:human_target", "human_1", "dev").await;
        assert_eq!(human_to_bot.len(), 1);
        assert!(bot_to_human.is_empty(), "Bot→Human edge must not be created");
        assert_eq!(
            human_to_bot[0].grant_ref_id,
            pp.get_active_default("x:human_target", "dev").await.unwrap().permission_profile_id
        );
    }
    #[tokio::test]
    async fn sync_add_friendship_uses_service_env() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service_in_env(&eg, &pp, &rq, &bc, "pre");

        svc.sync_add_friendship("x:envA", "x:envB").await.expect("sync add");

        assert!(eg.has_friend_edge("x:envA", "x:envB", "pre").await);
        assert!(!eg.has_friend_edge("x:envA", "x:envB", "dev").await);
        assert!(pp.get_active_default("x:envA", "pre").await.is_some());
        assert!(pp.get_active_default("x:envA", "dev").await.is_none());
    }
    #[tokio::test]
    async fn sync_remove_friendship_revokes_only_default_friend_edges() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);

        svc.sync_add_friendship("human_1", "x:sync_keep").await.expect("sync add");
        eg.insert_grant(EdgeGrant {
            edge_id: 0,
            env: "dev".to_string(),
            from_id: "human_1".to_string(),
            to_id: "x:sync_keep".to_string(),
            grant_kind: GrantKind::PermissionProfile,
            grant_ref_id: 4002,
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
        })
        .await
        .expect("insert non-friend edge");

        svc.sync_remove_friendship("human_1", "x:sync_keep").await.expect("sync remove");

        assert!(!eg.has_friend_edge("human_1", "x:sync_keep", "dev").await);
        let active = eg.list_active_grants("human_1", "x:sync_keep", "dev").await;
        assert_eq!(active.len(), 1);
        assert_eq!(active[0].grant_ref_id, 4002);
    }
    #[tokio::test]
    async fn revoke_friend_human_to_bot_one_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:unf", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let created = svc
            .create_connect("human_1", "x:unf", None, None)
            .await
            .expect("auto connect");
        assert_eq!(created.edge_ids.len(), 1);
        assert!(eg.has_friend_edge("human_1", "x:unf", "dev").await);

        let n = svc.revoke_friend("human_1", "x:unf", None).await.expect("revoke ok");
        assert_eq!(n.len(), 1, "Human→Bot: revoked exactly 1 friend edge");
        assert!(!eg.has_friend_edge("human_1", "x:unf", "dev").await);
    }
    #[tokio::test]
    async fn revoke_friend_bot_to_bot_two_edges() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:uA", "protected", "protected", "OPEN", "online", Some("85020")).await;
        seed_bot(&db, "x:uB", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        svc.create_connect("x:uA", "x:uB", None, None).await.expect("connect");
        assert!(eg.has_friend_edge("x:uA", "x:uB", "dev").await);

        let n = svc.revoke_friend("x:uA", "x:uB", None).await.expect("revoke ok");
        assert_eq!(n.len(), 2, "Bot↔Bot: revoked both friend edges");
        assert!(!eg.has_friend_edge("x:uA", "x:uB", "dev").await);
    }
    #[tokio::test]
    async fn revoke_friend_leaves_non_default_edges() {
        // A non-default profile edge (grant_ref_id != default) must survive
        // revoke_friend (it is not a friend edge per D12).
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:keep", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        svc.create_connect("human_1", "x:keep", None, None).await.expect("connect");
        // Manually insert a writer-profile edge with a different ref id.
        eg.insert_grant(EdgeGrant {
            edge_id: 4001,
            env: "dev".to_string(),
            from_id: "human_1".to_string(),
            to_id: "x:keep".to_string(),
            grant_kind: GrantKind::PermissionProfile,
            grant_ref_id: 4002, // NOT the default
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
        })
        .await
        .expect("insert writer edge");

        let n = svc.revoke_friend("human_1", "x:keep", None).await.expect("revoke");
        assert_eq!(n.len(), 1, "only the friend (default) edge revoked");
        let active = eg.list_active_grants("human_1", "x:keep", "dev").await;
        assert_eq!(active.len(), 1, "writer edge survives");
        assert_eq!(active[0].grant_ref_id, 4002);
    }
    #[tokio::test]
    async fn list_friends_after_connect() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:lf1", "protected", "protected", "OPEN", "online", Some("85020")).await;
        seed_bot(&db, "x:lf2", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        svc.create_connect("human_1", "x:lf1", None, None).await.expect("c1");
        svc.create_connect("human_1", "x:lf2", None, None).await.expect("c2");

        let friends = svc.list_friends("human_1").await.expect("list ok");
        let ids: Vec<String> = friends.iter().map(|f| f.actor_id.clone()).collect();
        let mut sorted = ids.clone();
        sorted.sort();
        assert_eq!(sorted, vec!["x:lf1".to_string(), "x:lf2".to_string()]);
        // Entries carry the right kind and no enrichment (T13 leaves it None).
        for f in &friends {
            assert_eq!(f.kind, ActorKind::Bot);
            assert!(f.name.is_none());
        }
    }
    #[tokio::test]
    async fn list_requests_received_returns_pending() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:lr", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:lr", None, None)
            .await
            .expect("pending");
        // The bot's inbox should list the pending request received.
        let page = svc
            .list_requests("x:lr", RequestDirection::Received, None, 1, 20)
            .await
            .expect("list ok");
        assert_eq!(page.total, 1);
        assert_eq!(page.items.len(), 1);
        assert_eq!(page.items[0].request_id, pending.request_ids[0]);
        // Status filter works.
        let page = svc
            .list_requests(
                "x:lr",
                RequestDirection::Received,
                Some(RequestStatus::Approved),
                1,
                20,
            )
            .await
            .expect("list ok");
        assert_eq!(page.total, 0, "no approved requests in inbox");
        // Sent direction is now backed by list_sent (B4d): the human caller's
        // outbox contains the pending request they just sent.
        let page = svc
            .list_requests("human_1", RequestDirection::Sent, None, 1, 20)
            .await
            .expect("list ok");
        assert_eq!(page.total, 1, "Sent direction backed by list_sent");
        assert_eq!(page.items[0].request_id, pending.request_ids[0]);
        assert_eq!(page.items[0].from_id, "human_1");
        assert_eq!(page.items[0].to_id, "x:lr");
    }
    #[tokio::test]
    async fn list_requests_pagination() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pg", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        // Create 3 separate human callers connecting to the same bot.
        for h in ["human_a", "human_b", "human_c"] {
            svc.create_connect(h, "x:pg", None, None).await.expect("pending");
        }
        let page1 = svc
            .list_requests("x:pg", RequestDirection::Received, None, 1, 2)
            .await
            .expect("ok");
        assert_eq!(page1.total, 3);
        assert_eq!(page1.items.len(), 2, "page_size=2");
        let page2 = svc
            .list_requests("x:pg", RequestDirection::Received, None, 2, 2)
            .await
            .expect("ok");
        assert_eq!(page2.items.len(), 1, "remainder on page 2");
    }
