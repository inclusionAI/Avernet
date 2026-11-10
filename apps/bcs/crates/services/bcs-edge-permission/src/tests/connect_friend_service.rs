use super::*;

    use bcs_bot::BotCore;
    use bcs_domain::edge_permission::RequestKind;
    use bcs_service_api::{
        BotCapabilities, BotRegistryCoreService, CreateFriendRequestCommand, DynamicStatusResponse,
        FriendListEntry, FriendRequest, FriendRequestDecisionCommand, FriendRequestDirection,
        FriendRequestStatus, FriendService, FriendUseCaseError, ListFriendRequestsCommand,
        ListFriendsCommand,
    };

    /// A bot seeded into BOTH the registry (visibility gate) and `bcs_bots`
    /// (the `DbConnectService` config read).
    struct TestBot<'a> {
        id: &'a str,
        visibility: &'a str,
        friend_check_in_strategy: &'a str,
        owner: Option<&'a str>,
    }

    impl<'a> TestBot<'a> {
        fn bot(id: &'a str) -> Self {
            Self {
                id,
                visibility: "protected",
                friend_check_in_strategy: "APPROVAL",
                owner: None,
            }
        }

        fn owned(id: &'a str, owner: &'a str) -> Self {
            Self {
                owner: Some(owner),
                ..Self::bot(id)
            }
        }
    }

    /// The legacy list delegate is not under test here (Task 3/4 wires the real
    /// `bcs_friend::Friend`); the facade must delegate to it unchanged.
    #[derive(Default)]
    struct RecordingLegacyFriendList {
        calls: std::sync::Mutex<Vec<(String, String)>>,
    }

    impl RecordingLegacyFriendList {
        fn calls(&self) -> Vec<(String, String)> {
            self.calls.lock().expect("calls lock").clone()
        }
    }

    #[async_trait]
    impl FriendService for RecordingLegacyFriendList {
        async fn create_friend_request(
            &self,
            _command: CreateFriendRequestCommand,
        ) -> Result<FriendRequest, FriendUseCaseError> {
            unreachable!("list delegate must only serve list_friends")
        }

        async fn list_friend_requests(
            &self,
            _command: ListFriendRequestsCommand,
        ) -> Result<Vec<FriendRequest>, FriendUseCaseError> {
            unreachable!("list delegate must only serve list_friends")
        }

        async fn accept_friend_request(
            &self,
            _command: FriendRequestDecisionCommand,
        ) -> Result<(), FriendUseCaseError> {
            unreachable!("list delegate must only serve list_friends")
        }

        async fn reject_friend_request(
            &self,
            _command: FriendRequestDecisionCommand,
        ) -> Result<(), FriendUseCaseError> {
            unreachable!("list delegate must only serve list_friends")
        }

        async fn friend_request_receiver(
            &self,
            _request_id: &str,
        ) -> Result<String, FriendUseCaseError> {
            unreachable!("list delegate must only serve list_friends")
        }

        async fn list_friends(
            &self,
            command: ListFriendsCommand,
        ) -> Result<Vec<FriendListEntry>, FriendUseCaseError> {
            self.calls.lock().expect("calls lock").push((
                command.caller_actor_id,
                command.target_actor_id,
            ));
            Ok(vec![FriendListEntry {
                bot_uuid: "legacy:friend".to_string(),
                name: Some("Legacy Friend".to_string()),
                summary: None,
                is_online: true,
                dynamic_status: DynamicStatusResponse {
                    status: "active".to_string(),
                },
            }])
        }
    }

    struct Fixture {
        facade: EdgeConnectFriendService,
        events: Arc<tokio::sync::Mutex<Vec<FriendConnectNotificationCommand>>>,
        writeback: Arc<RecordingWriteback>,
        delegate: Arc<RecordingLegacyFriendList>,
        requests: Arc<dyn PermissionRequestRepoPort>,
    }

    async fn fixture(bots: &[TestBot<'_>]) -> Fixture {
        let (eg, pp, rq, bc, db) = assemble().await;

        let registry = BotCore::memory();
        for bot in bots {
            registry
                .register(
                    bot.id.to_string(),
                    BotCapabilities {
                        name: Some(bot.id.to_string()),
                        visibility: bot.visibility.to_string(),
                        ..Default::default()
                    },
                )
                .await
                .expect("register bot in registry");
            seed_bot(
                &db,
                bot.id,
                bot.visibility,
                bot.visibility,
                bot.friend_check_in_strategy,
                "online",
                bot.owner,
            )
            .await;
        }

        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        let writeback = Arc::new(RecordingWriteback::default());
        let connect: Arc<dyn ConnectService> = Arc::new(
            service_with_notification(&eg, &pp, &rq, &bc, Arc::new(recorder))
                .with_friendship_writeback(Some(writeback.clone())),
        );
        let registry: Arc<dyn BotRegistryCoreService> = Arc::new(registry);
        let delegate = Arc::new(RecordingLegacyFriendList::default());
        let facade = EdgeConnectFriendService::new(connect, registry, delegate.clone());

        Fixture {
            facade,
            events,
            writeback,
            delegate,
            requests: rq,
        }
    }

    fn create(caller: &str, to_bot: &str) -> CreateFriendRequestCommand {
        CreateFriendRequestCommand {
            caller_actor_id: caller.to_string(),
            to_bot: to_bot.to_string(),
            request_auth: None,
        }
    }

    fn decision(caller: &str, request_id: &str) -> FriendRequestDecisionCommand {
        FriendRequestDecisionCommand {
            caller_actor_id: caller.to_string(),
            request_id: request_id.to_string(),
            request_to_bot: None,
            request_auth: None,
        }
    }

    async fn list(
        facade: &EdgeConnectFriendService,
        caller: &str,
        direction: FriendRequestDirection,
        status_filter: Option<FriendRequestStatus>,
    ) -> Vec<FriendRequest> {
        facade
            .list_friend_requests(ListFriendRequestsCommand {
                caller_actor_id: caller.to_string(),
                direction,
                status_filter,
            })
            .await
            .expect("list friend requests")
    }

    // ---- 1) dima regression anchor: legacy entry → connect → notify owner ----

    #[tokio::test]
    async fn create_pending_request_notifies_target_bot_owner() {
        let fx = fixture(&[
            TestBot::bot("botA"),
            TestBot::owned("botB", "owner1"),
        ])
        .await;

        let request = fx
            .facade
            .create_friend_request(create("botA", "botB"))
            .await
            .expect("manual approval → pending");

        assert_eq!(request.status, FriendRequestStatus::Pending);
        assert!(!request.id.is_empty(), "pending request must carry an id");
        assert_eq!(request.from_bot, "botA");
        assert_eq!(request.to_bot, "botB");
        assert!(
            request.created_at > 0,
            "timestamps come from the persisted request row"
        );

        // The regression this dima fixes: the legacy friend-request entry point
        // must reach `DbConnectService`, which emits ApprovalRequested to the
        // target bot's owner.
        let events = fx.events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(
            events[0].kind,
            FriendConnectNotificationKind::ApprovalRequested
        );
        assert_eq!(events[0].recipient_user_ids, vec!["owner1".to_string()]);
        assert_eq!(events[0].target_bot_id, "botB");
        assert_eq!(events[0].applicant_actor_id, "botA");
        assert!(
            events[0].request_ids.contains(&request.id),
            "notification must carry the forward request id: {:?}",
            events[0].request_ids
        );
        assert_eq!(
            events[0].request_ids.len(),
            2,
            "Bot↔Bot pending inserts forward + reverse rows"
        );
    }

    // ---- 2) legacy visibility gate (ensure_target_actor_not_private) ----

    #[tokio::test]
    async fn create_friend_request_to_private_or_unknown_target_returns_bot_not_found() {
        let mut private = TestBot::bot("botPrivate");
        private.visibility = "private";
        let fx = fixture(&[TestBot::bot("botA"), private]).await;

        match fx
            .facade
            .create_friend_request(create("botA", "botPrivate"))
            .await
        {
            Err(FriendUseCaseError::Service(ServiceError::BotNotFound(id))) => {
                assert_eq!(id, "botPrivate");
            }
            other => panic!("private target must map to BotNotFound, got {other:?}"),
        }

        match fx
            .facade
            .create_friend_request(create("botA", "botUnknown"))
            .await
        {
            Err(FriendUseCaseError::Service(ServiceError::BotNotFound(id))) => {
                assert_eq!(id, "botUnknown");
            }
            other => panic!("unknown target must map to BotNotFound, got {other:?}"),
        }

        assert!(
            fx.events.lock().await.is_empty(),
            "no notification may be emitted for a rejected target"
        );
    }

    // ---- 3) duplicate pending pre-check keeps the legacy 409 ----

    #[tokio::test]
    async fn create_duplicate_pending_returns_pending_request_exists_with_existing_id() {
        let fx = fixture(&[TestBot::bot("botA"), TestBot::owned("botB", "owner1")]).await;

        let first = fx
            .facade
            .create_friend_request(create("botA", "botB"))
            .await
            .expect("first pending");

        match fx
            .facade
            .create_friend_request(create("botA", "botB"))
            .await
        {
            Err(FriendUseCaseError::Service(ServiceError::PendingRequestExists {
                request_id,
                from_bot,
                to_bot,
            })) => {
                assert_eq!(request_id, first.id);
                assert_eq!(from_bot.as_deref(), Some("botA"));
                assert_eq!(to_bot.as_deref(), Some("botB"));
            }
            other => panic!("duplicate pending must map to 409, got {other:?}"),
        }

        assert_eq!(
            fx.events.lock().await.len(),
            1,
            "the 409 path must not re-notify the owner"
        );
    }

    // ---- 4) auto-approved (OPEN) keeps the legacy Accepted response ----

    #[tokio::test]
    async fn auto_approved_returns_accepted() {
        let mut target = TestBot::owned("botB", "owner1");
        target.friend_check_in_strategy = "OPEN";
        let fx = fixture(&[TestBot::bot("botA"), target]).await;

        let request = fx
            .facade
            .create_friend_request(create("botA", "botB"))
            .await
            .expect("OPEN target auto-approves");

        assert_eq!(request.status, FriendRequestStatus::Accepted);
        assert!(!request.id.is_empty(), "approved request keeps its id");
        assert_eq!(request.from_bot, "botA");
        assert_eq!(request.to_bot, "botB");

        // Coexistence: the edge→legacy reverse sync must still mirror the
        // Bot↔Bot friendship into `bcs_friendships`.
        assert_eq!(
            fx.writeback.calls(),
            vec!["add:botA:botB".to_string()],
            "auto-approved Bot↔Bot connect writes back the legacy friendship"
        );
        assert!(
            fx.events.lock().await.is_empty(),
            "auto-approved connects emit no approval notification"
        );
    }

    // ---- 5) already friends → synthetic empty-id Accepted ----

    #[tokio::test]
    async fn already_friends_returns_empty_id_accepted() {
        let fx = fixture(&[TestBot::bot("botA"), TestBot::owned("botB", "owner1")]).await;

        let pending = fx
            .facade
            .create_friend_request(create("botA", "botB"))
            .await
            .expect("pending");
        fx.facade
            .accept_friend_request(decision("botB", &pending.id))
            .await
            .expect("accept");

        let again = fx
            .facade
            .create_friend_request(create("botA", "botB"))
            .await
            .expect("already friends");

        assert_eq!(
            again.id, "",
            "already-friends is a synthetic request without a persisted row"
        );
        assert_eq!(again.status, FriendRequestStatus::Accepted);
        assert_eq!(again.from_bot, "botA");
        assert_eq!(again.to_bot, "botB");
        assert!(
            again.created_at > 0 && again.updated_at > 0,
            "the synthetic already-friends response must carry real timestamps              (the retired core stamped `now`), got {again:?}"
        );
    }

    // ---- 6) decision conflicts keep the legacy wording ----

    #[tokio::test]
    async fn accept_and_reject_follow_legacy_conflict_words() {
        let mut third = TestBot::bot("botC");
        third.visibility = "public";
        let fx = fixture(&[
            TestBot::bot("botA"),
            TestBot::owned("botB", "owner1"),
            third,
        ])
        .await;

        let pending = fx
            .facade
            .create_friend_request(create("botA", "botB"))
            .await
            .expect("pending");

        // Not the receiver: legacy `authorize_request_receiver` wording.
        match fx
            .facade
            .accept_friend_request(decision("botC", &pending.id))
            .await
        {
            Err(FriendUseCaseError::Forbidden(message)) => {
                assert!(
                    message.contains("Cannot accept request intended for"),
                    "unexpected wording: {message}"
                );
            }
            other => panic!("non-receiver accept must be Forbidden, got {other:?}"),
        }

        // reject → accept is a conflict.
        fx.facade
            .reject_friend_request(decision("botB", &pending.id))
            .await
            .expect("reject as receiver");
        match fx
            .facade
            .accept_friend_request(decision("botB", &pending.id))
            .await
        {
            Err(FriendUseCaseError::Service(ServiceError::CannotAcceptRejected)) => {}
            other => panic!("accept after reject must conflict, got {other:?}"),
        }

        // accept → reject is a conflict.
        let pending = fx
            .facade
            .create_friend_request(create("botA", "botB"))
            .await
            .expect("pending after rejection");
        fx.facade
            .accept_friend_request(decision("botB", &pending.id))
            .await
            .expect("accept as receiver");
        match fx
            .facade
            .reject_friend_request(decision("botB", &pending.id))
            .await
        {
            Err(FriendUseCaseError::Service(ServiceError::CannotRejectAccepted)) => {}
            other => panic!("reject after accept must conflict, got {other:?}"),
        }
    }

    // ---- 7) Bot↔Bot reverse pending rows are hidden from the legacy view ----

    #[tokio::test]
    async fn list_requests_hides_reverse_pending_rows() {
        let fx = fixture(&[TestBot::bot("botA"), TestBot::owned("botB", "owner1")]).await;

        let pending = fx
            .facade
            .create_friend_request(create("botA", "botB"))
            .await
            .expect("pending");

        let sent = list(&fx.facade, "botA", FriendRequestDirection::Sent, None).await;
        assert_eq!(sent.len(), 1, "forward row only: {sent:?}");
        assert_eq!(sent[0].id, pending.id);
        assert_eq!(sent[0].from_bot, "botA");
        assert_eq!(sent[0].to_bot, "botB");

        let received = list(&fx.facade, "botB", FriendRequestDirection::Received, None).await;
        assert_eq!(received.len(), 1, "inbox row only: {received:?}");
        assert_eq!(received[0].to_bot, "botB");
        assert_eq!(received[0].from_bot, "botA");
        assert_eq!(received[0].id, pending.id);

        assert!(
            list(&fx.facade, "botA", FriendRequestDirection::Received, None)
                .await
                .is_empty(),
            "the bot↔bot reverse row (created_by=botA) must not appear in botA's inbox"
        );

        fx.facade
            .accept_friend_request(decision("botB", &pending.id))
            .await
            .expect("accept");

        assert!(
            list(
                &fx.facade,
                "botA",
                FriendRequestDirection::Sent,
                Some(FriendRequestStatus::Pending)
            )
            .await
            .is_empty(),
            "status filter must push down to the connect listing"
        );
    }

    // ---- 8) legacy status vocabulary: approved → accepted ----

    #[tokio::test]
    async fn status_vocabulary_maps_approved_to_accepted() {
        let fx = fixture(&[TestBot::bot("botA"), TestBot::owned("botB", "owner1")]).await;

        let pending = fx
            .facade
            .create_friend_request(create("botA", "botB"))
            .await
            .expect("pending");
        fx.facade
            .accept_friend_request(decision("botB", &pending.id))
            .await
            .expect("accept");

        let sent = list(&fx.facade, "botA", FriendRequestDirection::Sent, None).await;
        assert_eq!(sent.len(), 1);
        assert_eq!(
            sent[0].status,
            FriendRequestStatus::Accepted,
            "connect `approved` must project as legacy `accepted`"
        );

        let accepted = list(
            &fx.facade,
            "botA",
            FriendRequestDirection::Sent,
            Some(FriendRequestStatus::Accepted),
        )
        .await;
        assert_eq!(
            accepted.len(),
            1,
            "legacy `accepted` filter must map to connect `approved`"
        );
    }

    // ---- 9) receiver lookup ----

    #[tokio::test]
    async fn friend_request_receiver_returns_to_id_and_unknown_is_not_found() {
        let fx = fixture(&[TestBot::bot("botA"), TestBot::owned("botB", "owner1")]).await;

        let pending = fx
            .facade
            .create_friend_request(create("botA", "botB"))
            .await
            .expect("pending");

        assert_eq!(
            fx.facade
                .friend_request_receiver(&pending.id)
                .await
                .expect("receiver"),
            "botB"
        );

        match fx.facade.friend_request_receiver("missing-request").await {
            Err(FriendUseCaseError::Service(ServiceError::FriendRequestNotFound(id))) => {
                assert_eq!(id, "missing-request");
            }
            other => panic!("unknown request must be FriendRequestNotFound, got {other:?}"),
        }

        // Defensive: a non-connect request is not a friend request.
        fx.requests
            .insert(PermissionRequest {
                request_id: "rules-request".to_string(),
                edge_id: None,
                env: "dev".to_string(),
                from_id: "botA".to_string(),
                to_id: "botB".to_string(),
                request_kind: RequestKind::Rules,
                requested_ref_id: None,
                requested_rules: None,
                message: None,
                status: RequestStatus::Pending,
                decision_reason: None,
                created_by: "botA".to_string(),
                decided_by: None,
                decided_at: None,
                created_at: 0,
                updated_at: 0,
            })
            .await
            .expect("insert rules request");
        match fx.facade.friend_request_receiver("rules-request").await {
            Err(FriendUseCaseError::Service(ServiceError::FriendRequestNotFound(id))) => {
                assert_eq!(id, "rules-request");
            }
            other => panic!("non-connect request must be FriendRequestNotFound, got {other:?}"),
        }
    }

    // ---- 10) non-connect requests never leak into the legacy surface ----

    #[tokio::test]
    async fn legacy_listing_and_duplicate_check_ignore_non_connect_requests() {
        let fx = fixture(&[TestBot::bot("botA"), TestBot::owned("botB", "owner1")]).await;

        // A pending permission/rules request between the same pair is NOT a
        // friend request: it must neither appear in the legacy list nor turn
        // the next friend request into a 409.
        fx.requests
            .insert(PermissionRequest {
                request_id: "rules-request".to_string(),
                edge_id: None,
                env: "dev".to_string(),
                from_id: "botA".to_string(),
                to_id: "botB".to_string(),
                request_kind: RequestKind::Rules,
                requested_ref_id: None,
                requested_rules: None,
                message: None,
                status: RequestStatus::Pending,
                decision_reason: None,
                created_by: "botA".to_string(),
                decided_by: None,
                decided_at: None,
                created_at: 0,
                updated_at: 0,
            })
            .await
            .expect("insert rules request");

        for direction in [
            FriendRequestDirection::Sent,
            FriendRequestDirection::All,
            FriendRequestDirection::Received,
        ] {
            let label = format!("{direction:?}");
            assert!(
                list(&fx.facade, "botA", direction, Some(FriendRequestStatus::Pending))
                    .await
                    .is_empty(),
                "non-connect requests must not be listed as friend requests ({label})"
            );
        }

        let created = fx
            .facade
            .create_friend_request(create("botA", "botB"))
            .await
            .expect("a non-connect pending row must not trigger the legacy 409");
        assert_eq!(created.status, FriendRequestStatus::Pending);
        assert_ne!(created.id, "rules-request");

        let listed = list(
            &fx.facade,
            "botA",
            FriendRequestDirection::Sent,
            Some(FriendRequestStatus::Pending),
        )
        .await;
        assert_eq!(listed.len(), 1, "only the connect request is listed");
        assert_eq!(listed[0].id, created.id);
    }

    // ---- 11) list_friends is a pure delegation to the legacy service ----

    #[tokio::test]
    async fn list_friends_delegates_to_the_legacy_service() {
        let fx = fixture(&[TestBot::bot("botA")]).await;

        let entries = fx
            .facade
            .list_friends(ListFriendsCommand {
                caller_actor_id: "botA".to_string(),
                target_actor_id: "botA".to_string(),
            })
            .await
            .expect("legacy list");

        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].bot_uuid, "legacy:friend");
        assert_eq!(
            fx.delegate.calls(),
            vec![("botA".to_string(), "botA".to_string())],
            "the facade must not re-implement friend-list logic"
        );
    }
    // ---- 12) inbound principal threading (F2) ----

    use bcs_service_api::application::connect::{ConnectResult, FriendEntriesPage, RequestsPage};

    /// `ConnectService` stub that records the `request_auth` it receives, so
    /// the facade's pass-through is observable (the real store-backed service
    /// only forwards it onward to ports these tests do not wire).
    #[derive(Default)]
    struct RecordingAuthConnect {
        creates: tokio::sync::Mutex<Vec<Option<RequestAuthHeaders>>>,
        approvals: tokio::sync::Mutex<Vec<Option<RequestAuthHeaders>>>,
        rejections: tokio::sync::Mutex<Vec<(String, String)>>,
    }

    #[async_trait]
    impl ConnectService for RecordingAuthConnect {
        async fn create_connect(
            &self,
            _caller: &str,
            _to_bot: &str,
            _message: Option<String>,
            request_auth: Option<RequestAuthHeaders>,
        ) -> ServiceResult<ConnectResult> {
            self.creates.lock().await.push(request_auth);
            Ok(ConnectResult {
                request_ids: vec!["req-1".to_string()],
                edge_ids: Vec::new(),
                status: ConnectStatus::Pending,
                auto_accepted: false,
            })
        }

        async fn approve(
            &self,
            _request_id: &str,
            _decider: &str,
            request_auth: Option<RequestAuthHeaders>,
        ) -> ServiceResult<Vec<u64>> {
            self.approvals.lock().await.push(request_auth);
            Ok(vec![1])
        }

        async fn reject(
            &self,
            request_id: &str,
            decider: &str,
            _reason: Option<String>,
        ) -> ServiceResult<()> {
            self.rejections
                .lock()
                .await
                .push((request_id.to_string(), decider.to_string()));
            Ok(())
        }

        async fn cancel(&self, _request_id: &str) -> ServiceResult<()> {
            unreachable!("facade auth forwarding never cancels")
        }

        async fn get_request(&self, request_id: &str) -> ServiceResult<PermissionRequest> {
            Ok(PermissionRequest {
                request_id: request_id.to_string(),
                edge_id: None,
                env: "dev".to_string(),
                from_id: "botA".to_string(),
                to_id: "botB".to_string(),
                request_kind: RequestKind::Connect,
                requested_ref_id: None,
                requested_rules: None,
                message: None,
                status: RequestStatus::Pending,
                decision_reason: None,
                created_by: "botA".to_string(),
                decided_by: None,
                decided_at: None,
                created_at: 111,
                updated_at: 222,
            })
        }

        async fn revoke_friend(
            &self,
            _caller: &str,
            _target: &str,
            _request_auth: Option<RequestAuthHeaders>,
        ) -> ServiceResult<Vec<u64>> {
            unreachable!("facade auth forwarding never revokes")
        }

        async fn list_friends(
            &self,
            _actor: &str,
        ) -> ServiceResult<Vec<bcs_domain::edge_permission::FriendListEntry>> {
            unreachable!("facade auth forwarding never lists friends")
        }

        async fn list_friends_paginated(
            &self,
            _actor: &str,
            _query: FriendListQuery,
        ) -> ServiceResult<FriendEntriesPage> {
            unreachable!("facade auth forwarding never pages friends")
        }

        async fn list_requests(
            &self,
            _actor: &str,
            _direction: RequestDirection,
            _status: Option<RequestStatus>,
            _page: u32,
            _page_size: u32,
        ) -> ServiceResult<RequestsPage> {
            Ok(RequestsPage {
                items: Vec::new(),
                total: 0,
                page: 1,
                page_size: u32::MAX,
            })
        }
    }

    /// The inbound principal carried by the commands must reach the connect use
    /// case verbatim, so the owner notification / friend-auth-sync call can
    /// authenticate as the acting human.
    #[tokio::test]
    async fn command_request_auth_is_forwarded_to_the_connect_service() {
        let registry = BotCore::memory();
        for id in ["botA", "botB"] {
            registry
                .register(
                    id.to_string(),
                    BotCapabilities {
                        name: Some(id.to_string()),
                        visibility: "protected".to_string(),
                        ..Default::default()
                    },
                )
                .await
                .expect("register bot in registry");
        }
        let connect = Arc::new(RecordingAuthConnect::default());
        let facade = EdgeConnectFriendService::new(
            connect.clone(),
            Arc::new(registry),
            Arc::new(RecordingLegacyFriendList::default()),
        );
        let create_auth = RequestAuthHeaders {
            authorization: Some("Bearer user-token".to_string()),
            cookie: Some("session=abc".to_string()),
            forwarded_headers: vec![(
                "x-avernet-principal".to_string(),
                "principal-token".to_string(),
            )],
        };
        let accept_auth = RequestAuthHeaders {
            authorization: Some("Bearer owner-token".to_string()),
            cookie: None,
            forwarded_headers: Vec::new(),
        };

        facade
            .create_friend_request(CreateFriendRequestCommand {
                caller_actor_id: "botA".to_string(),
                to_bot: "botB".to_string(),
                request_auth: Some(create_auth.clone()),
            })
            .await
            .expect("create");
        assert_eq!(
            connect.creates.lock().await.as_slice(),
            &[Some(create_auth)],
            "create_connect must receive the command's request_auth"
        );

        facade
            .accept_friend_request(FriendRequestDecisionCommand {
                caller_actor_id: "botB".to_string(),
                request_id: "req-1".to_string(),
                request_to_bot: Some("botB".to_string()),
                request_auth: Some(accept_auth.clone()),
            })
            .await
            .expect("accept");
        assert_eq!(
            connect.approvals.lock().await.as_slice(),
            &[Some(accept_auth)],
            "approve must receive the command's request_auth"
        );

        // `ConnectService::reject` takes no auth context (there is no
        // downstream auth-consuming call on that leg), so the command is still
        // carried through the facade's receiver authorization and reaches the
        // connect service with the resolved caller.
        facade
            .reject_friend_request(FriendRequestDecisionCommand {
                caller_actor_id: "botB".to_string(),
                request_id: "req-1".to_string(),
                request_to_bot: Some("botB".to_string()),
                request_auth: Some(create_auth_clone_for_reject()),
            })
            .await
            .expect("reject");
        assert_eq!(
            connect.rejections.lock().await.as_slice(),
            &[("req-1".to_string(), "botB".to_string())]
        );
    }

    fn create_auth_clone_for_reject() -> RequestAuthHeaders {
        RequestAuthHeaders {
            authorization: Some("Bearer user-token".to_string()),
            cookie: None,
            forwarded_headers: Vec::new(),
        }
    }
