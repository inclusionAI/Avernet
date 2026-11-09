use super::*;

#[tokio::test]
async fn workbench_human_bot_dm_rejects_owner_bot_proxy_sender_by_default() {
    let fixture = Fixture::new().with_human("human_alice", "Alice").with_bot(
        "assistant",
        "Assistant",
        "public",
        Some("alice"),
    );
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_dm(DmCreateCommand {
            group_id: Some("dm-under-test".to_string()),
            caller_actor_id: Some("human_alice".to_string()),
            driver_bot: None,
            target_actor_id: "assistant".to_string(),
            label: None,
            topic: None,
            context: None,
            provisioning: false,
        })
        .await
        .unwrap();

    service
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "dm-under-test".to_string(),
            from_actor_id: "human_alice".to_string(),
            session_id: None,
        })
        .await
        .expect("bound human participant can speak in DM");

    let proxy = service
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "dm-under-test".to_string(),
            from_actor_id: "assistant".to_string(),
            session_id: None,
        })
        .await;
    assert!(matches!(proxy, Err(WorkbenchUseCaseError::ForbiddenSender)));
}

#[tokio::test]
async fn workbench_normal_group_allows_bound_human_sender_when_participant() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .unwrap();
    service
        .update_participant_mode(GroupParticipantModeCommand {
            caller_actor_id: "human_alice".to_string(),
            group_id: "group-under-test".to_string(),
            actor_id: "human_alice".to_string(),
            mode: bcs_service_api::ParticipantMode::Present,
            message_view_scope: None,
        })
        .await
        .unwrap();

    service
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            from_actor_id: "human_alice".to_string(),
            session_id: None,
        })
        .await
        .expect("normal group should still allow bound Human actor sender");
}

#[tokio::test]
async fn workbench_chat_send_allows_bound_human_sender_when_present_in_session() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("owner"))
        .with_human("human_alice", "Alice");
    let session_id = "group-under-test:abcdef12".to_string();
    let session = test_session(
        &session_id,
        "group-under-test",
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            {
                let mut human = Participant::human("human_alice", ParticipantRole::Observer);
                human.mode = Some(ParticipantMode::Present);
                human
            },
        ],
    );
    let service = fixture
        .service_with_limits_and_session(5, 10, 10, Arc::new(StaticSessionManagement::new(session)));
    service
        .create_group(create_cmd(
            Some("human_owner"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .unwrap();

    service
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            from_actor_id: "human_alice".to_string(),
            session_id: Some(session_id),
        })
        .await
        .expect("session participants should be allowed to send inside that session");
}

#[tokio::test]
async fn workbench_session_service_rejects_invalid_sender_states() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("helper", "Helper", "public", Some("alice"))
        .with_bot("outside", "Outside", "public", Some("alice"))
        .with_bot("bob-bot", "Bob Bot", "public", Some("bob"));
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("helper", Some("consultant")),
                participant("bob-bot", Some("consultant")),
            ],
        ))
        .await
        .unwrap();

    let no_cookie = service
        .connect(WorkbenchConnectCommand {
            bound_actor_id: None,
            group_id: "group-under-test".to_string(),
            session_id: None,
            view_actor_id: None,
        })
        .await;
    assert!(matches!(no_cookie, Err(WorkbenchUseCaseError::Unauthorized)));

    let no_group_access = service
        .connect(WorkbenchConnectCommand {
            bound_actor_id: Some("human_charlie".to_string()),
            group_id: "group-under-test".to_string(),
            session_id: None,
            view_actor_id: None,
        })
        .await;
    assert!(matches!(
        no_group_access,
        Err(WorkbenchUseCaseError::ForbiddenGroupAccess)
    ));

    let explicit_unowned_view = service
        .connect(WorkbenchConnectCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            session_id: None,
            view_actor_id: Some("bob-bot".to_string()),
        })
        .await;
    assert!(matches!(
        explicit_unowned_view,
        Err(WorkbenchUseCaseError::ForbiddenViewActor)
    ));

    let mut group = fixture.group.get("group-under-test").await.unwrap();
    let helper = group
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "helper")
        .expect("helper participant");
    helper.mode = Some(bcs_service_api::ParticipantMode::Absent);
    fixture.group.upsert(group).await.unwrap();

    let absent_sender = service
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            from_actor_id: "helper".to_string(),
            session_id: None,
        })
        .await;
    assert!(matches!(
        absent_sender,
        Err(WorkbenchUseCaseError::ParticipantAbsent)
    ));

    let outside_sender = service
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            from_actor_id: "outside".to_string(),
            session_id: None,
        })
        .await;
    assert!(matches!(
        outside_sender,
        Err(WorkbenchUseCaseError::SenderNotInGroup)
    ));

    let not_owned_sender = service
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            from_actor_id: "bob-bot".to_string(),
            session_id: None,
        })
        .await;
    assert!(matches!(
        not_owned_sender,
        Err(WorkbenchUseCaseError::ForbiddenSender)
    ));
}
