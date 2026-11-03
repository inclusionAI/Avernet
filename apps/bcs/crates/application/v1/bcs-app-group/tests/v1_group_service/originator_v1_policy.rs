    //! V1 create-group originator authorization (caller↔originator, not
    //! caller↔driver). Tests run the `for_v1_openapi` core branch.
    use super::*;

    fn chat_group(
        originator: Option<String>,
        participants: Vec<CreateParticipant>,
    ) -> CreateGroup {
        chat_group_with_driver("driver", originator, participants)
    }

    fn chat_group_with_driver(
        driver: &str,
        originator: Option<String>,
        participants: Vec<CreateParticipant>,
    ) -> CreateGroup {
        CreateGroup {
            caller: human_principal_with_profile("staff-1", "alice", Some("Alice"), None),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                name: Some("Planning".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: driver.into(),
                participants,
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
                originator,
            }),
        }
    }

    fn collaboration_originator(detail: GroupDetail) -> String {
        let GroupDetail::Collaboration(it) = detail else {
            panic!("expected collaboration detail, got {detail:?}");
        };
        it.originator_actor_id.to_string()
    }

    #[tokio::test]
    async fn originator_defaults_to_caller_when_omitted() {
        let fixture = Fixture::new().await;
        fixture.add_public_bot("driver").await;
        let detail = fixture
            .service
            .create(chat_group(None, vec![]))
            .await
            .expect("create group");
        assert_eq!(collaboration_originator(detail), "human_staff-1");
    }

    #[tokio::test]
    async fn originator_accepts_explicit_caller_self() {
        let fixture = Fixture::new().await;
        fixture.add_public_bot("driver").await;
        let detail = fixture
            .service
            .create(chat_group(Some("human_staff-1".into()), vec![]))
            .await
            .expect("create group");
        assert_eq!(collaboration_originator(detail), "human_staff-1");
    }

    #[tokio::test]
    async fn originator_rejects_bot_not_owned_by_caller() {
        let fixture = Fixture::new().await;
        fixture.add_public_bot("driver").await;
        fixture.add_bot_owned_by("bot-other", "someone-else", "public").await;
        let err = fixture
            .service
            .create(chat_group(Some("bot-other".into()), vec![]))
            .await
            .expect_err("unowned originator must be rejected");
        assert!(matches!(err, ApplicationError::Forbidden { .. }), "got {err:?}");
    }

    #[tokio::test]
    async fn originator_rejects_unregistered_originator() {
        let fixture = Fixture::new().await;
        fixture.add_public_bot("driver").await;
        let err = fixture
            .service
            .create(chat_group(Some("does-not-exist".into()), vec![]))
            .await
            .expect_err("unregistered originator must be rejected");
        assert!(matches!(err, ApplicationError::Forbidden { .. }), "got {err:?}");
    }

    #[tokio::test]
    async fn originator_rejects_other_human_originator() {
        let fixture = Fixture::new().await;
        fixture.add_public_bot("driver").await;
        let err = fixture
            .service
            .create(chat_group(Some("human_staff-2".into()), vec![]))
            .await
            .expect_err("another human as originator must be rejected");
        assert!(matches!(err, ApplicationError::Forbidden { .. }), "got {err:?}");
    }

    #[tokio::test]
    async fn owned_bot_originator_accepted_when_driver_reachable() {
        let fixture = Fixture::new().await;
        fixture.add_public_bot("driver").await;
        fixture.add_bot_owned_by("bot-o", "staff-1", "public").await;
        let detail = fixture
            .service
            .create(chat_group(Some("bot-o".into()), vec![]))
            .await
            .expect("owned-bot originator with public driver");
        assert_eq!(collaboration_originator(detail), "bot-o");
    }

    #[tokio::test]
    async fn owned_bot_originator_rejects_unreachable_driver() {
        let fixture = Fixture::new().await;
        fixture.add_bot_owned_by("driver", "someone-else", "protected").await;
        fixture.add_bot_owned_by("bot-o", "staff-1", "public").await;
        let err = fixture
            .service
            .create(chat_group_with_driver(
                "driver",
                Some("bot-o".into()),
                vec![],
            ))
            .await
            .expect_err("driver not reachable from originator bot");
        assert!(matches!(err, ApplicationError::Forbidden { .. }), "got {err:?}");
    }

    #[tokio::test]
    async fn driver_gated_against_human_originator_unless_owned_or_public() {
        // Originator-anchored: a Human originator may drive only a public bot
        // or a bot it owns. Here the protected driver is owned by someone else,
        // so the driver↔originator gate rejects creation (the driver is no
        // longer exempt from the originator reachability check).
        let fixture = Fixture::new().await;
        fixture.add_bot_owned_by("driver", "someone-else", "protected").await;
        let err = fixture
            .service
            .create(chat_group_with_driver("driver", None, vec![]))
            .await
            .expect_err("protected driver not owned by human originator");
        assert!(matches!(err, ApplicationError::Forbidden { .. }), "got {err:?}");
    }

    #[tokio::test]
    async fn owned_bot_originator_equal_to_driver_succeeds() {
        // A human designates an owned bot as BOTH originator and driver. The
        // driver is self-reachable — must not require self-friendship.
        let fixture = Fixture::new().await;
        fixture.add_bot_owned_by("bot-o", "staff-1", "protected").await;
        let detail = fixture
            .service
            .create(chat_group_with_driver("bot-o", Some("bot-o".into()), vec![]))
            .await
            .expect("originator==driver must succeed without self-friendship");
        assert_eq!(collaboration_originator(detail), "bot-o");
    }

    #[tokio::test]
    async fn owned_bot_originator_accepts_friend_driver() {
        // When the originator is a caller-owned Bot distinct from the driver,
        // the driver must be reachable from that originator bot — here, a
        // friend (covers the try_are_friends==true reachable branch).
        let fixture = Fixture::new().await;
        fixture.add_bot_owned_by("bot-o", "staff-1", "public").await;
        fixture.add_bot_owned_by("driver", "someone-else", "protected").await;
        fixture
            .friends
            .add_friendship("bot-o", "driver")
            .await
            .expect("originator/driver friendship");
        let detail = fixture
            .service
            .create(chat_group_with_driver("driver", Some("bot-o".into()), vec![]))
            .await
            .expect("friend driver reachable from originator bot");
        assert_eq!(collaboration_originator(detail), "bot-o");
    }

    #[tokio::test]
    async fn originator_rejects_registered_human_as_originator() {
        // A registered non-Bot actor (a human) passed as originator is not
        // the caller-self and is not a Bot, so it must be rejected with
        // invalid_originator (covers authorize_originator's actor_kind arm).
        let fixture = Fixture::new().await;
        fixture.add_public_bot("driver").await;
        fixture
            .bots
            .ensure_human_actor("staff-2", "Bob")
            .await
            .expect("register Human actor");
        let err = fixture
            .service
            .create(chat_group(Some("human_staff-2".into()), vec![]))
            .await
            .expect_err("registered non-bot originator must be rejected");
        assert!(
            matches!(err, ApplicationError::InvalidInput { ref code, .. } if code == "invalid_originator"),
            "got {err:?}"
        );
    }
