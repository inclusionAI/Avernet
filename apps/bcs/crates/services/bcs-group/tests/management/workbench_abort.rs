use super::*;

#[tokio::test]
async fn workbench_chat_abort_allows_session_bot_owner_and_absent_target() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("target", "Target", "public", Some("bob"));
    let mut target = Participant::bot("target", ParticipantRole::Consultant);
    target.mode = Some(ParticipantMode::Absent);
    let session = test_session(
        "session-abort",
        "group-under-test",
        vec![Participant::bot("driver", ParticipantRole::Driver), target],
    );
    let service = fixture.service_with_limits_and_session(
        5,
        10,
        10,
        Arc::new(StaticSessionManagement::new(session)),
    );
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .unwrap();

    service
        .authorize_chat_abort(WorkbenchChatAbortAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            session_id: "session-abort".to_string(),
            target_bot_id: "target".to_string(),
        })
        .await
        .expect("owner of a Session Bot may clean up an absent target Bot");

    let outside = service
        .authorize_chat_abort(WorkbenchChatAbortAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            session_id: "session-abort".to_string(),
            target_bot_id: "outside".to_string(),
        })
        .await;
    assert!(matches!(
        outside,
        Err(WorkbenchUseCaseError::TargetBotNotInSession)
    ));
}

#[tokio::test]
async fn workbench_chat_abort_rechecks_human_mode_and_authentication() {
    let fixture = Fixture::new().with_bot("driver", "Driver", "public", Some("alice"));
    let mut human = Participant::human("human_alice", ParticipantRole::Observer);
    human.mode = Some(ParticipantMode::Absent);
    let session = test_session(
        "session-abort",
        "group-under-test",
        vec![Participant::bot("driver", ParticipantRole::Driver), human],
    );
    let service = fixture.service_with_limits_and_session(
        5,
        10,
        10,
        Arc::new(StaticSessionManagement::new(session)),
    );
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .unwrap();

    let absent = service
        .authorize_chat_abort(WorkbenchChatAbortAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            session_id: "session-abort".to_string(),
            target_bot_id: "driver".to_string(),
        })
        .await;
    assert!(matches!(
        absent,
        Err(WorkbenchUseCaseError::ParticipantAbsent)
    ));

    let anonymous = service
        .authorize_chat_abort(WorkbenchChatAbortAuthorizationCommand {
            bound_actor_id: None,
            group_id: "group-under-test".to_string(),
            session_id: "session-abort".to_string(),
            target_bot_id: "driver".to_string(),
        })
        .await;
    assert!(matches!(
        anonymous,
        Err(WorkbenchUseCaseError::Unauthorized)
    ));
}
