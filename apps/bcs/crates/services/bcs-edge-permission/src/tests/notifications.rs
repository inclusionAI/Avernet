use super::*;

    #[tokio::test]
    async fn emit_skips_with_warn_when_recipients_empty() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // No owner (`created_by = NULL`) → no resolvable notification recipients.
        seed_bot(&db, "x:ownerless", "protected", "protected", "APPROVAL", "online", None).await;
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        let svc = service_with_notification(&eg, &pp, &rq, &bc, Arc::new(recorder));
        let res = svc
            .create_connect("human_1", "x:ownerless", Some("hi".into()), None)
            .await
            .expect("a pending connect without owner recipients still succeeds");
        assert_eq!(res.status, ConnectStatus::Pending);
        // The notification port must not be called when nobody owns the target.
        // `emit_friend_connect_notification` logs a `warn!` (request ids +
        // target bot id) instead of returning silently; that warning is for
        // human log observation and is not asserted here.
        assert!(
            events.lock().await.is_empty(),
            "no recipients → no notification is sent"
        );
    }

    #[tokio::test]
    async fn pending_friend_request_emits_notification_to_target_owner() {

        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pending_notify", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        let svc = service_with_notification(&eg, &pp, &rq, &bc, Arc::new(recorder));
        let request_auth = RequestAuthHeaders { authorization: Some("Bearer user-token".to_string()), cookie: Some("session=abc".to_string()), forwarded_headers: Vec::new() };
        let res = svc
            .create_connect("human_1", "x:pending_notify", Some("hi".into()), Some(request_auth.clone()))
            .await
            .expect("manual pending");
        assert_eq!(res.status, ConnectStatus::Pending);
        let events = events.lock().await;
        assert_eq!(events.len(), 1);
        let event = &events[0];
        assert_eq!(event.kind, FriendConnectNotificationKind::ApprovalRequested);
        assert_eq!(event.env, "dev");
        assert_eq!(event.request_ids, res.request_ids);
        assert_eq!(event.applicant_actor_id, "human_1");
        assert_eq!(event.target_bot_id, "x:pending_notify");
        assert_eq!(event.recipient_user_ids, vec!["85020".to_string()]);
        assert_eq!(event.message.as_deref(), Some("hi"));
        assert_eq!(event.request_auth, Some(request_auth));
    }
    #[tokio::test]
    async fn friend_connect_notification_resolves_applicant_and_target_names() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // Target bot: seed (default name = bot_uuid) then set a human-friendly name.
        seed_bot(&db, "x:expert", "protected", "public", "APPROVAL", "online", Some("85020")).await;
        db.execute(DbStatement::with_params(
            "UPDATE bcs_bots SET name = ? WHERE bot_uuid = ?",
            vec![DbValue::from("本地代码专家"), DbValue::from("x:expert")],
        ))
        .await
        .expect("set target bot name");
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        let user_directory: Arc<dyn UserDirectoryPlugin> =
            Arc::new(FixedNickUserDirectoryPlugin { nick: "李四".to_string() });
        let svc = DbConnectService::new(
            eg.clone(),
            pp.clone(),
            rq.clone(),
            bc.clone(),
            Some(user_directory),
            Arc::new(recorder),
            Arc::new(bcs_service_api::NoopFriendAuthSyncPort),
            "dev".to_string(),
        );
        // Human applicant (nick 李四) → bot "本地代码专家" (owner 85020); APPROVAL → pending.
        svc.create_connect("human_12345", "x:expert", None, None)
            .await
            .expect("manual pending connect");
        let events = events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, FriendConnectNotificationKind::ApprovalRequested);
        // applicant nick resolved via the user directory; target name via bot config.
        assert_eq!(events[0].applicant_name.as_deref(), Some("李四"));
        assert_eq!(events[0].target_bot_name.as_deref(), Some("本地代码专家"));
        assert_eq!(events[0].recipient_user_ids, vec!["85020".to_string()]);
    }
    #[tokio::test]
    async fn friend_connect_notification_falls_back_when_user_directory_absent() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // No UPDATE → name stays the seed default (= bot_uuid).
        seed_bot(&db, "x:noexp", "protected", "public", "APPROVAL", "online", Some("85020")).await;
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        // service_with_notification wires user_directory = None.
        let svc = service_with_notification(&eg, &pp, &rq, &bc, Arc::new(recorder));
        svc.create_connect("human_12345", "x:noexp", None, None)
            .await
            .expect("manual pending connect");
        let events = events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(
            events[0].applicant_name,
            None,
            "no user directory → applicant name unresolved (falls back to id)"
        );
        // Target name still resolves from the bot's config (seed default = bot_uuid).
        assert_eq!(events[0].target_bot_name.as_deref(), Some("x:noexp"));
    }
    #[tokio::test]
    async fn friend_connect_notification_carries_no_human_applicant_for_a_bot() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // The applicant bot HAS a historical creator (152819) — which
        // is not necessarily the user performing this request — and the
        // target bot is owned by 85020.
        seed_bot(&db, "x:applicant", "protected", "public", "APPROVAL", "online", Some("152819")).await;
        seed_bot(&db, "x:target", "protected", "public", "APPROVAL", "online", Some("85020")).await;
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        let svc = service_with_notification(&eg, &pp, &rq, &bc, Arc::new(recorder));
        // Bot→Bot pending connect (APPROVAL strategy → needs approval → ApprovalRequested).
        svc.create_connect("x:applicant", "x:target", None, None)
            .await
            .expect("bot→bot manual pending connect");
        let events = events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, FriendConnectNotificationKind::ApprovalRequested);
        // A Bot applicant carries NO Human applicant id: the work-order
        // intake derives the acting Human (owner/manager performing the
        // request) from the verified principal — the Bot's historical
        // `created_by` (152819 here) must never leak into it.
        assert_eq!(events[0].applicant_user_id, None);
        assert_eq!(events[0].applicant_actor_id, "x:applicant");
        assert_eq!(events[0].target_bot_id, "x:target");
        // Approver/recipient = the target bot's owner.
        assert_eq!(events[0].recipient_user_ids, vec!["85020".to_string()]);
    }
    /// Notification transport that always fails, standing in for a backend
    /// work-order API rejection (for example a 401 from the intake endpoint).
    struct FailingFriendConnectNotificationPort;

    #[async_trait]
    impl FriendConnectNotificationPort for FailingFriendConnectNotificationPort {
        async fn notify(&self, _command: FriendConnectNotificationCommand) -> ServiceResult<()> {
            Err(ServiceError::InternalError(
                "work-order notification rejected".to_string(),
            ))
        }
    }

    /// Spec §12.3: the adapter may degrade to a no-op on backend rejection —
    /// the friend request itself must stay runnable. A notification failure
    /// after the pending rows are written must not turn the create into a 500.
    #[tokio::test]
    async fn notification_port_failure_does_not_fail_create_connect() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:notify_fail", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service_with_notification(
            &eg,
            &pp,
            &rq,
            &bc,
            Arc::new(FailingFriendConnectNotificationPort),
        );
        let res = svc
            .create_connect("human_1", "x:notify_fail", Some("hi".into()), None)
            .await
            .expect("a notification failure must not fail the pending connect");
        assert_eq!(res.status, ConnectStatus::Pending);
        assert!(
            !res.request_ids.is_empty(),
            "the pending request ids are still returned"
        );
        // The pending rows were written before the notification attempt and
        // must survive its failure.
        let pending = rq
            .list_inbox("x:notify_fail", "dev", Some(RequestStatus::Pending))
            .await.expect("request listing");
        assert!(
            pending
                .iter()
                .any(|request| request.request_id == res.request_ids[0]),
            "pending row must survive the notification failure: {pending:?}"
        );
    }

    #[tokio::test]
    async fn pending_notification_is_not_duplicated_on_idempotent_create() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pending_idem", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        let svc = service_with_notification(&eg, &pp, &rq, &bc, Arc::new(recorder));
        let first = svc
            .create_connect("human_1", "x:pending_idem", None, None)
            .await
            .expect("first pending");
        let second = svc
            .create_connect("human_1", "x:pending_idem", None, None)
            .await
            .expect("idempotent pending");
        assert_eq!(first.request_ids, second.request_ids);
        assert_eq!(events.lock().await.len(), 1);
    }

    #[tokio::test]
    async fn pending_friend_request_notifies_current_authority_owner_after_transfer() {
        // The approval notification must reach the CURRENT authority owner —
        // not the historical `created_by` fact. Here the legacy creator is
        // 85020 while the post-transfer authority state (ownership_version 2
        // + the approved owner edge) names bob.
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:moved", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        db.execute(DbStatement::with_params(
            "UPDATE bcs_bots SET ownership_version = 2 WHERE bot_uuid = ?",
            vec![DbValue::from("x:moved")],
        ))
        .await
        .expect("bump ownership_version");
        eg.insert_grant(EdgeGrant {
            edge_id: 0,
            env: "dev".to_string(),
            from_id: "human_bob".to_string(),
            to_id: "x:moved".to_string(),
            grant_kind: GrantKind::Owner,
            grant_ref_id: 0,
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::SameAsFrom,
            originator_policy_data: None,
        })
        .await
        .expect("seed current owner edge");
        let authority: Arc<dyn BotAuthorityRepoPort> =
            Arc::new(bcs_edge_permission_store::DbBotAuthorityStore::sqlite(
                db.clone(),
                "dev".to_string(),
            ));
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        let svc = service_with_notification(&eg, &pp, &rq, &bc, Arc::new(recorder))
            .with_bot_authority_port(Some(authority));
        let res = svc
            .create_connect("human_1", "x:moved", Some("hi".into()), None)
            .await
            .expect("manual pending");
        assert_eq!(res.status, ConnectStatus::Pending);
        let events = events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(
            events[0].recipient_user_ids,
            vec!["bob".to_string()],
            "the current authority owner receives the notification, \
             not the historical creator 85020"
        );
    }

    #[tokio::test]
    async fn pending_friend_request_falls_back_to_created_by_when_ownership_uninitialized() {
        // Legacy bots (ownership_version = 0) have no authority owner edge:
        // the strict ownership read errors and the recipients degrade to the
        // historical `created_by` so unmigrated bots keep their existing
        // notification behavior.
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:legacy", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let authority: Arc<dyn BotAuthorityRepoPort> =
            Arc::new(bcs_edge_permission_store::DbBotAuthorityStore::sqlite(
                db.clone(),
                "dev".to_string(),
            ));
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        let svc = service_with_notification(&eg, &pp, &rq, &bc, Arc::new(recorder))
            .with_bot_authority_port(Some(authority));
        let res = svc
            .create_connect("human_1", "x:legacy", None, None)
            .await
            .expect("manual pending");
        assert_eq!(res.status, ConnectStatus::Pending);
        let events = events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].recipient_user_ids, vec!["85020".to_string()]);
    }
