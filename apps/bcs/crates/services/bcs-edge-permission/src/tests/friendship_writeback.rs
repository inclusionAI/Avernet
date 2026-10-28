use super::*;

    // ---- Edge→legacy friendship writeback reverse sync ----

    #[tokio::test]
    async fn auto_approved_bot_bot_connect_writebacks_legacy_friendship() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // OPEN strategy → needs_approval false → auto-approve inside create_connect.
        seed_bot(&db, "x:autoA", "protected", "protected", "OPEN", "online", Some("85020")).await;
        seed_bot(&db, "x:autoB", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let recorder = Arc::new(RecordingWriteback::default());
        let svc = service_with_writeback(&eg, &pp, &rq, &bc, recorder.clone());

        let result = svc
            .create_connect("x:autoA", "x:autoB", None, None)
            .await
            .expect("auto connect");

        assert_eq!(result.status, ConnectStatus::Approved);
        assert_eq!(
            recorder.calls(),
            vec!["add:x:autoA:x:autoB".to_string()],
            "auto-approve must mirror the Bot↔Bot pair into the legacy store"
        );
    }
    #[tokio::test]
    async fn human_bot_connect_does_not_writeback() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:api", "public", "public", "OPEN", "online", Some("85020")).await;
        let recorder = Arc::new(RecordingWriteback::default());
        let svc = service_with_writeback(&eg, &pp, &rq, &bc, recorder.clone());

        let result = svc
            .create_connect("human_1", "x:api", None, None)
            .await
            .expect("human connect");

        assert_eq!(result.status, ConnectStatus::Approved);
        assert!(
            recorder.calls().is_empty(),
            "Human→Bot edges are out of writeback scope"
        );
    }
    #[tokio::test]
    async fn manual_approve_writebacks_and_idempotent_approve_backfills() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "bot_82ce3565", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        seed_bot(&db, "20260525_tzgcxt13:195302", "protected", "protected", "APPROVAL", "online", Some("330172")).await;
        let recorder = Arc::new(RecordingWriteback::default());
        let svc = service_with_writeback(&eg, &pp, &rq, &bc, recorder.clone());

        let result = svc
            .create_connect("bot_82ce3565", "20260525_tzgcxt13:195302", None, None)
            .await
            .expect("pending connect");
        assert_eq!(result.status, ConnectStatus::Pending);

        let request_id = result.request_ids.first().expect("pending request id").clone();
        svc.approve(&request_id, "human_330172", None)
            .await
            .expect("manual approve");
        assert_eq!(
            recorder.calls(),
            vec!["add:bot_82ce3565:20260525_tzgcxt13:195302".to_string()]
        );

        // Idempotent re-approve hits the already-Approved branch and backfills the
        // legacy row (covers friendships approved before this sync shipped).
        recorder.calls.lock().expect("clear").clear();
        svc.approve(&request_id, "human_330172", None)
            .await
            .expect("idempotent approve");
        assert_eq!(
            recorder.calls(),
            vec!["add:bot_82ce3565:20260525_tzgcxt13:195302".to_string()],
            "idempotent approve must backfill the legacy row"
        );
    }
    #[tokio::test]
    async fn revoke_friend_writebacks_removal() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:r1", "public", "public", "OPEN", "online", Some("85020")).await;
        seed_bot(&db, "x:r2", "public", "public", "OPEN", "online", Some("85020")).await;
        let recorder = Arc::new(RecordingWriteback::default());
        let svc = service_with_writeback(&eg, &pp, &rq, &bc, recorder.clone());

        svc.create_connect("x:r1", "x:r2", None, None)
            .await
            .expect("connect");
        recorder.calls.lock().expect("clear").clear();

        svc.revoke_friend("x:r1", "x:r2", None)
            .await
            .expect("revoke");

        assert_eq!(
            recorder.calls(),
            vec!["remove:x:r1:x:r2".to_string()],
            "unfriend must drop the legacy friendship row"
        );
    }
    #[tokio::test]
    async fn connect_flow_keeps_legacy_friendship_in_sync() {
        // Composition mirrors the DB profile in bootstrap/server.rs: DbConnectService
        // with the real FriendshipWritebackService over a shared FriendRepoPort.
        // The plain-token bot ids reproduce the original 403 payload shape.
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "bot_82ce3565", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        seed_bot(&db, "20260525_tzgcxt13:195302", "protected", "protected", "APPROVAL", "online", Some("330172")).await;

        let friend_repo: Arc<bcs_friend::MemoryFriendRepo> =
            Arc::new(bcs_friend::MemoryFriendRepo::new());
        let writeback = Arc::new(bcs_friend::FriendshipWritebackService::new(
            friend_repo.clone(),
            Arc::new(bcs_test_support::NoopRelationCoreService),
        ));
        let svc = service_with_writeback(&eg, &pp, &rq, &bc, writeback);

        // Before: the legacy gate sees no friendship (the original 403 state).
        assert!(!friend_repo
            .are_friends("bot_82ce3565", "20260525_tzgcxt13:195302")
            .await
            .expect("are_friends before"));

        let result = svc
            .create_connect("bot_82ce3565", "20260525_tzgcxt13:195302", None, None)
            .await
            .expect("connect");
        assert_eq!(result.status, ConnectStatus::Pending);

        let request_id = result.request_ids.first().expect("pending request id").clone();
        svc.approve(&request_id, "human_330172", None)
            .await
            .expect("approve");

        // After approve: the legacy gate (bcs-group ensure_v1_reachable reads
        // this exact repo/port) now sees the friendship → group creation passes.
        assert!(friend_repo
            .are_friends("bot_82ce3565", "20260525_tzgcxt13:195302")
            .await
            .expect("are_friends after approve"));

        // After unfriend: the legacy row is gone → gate closes again.
        svc.revoke_friend("bot_82ce3565", "20260525_tzgcxt13:195302", None)
            .await
            .expect("revoke");
        assert!(!friend_repo
            .are_friends("bot_82ce3565", "20260525_tzgcxt13:195302")
            .await
            .expect("are_friends after revoke"));
    }
    #[tokio::test]
    async fn writeback_failure_is_best_effort() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:f1", "public", "public", "OPEN", "online", Some("85020")).await;
        seed_bot(&db, "x:f2", "public", "public", "OPEN", "online", Some("85020")).await;
        let svc = service_with_writeback(&eg, &pp, &rq, &bc, Arc::new(FailingWriteback));

        let result = svc
            .create_connect("x:f1", "x:f2", None, None)
            .await
            .expect("connect must succeed despite writeback failure");

        assert_eq!(result.status, ConnectStatus::Approved);
        assert!(eg.has_friend_edge("x:f1", "x:f2", "dev").await);

        // Remove path is also best-effort: unfriend succeeds even though the
        // writeback port fails, and the edge grant is torn down.
        svc.revoke_friend("x:f1", "x:f2", None)
            .await
            .expect("revoke must succeed despite writeback failure");
        assert!(!eg.has_friend_edge("x:f1", "x:f2", "dev").await);
    }
