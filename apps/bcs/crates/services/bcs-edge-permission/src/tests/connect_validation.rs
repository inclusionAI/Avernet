use super::*;

    #[tokio::test]
    async fn actor_kind_helper() {
        assert_eq!(actor_kind_of("human_88001"), ActorKind::Human);
        assert_eq!(actor_kind_of("20260421_x:85020"), ActorKind::Bot);
        // BCS-native bot uuids (no `:`) now fall back to Bot (A1 fix) instead
        // of None — the direction gate in create_connect rejects invalid dirs,
        // not actor_kind_of.
        assert_eq!(actor_kind_of("plainbot"), ActorKind::Bot);
    }
    #[tokio::test]
    async fn edge_permission_friend_sync_adds_and_removes_bot_friend_edges() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:syncA", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        seed_bot(&db, "x:syncB", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);

        svc.sync_add_friendship("x:syncA", "x:syncB")
            .await
            .expect("sync add");

        assert!(eg.has_friend_edge("x:syncA", "x:syncB", "dev").await);
        assert_eq!(eg.list_active_grants("x:syncA", "x:syncB", "dev").await.len(), 1);
        assert_eq!(eg.list_active_grants("x:syncB", "x:syncA", "dev").await.len(), 1);

        svc.sync_remove_friendship("x:syncA", "x:syncB")
            .await
            .expect("sync remove");

        assert!(!eg.has_friend_edge("x:syncA", "x:syncB", "dev").await);
    }
    #[tokio::test]
    async fn edge_permission_friend_sync_normalizes_human_bot_direction() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:syncBot", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);

        svc.sync_add_friendship("x:syncBot", "human_1")
            .await
            .expect("sync add");

        assert!(eg.has_friend_edge("human_1", "x:syncBot", "dev").await);
        assert_eq!(eg.list_active_grants("human_1", "x:syncBot", "dev").await.len(), 1);
        assert!(eg.list_active_grants("x:syncBot", "human_1", "dev").await.is_empty());
    }
    #[tokio::test]
    async fn cannot_add_self() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("bot_a:1", "bot_a:1", None, None)
            .await
            .expect_err("self-add rejected");
        assert!(matches!(err, ServiceError::CannotAddSelf), "got {err:?}");
    }
    #[tokio::test]
    async fn human_to_human_rejected() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("human_1", "human_2", None, None)
            .await
            .expect_err("human→human rejected");
        assert!(
            matches!(err, ServiceError::InvalidOperation { .. }),
            "got {err:?}"
        );
    }
    #[tokio::test]
    async fn request_lookup_miss_paths_and_env_scope() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);

        assert_eq!(svc.env(), "dev");

        let err = svc
            .approve("missing-request-approve", "decider_1", None)
            .await
            .expect_err("approve missing request should fail");
        assert!(matches!(err, ServiceError::FriendRequestNotFound(_)), "got {err:?}");

        let err = svc
            .reject("missing-request-reject", "decider_2", None)
            .await
            .expect_err("reject missing request should fail");
        assert!(matches!(err, ServiceError::FriendRequestNotFound(_)), "got {err:?}");

        let err = svc
            .cancel("missing-request-cancel")
            .await
            .expect_err("cancel missing request should fail");
        assert!(matches!(err, ServiceError::FriendRequestNotFound(_)), "got {err:?}");

        let err = svc
            .get_request("missing-request-get")
            .await
            .expect_err("get_request missing request should fail");
        assert!(matches!(err, ServiceError::FriendRequestNotFound(_)), "got {err:?}");
    }
    #[tokio::test]
    async fn bot_to_human_rejected() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("x:1", "human_2", None, None)
            .await
            .expect_err("bot→human rejected");
        assert!(
            matches!(err, ServiceError::InvalidOperation { .. }),
            "got {err:?}"
        );
    }
    #[tokio::test]
    async fn bot_not_found() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("human_1", "x:missing", None, None)
            .await
            .expect_err("missing bot → BotNotFound");
        assert!(
            matches!(err, ServiceError::BotNotFound(_)),
            "got {err:?}"
        );
    }
    #[tokio::test]
    async fn hidden_bot_rejected() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:hidden", "public", "protected", "OPEN", "hidden", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("human_1", "x:hidden", None, None)
            .await
            .expect_err("hidden → BotHidden");
        assert!(matches!(err, ServiceError::BotHidden(_)), "got {err:?}");
    }
    #[tokio::test]
    async fn private_bot_rejected() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // visibility=private blocks bot→bot collaboration (a bot adding a
        // private-visibility bot). Human callers are gated by `user_visibility`,
        // not `visibility` — see user_visibility_private_for_human_caller and
        // human_adds_visibility_private_user_visibility_public_bot_succeeds.
        seed_bot(&db, "x:priv", "private", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("caller_bot:1", "x:priv", None, None)
            .await
            .expect_err("bot→private visibility → PrivateBotCannotCollaborate");
        assert!(
            matches!(err, ServiceError::PrivateBotCannotCollaborate),
            "got {err:?}"
        );
    }
    #[tokio::test]
    async fn user_visibility_private_for_human_caller() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:nha", "protected", "private", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("human_1", "x:nha", None, None)
            .await
            .expect_err("user_visibility=private → Forbidden for human caller");
        assert!(matches!(err, ServiceError::Forbidden(_)), "got {err:?}");
    }
    #[tokio::test]
    async fn human_adds_visibility_private_user_visibility_public_bot_succeeds() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // visibility=private no longer blocks a human caller — humans are gated by
        // `user_visibility=public`, so this bot is human-addable (mirrors the
        // /bots/search viewer-kind selection).
        seed_bot(&db, "x:privuvis", "private", "public", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("human_1", "x:privuvis", None, None)
            .await
            .expect("human→(visibility=private, user_visibility=public) is human-addable");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 1);
        assert!(eg.has_friend_edge("human_1", "x:privuvis", "dev").await);
    }
