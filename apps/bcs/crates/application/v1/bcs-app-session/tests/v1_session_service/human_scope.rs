use super::*;

#[tokio::test]
async fn human_participant_can_update_own_mode_without_session_management() {
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
                    Participant::human("human_staff-1", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");

    let updated = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session.id,
            bot_uuid: "human_staff-1".into(),
            mode: Some(ParticipantMode::Present),
            message_view_scope: None,
        })
        .await
        .expect("Human participant updates own presence");

    assert_eq!(updated.actor_kind, ActorKind::Human);
    assert_eq!(updated.role, ParticipantRole::Observer);
    assert_eq!(updated.mode, ParticipantMode::Present);
}
#[tokio::test]
async fn readable_session_auto_adds_missing_human_with_selected_scope() {
    let participant_view_bindings = Arc::new(RecordingParticipantViewBindings::default());
    let fixture = Fixture::new()
        .await
        .with_participant_view_bindings(participant_view_bindings.clone());
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

    let inserted = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session.id,
            bot_uuid: "human_staff-1".into(),
            mode: Some(ParticipantMode::Present),
            message_view_scope: Some(MessageViewScope::Participant),
        })
        .await
        .expect("missing Human self-inserts with selected scope");

    assert_eq!(inserted.actor_kind, ActorKind::Human);
    assert_eq!(inserted.role, ParticipantRole::Observer);
    assert_eq!(inserted.mode, ParticipantMode::Present);
    assert_eq!(inserted.message_view_scope, MessageViewScope::Participant);
    let stored = fixture
        .session_repo
        .get(&session_id)
        .await
        .expect("stored session");
    let matching = stored
        .participants
        .iter()
        .filter(|participant| participant.bot_uuid == "human_staff-1")
        .collect::<Vec<_>>();
    assert_eq!(matching.len(), 1);
    assert_eq!(matching[0].role, ParticipantRole::Observer);
    assert_eq!(matching[0].mode, Some(ParticipantMode::Present));
    assert_eq!(
        matching[0].message_view_scope,
        MessageViewScope::Participant
    );
    assert!(
        participant_view_bindings
            .begun
            .lock()
            .expect("scope binding begin lock")
            .is_empty(),
        "initial membership creation must not acquire a scope-change lease"
    );
}
#[tokio::test]
async fn readable_session_auto_adds_missing_human_as_absent_observer() {
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

    let inserted = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session.id,
            bot_uuid: "human_staff-1".into(),
            mode: Some(ParticipantMode::Absent),
            message_view_scope: None,
        })
        .await
        .expect("missing Human self-inserts as absent");

    assert_eq!(inserted.actor_kind, ActorKind::Human);
    assert_eq!(inserted.role, ParticipantRole::Observer);
    assert_eq!(inserted.mode, ParticipantMode::Absent);
    let stored = fixture
        .session_repo
        .get(&session_id)
        .await
        .expect("stored session");
    let matching = stored
        .participants
        .iter()
        .filter(|participant| participant.bot_uuid == "human_staff-1")
        .collect::<Vec<_>>();
    assert_eq!(matching.len(), 1);
    assert_eq!(matching[0].role, ParticipantRole::Observer);
    assert_eq!(matching[0].mode, Some(ParticipantMode::Absent));
}
#[tokio::test]
async fn readable_session_auto_add_inherits_group_human_scope() {
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
    let mut group_human = Participant::human("human_staff-1", ParticipantRole::Observer);
    group_human.message_view_scope = MessageViewScope::Participant;
    fixture
        .groups
        .add_participant("g1", group_human)
        .await
        .expect("add Human to Group");
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

    let inserted = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session.id,
            bot_uuid: "human_staff-1".into(),
            mode: Some(ParticipantMode::Present),
            message_view_scope: None,
        })
        .await
        .expect("missing Human inherits Group scope when joining Session");

    assert_eq!(inserted.mode, ParticipantMode::Present);
    assert_eq!(inserted.message_view_scope, MessageViewScope::Participant);
    let stored = fixture
        .session_repo
        .get(&session_id)
        .await
        .expect("stored session");
    let human = stored
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_staff-1")
        .expect("stored Human participant");
    assert_eq!(human.mode, Some(ParticipantMode::Present));
    assert_eq!(human.message_view_scope, MessageViewScope::Participant);
}
#[tokio::test]
async fn legacy_chat_session_with_state_machine_run_allows_participant_scope_and_history() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture
        .store_group_with_originator("g1", "driver", "human_staff-1", None)
        .await;
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
                created_by: Some("human_staff-1".to_string()),
                message_visibility_version: 0,
                ..Default::default()
            },
        )
        .await
        .expect("seed legacy Chat session");
    *fixture
        .runtime
        .session_run
        .lock()
        .expect("runtime session run lock") = Some(StateMachineRunView {
                terminal_result: None,
        run: StateMachineRun {
            run_id: "legacy-run".to_string(),
            root_run_id: Some("legacy-run".to_string()),
            rerun_of: None,
            definition_id: "definition-1".to_string(),
            definition_version: 1,
            group_id: "g1".to_string(),
            group_version: group.version,
            session_id: session.id.clone(),
            session_activation_count: None,
            created_by: Some("human_staff-1".to_string()),
            status: StateMachineRunStatus::Completed,
            input: serde_json::Value::Null,
            opening_message_override: None,
            output: None,
            error: None,
            created_at: 1,
            updated_at: 1,
            completed_at: Some(1),
        },
        nodes: Vec::new(),
        node_execution_metadata: None,
        judge_outputs: Vec::new(),
    });

    let updated = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-1"),
            session_id: session.id.clone(),
            bot_uuid: "human_staff-1".to_string(),
            mode: None,
            message_view_scope: Some(MessageViewScope::Participant),
        })
        .await
        .expect("message scope must not be gated by state-machine artifacts");
    assert_eq!(updated.message_view_scope, MessageViewScope::Participant);

    let history = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_principal("staff-1"),
            session_id: session.id,
            before: None,
            limit: 100,
            view_bot_id: None,
        },
    )
    .await
    .expect("participant history remains available for legacy sessions");
    assert!(history.is_empty());
    let calls = fixture
        .runtime
        .participant_history_calls
        .lock()
        .expect("runtime participant history lock");
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].3.actor_id, "human_staff-1");
}
#[tokio::test]
async fn session_manager_cannot_update_another_human_mode() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture
        .bots
        .save_created_by("driver", "staff-manager", true)
        .await
        .expect("assign driver owner");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "driver",
            "staff-manager",
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
                    Participant::human("human_target", ParticipantRole::Observer),
                ],
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
            caller: human_principal("staff-manager"),
            session_id: session.id,
            bot_uuid: "human_target".into(),
            mode: Some(ParticipantMode::Present),
            message_view_scope: None,
        })
        .await
        .expect_err("Session manager cannot control another Human's presence");

    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}
#[tokio::test]
async fn session_manager_can_update_another_human_scope_without_changing_mode() {
    let participant_view_bindings = Arc::new(RecordingParticipantViewBindings::default());
    let fixture = Fixture::new()
        .await
        .with_participant_view_bindings(participant_view_bindings.clone());
    fixture.add_bot("driver").await;
    fixture
        .bots
        .save_created_by("driver", "staff-manager", true)
        .await
        .expect("assign driver owner");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "driver",
            "staff-manager",
        )
        .await;
    fixture
        .store_group_with_originator("g1", "driver", "human_other", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let mut target = Participant::human("human_target", ParticipantRole::Observer);
    target.mode = Some(ParticipantMode::Absent);
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    target,
                ],
                group_version: Some(group.version),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    let session_id = session.id.clone();

    let updated = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("staff-manager"),
            session_id: session.id,
            bot_uuid: "human_target".into(),
            mode: None,
            message_view_scope: Some(MessageViewScope::Participant),
        })
        .await
        .expect("Session manager may update another Human's scope");

    assert_eq!(updated.mode, ParticipantMode::Absent);
    assert_eq!(updated.message_view_scope, MessageViewScope::Participant);
    let stored = fixture
        .session_repo
        .get(&session_id)
        .await
        .expect("stored session");
    let target = stored
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_target")
        .expect("target remains in session");
    assert_eq!(target.mode, Some(ParticipantMode::Absent));
    assert_eq!(target.message_view_scope, MessageViewScope::Participant);
    assert_eq!(
        *participant_view_bindings
            .begun
            .lock()
            .expect("scope binding begin lock"),
        vec![(session_id.clone(), "human_target".to_string())]
    );
    let finished = participant_view_bindings
        .finished
        .lock()
        .expect("scope binding finish lock");
    assert_eq!(finished.len(), 1);
    assert_eq!(finished[0].scope_id, session_id);
    assert_eq!(finished[0].human_actor_id, "human_target");
}
#[tokio::test]
async fn session_manager_cannot_auto_add_another_human() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture
        .bots
        .save_created_by("driver", "staff-manager", true)
        .await
        .expect("assign driver owner");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "driver",
            "staff-manager",
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
            caller: human_principal("staff-manager"),
            session_id: session.id,
            bot_uuid: "human_target".into(),
            mode: Some(ParticipantMode::Present),
            message_view_scope: None,
        })
        .await
        .expect_err("Session manager cannot auto-add another Human");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));

    let stored = fixture
        .session_repo
        .get(&session_id)
        .await
        .expect("stored session");
    assert!(
        stored
            .participants
            .iter()
            .all(|participant| participant.bot_uuid != "human_target")
    );
}
