use super::*;

#[tokio::test]
async fn human_owner_of_group_driver_can_add_session_participant_without_human_membership() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "newcomer"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("driver", "staff-driver", true)
        .await
        .expect("assign driver owner");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "driver",
            "staff-driver",
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
                    Participant::bot("expert", ParticipantRole::Consultant),
                ],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");

    let err = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("staff-unrelated"),
            session_id: session.id.clone(),
            bot_uuid: "newcomer".into(),
            message_view_scope: None,
        })
        .await
        .expect_err("unrelated Human cannot manage session through Bot ownership");
    assert!(matches!(
        err,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));

    let added = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("staff-driver"),
            session_id: session.id,
            bot_uuid: "newcomer".into(),
            message_view_scope: None,
        })
        .await
        .expect("Human owner of group driver can manage session");

    assert_eq!(added.actor_id, "newcomer");
}
#[tokio::test]
async fn human_owner_of_session_creator_can_update_session_without_group_management() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "creator-bot"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("creator-bot", "staff-creator", true)
        .await
        .expect("assign creator owner");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "creator-bot",
            "staff-creator",
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
                participants: vec![Participant::bot("expert", ParticipantRole::Consultant)],
                group_version: Some(group.version),
                created_by: Some("creator-bot".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");

    let err = fixture
        .service
        .update(UpdateSession {
            caller: human_principal("staff-unrelated"),
            session_id: session.id.clone(),
            title: Some("unrelated update".into()),
        })
        .await
        .expect_err("unrelated Human cannot manage session through creator ownership");
    assert!(matches!(
        err,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));

    let detail = fixture
        .service
        .update(UpdateSession {
            caller: human_principal("staff-creator"),
            session_id: session.id,
            title: Some("owned creator update".into()),
        })
        .await
        .expect("Human owner of session creator can manage session");

    assert_eq!(detail.title.as_deref(), Some("owned creator update"));
}
#[tokio::test]
async fn chat_manager_role_does_not_grant_session_management_to_human_owner() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "manager", "newcomer"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("manager", "staff-manager-owner", true)
        .await
        .expect("assign manager owner");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "manager",
            "staff-manager-owner",
        )
        .await;
    fixture
        .store_group_with_originator("g1", "driver", "human_other", None)
        .await;
    let mut group = fixture.groups.get("g1").await.expect("group exists");
    group
        .participants
        .push(Participant::bot("manager", ParticipantRole::Manager));
    fixture
        .groups
        .upsert(group.clone())
        .await
        .expect("store group");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::bot("expert", ParticipantRole::Consultant),
                    Participant::bot("manager", ParticipantRole::Manager),
                ],
                group_version: Some(group.version),
                created_by: Some("expert".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");

    let err = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("staff-manager-owner"),
            session_id: session.id,
            bot_uuid: "newcomer".into(),
            message_view_scope: None,
        })
        .await
        .expect_err("Chat manager role must not grant session management authority");
    assert!(matches!(
        err,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}
#[tokio::test]
async fn participant_add_update_remove_lifecycle() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "newcomer"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    // Add.
    let added = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
            message_view_scope: None,
        })
        .await
        .expect("add participant");
    assert_eq!(added.actor_id, "newcomer");
    assert_eq!(added.role, ParticipantRole::Consultant);
    assert_eq!(added.mode, ParticipantMode::Auto);

    // Adding twice is a conflict.
    let error = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
            message_view_scope: None,
        })
        .await
        .expect_err("duplicate add should conflict");
    assert_eq!(error.code(), "participant_already_exists");

    // Update mode.
    let updated = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
            mode: Some(ParticipantMode::Muted),
            message_view_scope: None,
        })
        .await
        .expect("update participant");
    assert_eq!(updated.mode, ParticipantMode::Muted);

    // Remove.
    let removed = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
        })
        .await
        .expect("delete participant");
    assert!(removed.deleted);

    // Remove again is idempotent.
    let again = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: bot_principal("driver"),
            session_id,
            bot_uuid: "newcomer".into(),
        })
        .await
        .expect("idempotent delete participant");
    assert!(!again.deleted);
}
#[tokio::test]
async fn update_participant_emits_mode_changed_system_message_only_on_change() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    // Add expert (defaults to Auto) so its mode can be updated.
    let added = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "expert".into(),
            message_view_scope: None,
        })
        .await
        .expect("add expert");
    assert_eq!(added.mode, ParticipantMode::Auto);

    // Changing expert Auto -> Muted must emit ParticipantModeChanged.
    fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "expert".into(),
            mode: Some(ParticipantMode::Muted),
            message_view_scope: None,
        })
        .await
        .expect("mute expert");

    let mode_changes = recorded_mode_changes(&fixture, &session_id);
    assert_eq!(mode_changes.len(), 1, "exactly one mode-change event");
    assert_eq!(mode_changes[0].0, "expert");
    assert_eq!(mode_changes[0].1, Some(ParticipantMode::Auto));
    assert_eq!(mode_changes[0].2, ParticipantMode::Muted);

    // Re-applying the same mode is a no-op and must NOT emit another event.
    fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "expert".into(),
            mode: Some(ParticipantMode::Muted),
            message_view_scope: None,
        })
        .await
        .expect("re-mute expert is idempotent");

    let mode_changes = recorded_mode_changes(&fixture, &session_id);
    assert_eq!(
        mode_changes.len(),
        1,
        "no new event when the mode is unchanged"
    );
}
#[tokio::test]
async fn add_participant_emits_bot_joined_system_message() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    // Adding a participant must emit a BotJoined notification (parity with the
    // legacy `add_session_participant` route).
    fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "expert".into(),
            message_view_scope: None,
        })
        .await
        .expect("add expert");

    let joined = recorded_bot_joined(&fixture, &session_id);
    assert_eq!(joined.len(), 1, "exactly one BotJoined event");
    assert_eq!(joined[0], "expert");

    // A duplicate add is a conflict and must NOT emit another event.
    let error = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "expert".into(),
            message_view_scope: None,
        })
        .await
        .expect_err("duplicate add should conflict");
    assert_eq!(error.code(), "participant_already_exists");
    assert_eq!(
        recorded_bot_joined(&fixture, &session_id).len(),
        1,
        "no new event on duplicate add"
    );
}
#[tokio::test]
async fn delete_participant_emits_bot_left_system_message() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();
    fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "expert".into(),
            message_view_scope: None,
        })
        .await
        .expect("add expert");

    // Removing a participant must emit a BotLeft notification (parity with the
    // legacy `remove_session_participant` route).
    let removed = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "expert".into(),
        })
        .await
        .expect("delete expert");
    assert!(removed.deleted);

    let left = recorded_bot_left(&fixture, &session_id);
    assert_eq!(left.len(), 1, "exactly one BotLeft event");
    assert_eq!(left[0], "expert");

    // Removing an already-absent participant is idempotent and must NOT emit
    // another event.
    let again = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "expert".into(),
        })
        .await
        .expect("idempotent delete");
    assert!(!again.deleted);
    assert_eq!(
        recorded_bot_left(&fixture, &session_id).len(),
        1,
        "no new event on idempotent delete"
    );
}
#[tokio::test]
async fn delete_participant_allows_removing_group_originator() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    // Group whose originator is the human `human_owner-1` (distinct from the
    // driver bot). The originator is a session participant and must be allowed
    // to leave the session — only the driver/manager are structurally pinned.
    fixture
        .store_group_with_originator("g1", "driver", "human_owner-1", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_owner-1", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    let removed = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: human_principal("owner-1"),
            session_id: session.id.clone(),
            bot_uuid: "human_owner-1".into(),
        })
        .await
        .expect("originator can leave the session");
    assert!(removed.deleted);
    let left = recorded_bot_left(&fixture, &session.id);
    assert_eq!(left.len(), 1);
    assert_eq!(left[0], "human_owner-1");
}
#[tokio::test]
async fn delete_participant_still_rejects_group_driver() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture
        .store_group_with_originator("g1", "driver", "human_owner-1", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_owner-1", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    let error = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: human_principal("owner-1"),
            session_id: session.id.clone(),
            bot_uuid: "driver".into(),
        })
        .await
        .expect_err("driver remains pinned");
    assert!(
        matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::InvalidInput { .. }
        ),
        "expected invalid_request, got {error:?}"
    );
    // No leave event when removal is rejected.
    assert!(recorded_bot_left(&fixture, &session.id).is_empty());
}
#[tokio::test]
async fn delete_participant_self_leave_needs_no_manage_rights() {
    // A plain Human participant — not a group manager and not the session
    // creator — may remove its own Human Actor: self-leave is voluntary and
    // must not require session-manage authorization.
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
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_leaver", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    let removed = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: human_principal("leaver"),
            session_id: session.id.clone(),
            bot_uuid: "human_leaver".into(),
        })
        .await
        .expect("plain participant may leave the session");
    assert!(removed.deleted);
    let left = recorded_bot_left(&fixture, &session.id);
    assert_eq!(left.len(), 1);
    assert_eq!(left[0], "human_leaver");
}
#[tokio::test]
async fn delete_participant_owner_removes_owned_bot_without_manage_rights() {
    // Removing a Bot owned by the caller is the owner's voluntary leave and
    // needs no session-manage authorization either.
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.add_bot_with("expert", "public", "owner-9").await;
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
                    Participant::bot("expert", ParticipantRole::Consultant),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    let removed = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: human_principal("owner-9"),
            session_id: session.id.clone(),
            bot_uuid: "expert".into(),
        })
        .await
        .expect("owner may withdraw its own bot");
    assert!(removed.deleted);
    let left = recorded_bot_left(&fixture, &session.id);
    assert_eq!(left.len(), 1);
    assert_eq!(left[0], "expert");
}
#[tokio::test]
async fn delete_participant_non_manager_still_cannot_remove_others() {
    // Self-leave does not widen authorization: a plain participant removing
    // someone else's Actor still requires session-manage rights.
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.add_bot_with("expert", "public", "owner-9").await;
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
                    Participant::bot("expert", ParticipantRole::Consultant),
                    Participant::human("human_leaver", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    let error = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: human_principal("leaver"),
            session_id: session.id.clone(),
            bot_uuid: "expert".into(),
        })
        .await
        .expect_err("removing another actor requires manage rights");
    assert_eq!(error.code(), "forbidden");
    assert!(recorded_bot_left(&fixture, &session.id).is_empty());
}
#[tokio::test]
async fn delete_participant_driver_owner_self_leave_still_rejected() {
    // The driver/manager pin applies to self-leaves too: the owner of the
    // driver Bot acting as the driver may not remove it from the session.
    let fixture = Fixture::new().await;
    fixture.add_bot_with("driver", "public", "owner-1").await;
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
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    let error = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: human_principal("owner-1"),
            session_id: session.id.clone(),
            bot_uuid: "driver".into(),
        })
        .await
        .expect_err("driver remains pinned even for its owner");
    assert!(
        matches!(error, bcs_service_api::application::v1::ApplicationError::InvalidInput { .. }),
        "expected invalid_request, got {error:?}"
    );
    assert!(recorded_bot_left(&fixture, &session.id).is_empty());
}
