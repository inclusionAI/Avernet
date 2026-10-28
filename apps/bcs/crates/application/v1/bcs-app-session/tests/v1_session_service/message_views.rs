use super::*;

#[tokio::test]
async fn omitted_message_view_selects_human_and_requires_session_participation() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "driver",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let error = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: bot_principal("worker-a"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: None,
        },
    )
    .await
    .expect_err("omission selects human_worker-a, which is not a participant");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}
#[tokio::test]
async fn human_caller_can_explicitly_select_an_owned_bot_message_view() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "driver",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let messages = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: bot_principal("worker-a"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: Some("worker-a".to_string()),
        },
    )
    .await
    .expect("list messages");

    assert!(messages.is_empty());
    let calls = fixture.history.session_calls.lock().expect("history lock");
    assert_eq!(
        calls.last().expect("history call").view_bot_id.as_deref(),
        Some("worker-a")
    );
}
#[tokio::test]
async fn human_caller_cannot_select_an_unowned_bot_message_view() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "driver",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let error = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: bot_principal("worker-a"),
            session_id,
            before: None,
            limit: 100,
            view_bot_id: Some("worker-b".to_string()),
        },
    )
    .await
    .expect_err("an unowned Bot view should be forbidden");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}
#[tokio::test]
async fn omitted_human_message_view_equals_the_human_participant_view() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "human_staff-1",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let messages = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_principal("staff-1"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: None,
        },
    )
    .await
    .expect("list messages");

    assert!(messages.is_empty());
    let calls = fixture.history.session_calls.lock().expect("history lock");
    assert_eq!(
        calls.last().expect("history call").view_bot_id.as_deref(),
        Some("human_staff-1")
    );
}
#[tokio::test]
async fn human_view_session_messages_as_self_human() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "human_staff-1",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let messages = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_principal("staff-1"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: Some("human_staff-1".to_string()),
        },
    )
    .await
    .expect("list messages");

    assert!(messages.is_empty());
    let calls = fixture.history.session_calls.lock().expect("history lock");
    assert_eq!(
        calls.last().expect("history call").view_bot_id.as_deref(),
        Some("human_staff-1")
    );
}
#[tokio::test]
async fn human_view_session_messages_as_owned_bot() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    // worker-a is owned by Human staff-1 via created_by.
    fixture
        .bots
        .save_created_by("worker-a", "staff-1", true)
        .await
        .expect("save owner");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "worker-a",
            "staff-1",
        )
        .await;
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "human_staff-1",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let messages = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_principal("staff-1"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: Some("worker-a".to_string()),
        },
    )
    .await
    .expect("human views as owned bot");

    assert!(messages.is_empty());
    let calls = fixture.history.session_calls.lock().expect("history lock");
    assert_eq!(
        calls.last().expect("history call").view_bot_id.as_deref(),
        Some("worker-a")
    );
}
#[tokio::test]
async fn human_view_session_messages_as_unowned_bot_forbidden() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    // worker-b is registered but NOT owned by staff-1 (created_by stays None).
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "human_staff-1",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let error = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_principal("staff-1"),
            session_id,
            before: None,
            limit: 100,
            view_bot_id: Some("worker-b".to_string()),
        },
    )
    .await
    .expect_err("human viewing as unowned bot should be forbidden");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}
#[tokio::test]
async fn get_preserves_human_participant_from_legacy_invitation_join() {
    // Vey7i: the legacy invitation-accept path (`join_session_by_invite`)
    // inserts a Human participant directly into `session.participants` with
    // `actor_kind: Human, mode: Present` (see
    // `bcs-group/src/application/invite.rs`). The V1 facade `get` must surface
    // that participant verbatim — `actor_kind: Human` and `mode: Present` —
    // NOT boot-truncate it to the Bot-only `Auto`. This seeds the same roster
    // shape via the repo (the contract `join_session_by_invite` ultimately
    // writes via `SessionManagementService::add_participant`) and reads it
    // back through the V1 `SessionService::get` projection path.
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;

    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant {
                        bot_uuid: "human_staff-1".into(),
                        bot_name: Some("Alice".into()),
                        kind: None,
                        role: ParticipantRole::Consultant,
                        actor_kind: ActorKind::Human,
                        mode: Some(ParticipantMode::Present),
                        tags: Vec::new(),
                        message_view_scope: MessageViewScope::Full,
                    },
                ],
                group_version: Some(group.version),
                caller_id: Some("driver".to_string()),
                caller_principal: Some("driver".to_string()),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session with human participant");

    let detail = fixture
        .service
        .get(GetSession {
            caller: bot_principal("driver"),
            session_id: session.id.clone(),
        })
        .await
        .expect("get session");

    let human = detail
        .participants
        .iter()
        .find(|p| p.actor_id == "human_staff-1")
        .expect("human participant present in V1 projection");
    assert_eq!(human.actor_kind, ActorKind::Human);
    assert_eq!(human.mode, ParticipantMode::Present);
    assert_eq!(human.role, ParticipantRole::Consultant);

    // The Bot driver is still projected as a Bot with Auto (no regression).
    let driver = detail
        .participants
        .iter()
        .find(|p| p.actor_id == "driver")
        .expect("driver participant present");
    assert_eq!(driver.actor_kind, ActorKind::Bot);
    assert_eq!(driver.mode, ParticipantMode::Auto);
}
