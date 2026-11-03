use super::*;

#[tokio::test]
async fn human_cannot_auto_join_an_unreadable_session() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture
        .store_group_with_originator("g1", "driver", "human_other", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![Participant::bot("driver", ParticipantRole::Driver)],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");

    let error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session.id,
            bot_uuid: "human_staff-1".into(),
            mode: Some(ParticipantMode::Present),
            message_view_scope: None,
        })
        .await
        .expect_err("Human cannot join a Session it cannot read");

    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}
#[tokio::test]
async fn participant_mode_must_match_the_target_actor_kind() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_staff-1", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");

    let human_error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session.id.clone(),
            bot_uuid: "human_staff-1".into(),
            mode: Some(ParticipantMode::Auto),
            message_view_scope: None,
        })
        .await
        .expect_err("Human rejects Bot-only mode");
    assert_eq!(human_error.code(), "invalid_participant_mode");

    let bot_error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("driver"),
            session_id: session.id,
            bot_uuid: "driver".into(),
            mode: Some(ParticipantMode::Present),
            message_view_scope: None,
        })
        .await
        .expect_err("Bot rejects Human-only mode");
    assert_eq!(bot_error.code(), "invalid_participant_mode");
}
#[tokio::test]
async fn bot_mode_does_not_auto_add_a_missing_human() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "owned-bot"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("owned-bot", "staff-1", true)
        .await
        .expect("assign owned Bot");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "owned-bot",
            "staff-1",
        )
        .await;
    fixture
        .store_group_with_originator("g1", "driver", "human_other", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::bot("owned-bot", ParticipantRole::Consultant),
                ],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    let session_id = session.id.clone();

    let error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session.id,
            bot_uuid: "human_staff-1".into(),
            mode: Some(ParticipantMode::Auto),
            message_view_scope: None,
        })
        .await
        .expect_err("Bot-only mode cannot auto-add a Human");
    assert_eq!(error.code(), "invalid_participant_mode");

    let stored = fixture
        .session_repo
        .get(&session_id)
        .await
        .expect("stored session");
    assert!(
        stored
            .participants
            .iter()
            .all(|participant| participant.bot_uuid != "human_staff-1")
    );
}
#[tokio::test]
async fn update_mode_does_not_auto_add_a_missing_bot() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![Participant::bot("driver", ParticipantRole::Driver)],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    let session_id = session.id.clone();

    let error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("driver"),
            session_id: session.id,
            bot_uuid: "missing-bot".into(),
            mode: Some(ParticipantMode::Auto),
            message_view_scope: None,
        })
        .await
        .expect_err("missing Bot is not auto-added");
    assert_eq!(error.code(), "participant_not_found");

    let stored = fixture
        .session_repo
        .get(&session_id)
        .await
        .expect("stored session");
    assert!(
        stored
            .participants
            .iter()
            .all(|participant| participant.bot_uuid != "missing-bot")
    );
}
#[tokio::test]
async fn owned_bot_removal_is_owner_self_leave_not_management() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "bot-a"] {
        fixture.add_bot(bot).await;
    }
    // bot-a is owned by Human staff-1 via created_by.
    fixture
        .bots
        .save_created_by("bot-a", "staff-1", true)
        .await
        .expect("save owner");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "bot-a",
            "staff-1",
        )
        .await;
    fixture.store_group("g1", "driver", None).await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    // Seed the session directly so bot-a is an actual session participant
    // (the V1 create flow inherits the group roster, which lacks bot-a).
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::bot("bot-a", ParticipantRole::Consultant),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");
    let session_id = session.id.clone();

    // Non-owner Human (staff-2) is forbidden — neither self, owner, nor
    // manager/creator.
    let error = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: human_principal("staff-2"),
            session_id: session_id.clone(),
            bot_uuid: "bot-a".into(),
        })
        .await
        .expect_err("non-owner human should be forbidden");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));

    // The owner removing its own Bot is a voluntary leave (Design §8.7 parity
    // with the Group facade), not an exercise of management rights: it is
    // allowed even though staff-1 cannot manage the session. Ownership still
    // grants no management — see the sibling update-permission test.
    let removed = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session_id.clone(),
            bot_uuid: "bot-a".into(),
        })
        .await
        .expect("owner may withdraw its own bot");
    assert!(removed.deleted);
}
#[tokio::test]
async fn owned_bot_does_not_grant_session_participant_update_permission() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "bot-a"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("bot-a", "staff-1", true)
        .await
        .expect("save owner");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "bot-a",
            "staff-1",
        )
        .await;
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("bot-a", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let owner_error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session_id.clone(),
            bot_uuid: "bot-a".into(),
            mode: Some(ParticipantMode::Muted),
            message_view_scope: None,
        })
        .await
        .expect_err("Bot ownership grants detail read only, not management");
    assert!(matches!(
        owner_error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));

    // Non-owner Human is forbidden.
    let error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-2"),
            session_id,
            bot_uuid: "bot-a".into(),
            mode: Some(ParticipantMode::Auto),
            message_view_scope: None,
        })
        .await
        .expect_err("non-owner human should be forbidden");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}
