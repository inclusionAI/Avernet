use super::*;

#[tokio::test]
async fn list_messages_delegates_and_returns_legacy_group_messages_unchanged() {
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
                    Participant::human("human_staff-1", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    let expected = rich_group_message();
    *fixture.history.messages.lock().expect("messages lock") = vec![expected.clone()];

    let messages = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_principal("staff-1"),
            session_id: session.id.clone(),
            before: Some(1_786_590_000_000),
            limit: 25,
            view_bot_id: None,
        },
    )
    .await
    .expect("list messages");

    assert_eq!(
        serde_json::to_value(&messages).expect("serialize messages"),
        serde_json::to_value([expected]).expect("serialize expected")
    );
    let calls = fixture.history.session_calls.lock().expect("history lock");
    let call = calls.last().expect("history call");
    assert_eq!(call.group_id, "g1");
    assert_eq!(call.session_id, session.id);
    assert_eq!(call.view_bot_id.as_deref(), Some("human_staff-1"));
    assert_eq!(call.limit, 25);
    assert_eq!(call.before, Some(1_786_590_000_000));
    assert_eq!(
        call.caller,
        CallerContext::Human(HumanActor {
            actor_id: "human_staff-1".into(),
            staff_no: "staff-1".into(),
        })
    );
    drop(calls);
    assert_eq!(
        fixture
            .history
            .session_options
            .lock()
            .expect("history options lock")
            .last(),
        Some(&MessageHistoryOptions::default())
    );

    SessionMessageService::list_with_options(
        &fixture.service,
        ListSessionMessages {
            caller: human_principal("staff-1"),
            session_id: session.id,
            before: None,
            limit: 25,
            view_bot_id: None,
        },
        MessageHistoryOptions {
            include_pending: true,
        },
    )
    .await
    .expect("list messages with pending snapshots");
    assert_eq!(
        fixture
            .history
            .session_options
            .lock()
            .expect("history options lock")
            .last(),
        Some(&MessageHistoryOptions {
            include_pending: true,
        })
    );
}
#[tokio::test]
async fn participant_history_merges_own_legacy_one_shot_response_snapshot() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a"],
            "human_staff-1",
            None,
        )
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let mut human = Participant::human("human_staff-1", ParticipantRole::Observer);
    human.message_view_scope = MessageViewScope::Participant;
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::bot("worker-a", ParticipantRole::Worker),
                    human,
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed manager-worker session");

    let mut prompt = rich_group_message();
    prompt.id = "run-1:review:1:human-input-prompt".into();
    prompt.timestamp = 10;
    prompt.content = "请审核".into();
    *fixture.history.messages.lock().expect("messages lock") = vec![prompt.clone()];

    let mut duplicate_prompt = prompt.clone();
    duplicate_prompt.content = "snapshot duplicate must not replace durable message".into();
    let mut response = rich_group_message();
    response.id = "run-1:review:1:1-output".into();
    response.timestamp = 20;
    response.sender = "human_staff-1".into();
    response.content = "通过".into();
    response.role = MessageRole::User;
    response.bot_name = None;
    *fixture
        .runtime
        .history_result
        .lock()
        .expect("runtime result lock") = Some(SessionHistoryResult {
        session_id: session.id.clone(),
        messages: vec![duplicate_prompt, response.clone()],
        limit: 100,
        before: None,
        next_before: None,
    });

    let messages = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_principal("staff-1"),
            session_id: session.id.clone(),
            before: None,
            limit: 100,
            view_bot_id: None,
        },
    )
    .await
    .expect("participant history");

    assert_eq!(
        messages
            .iter()
            .map(|message| (message.id.as_str(), message.content.as_str()))
            .collect::<Vec<_>>(),
        vec![
            ("run-1:review:1:1-output", "通过"),
            ("run-1:review:1:human-input-prompt", "请审核"),
        ]
    );
    assert_eq!(messages[0].role, MessageRole::User);
    assert_eq!(
        fixture
            .runtime
            .participant_history_calls
            .lock()
            .expect("participant history calls")
            .as_slice(),
        &[(
            session.id,
            100,
            None,
            HumanMessageView {
                actor_id: "human_staff-1".into(),
                scope: MessageViewScope::Participant,
                allow_legacy_unclassified_chat: false,
            },
        )]
    );
}
#[tokio::test]
async fn list_messages_rejects_invalid_limit_before_calling_history() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        Vec::new(),
        None,
        None,
    )
    .await;
    let error = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: bot_principal("driver"),
            session_id: outcome.session.session_id,
            before: None,
            limit: 0,
            view_bot_id: Some("driver".into()),
        },
    )
    .await
    .expect_err("zero limit should be invalid");
    assert_eq!(error.code(), "invalid_request");
    assert!(
        fixture
            .history
            .session_calls
            .lock()
            .expect("history lock")
            .is_empty()
    );
}
#[tokio::test]
async fn state_machine_session_history_uses_runtime_and_returns_messages_unchanged() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_state_machine_group("g1", "driver").await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_staff-1", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed state-machine session");
    let expected = rich_group_message();
    *fixture
        .runtime
        .history_result
        .lock()
        .expect("runtime result lock") = Some(SessionHistoryResult {
        session_id: session.id.clone(),
        messages: vec![expected.clone()],
        limit: 20,
        before: Some(1_786_590_000_000),
        next_before: None,
    });

    let messages = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_principal("staff-1"),
            session_id: session.id.clone(),
            before: Some(1_786_590_000_000),
            limit: 20,
            view_bot_id: None,
        },
    )
    .await
    .expect("state-machine history");

    assert_eq!(
        serde_json::to_value(messages).expect("serialize messages"),
        serde_json::to_value([expected]).expect("serialize expected")
    );
    assert_eq!(
        fixture
            .runtime
            .history_calls
            .lock()
            .expect("runtime history lock")
            .as_slice(),
        &[(session.id, 20, Some(1_786_590_000_000))]
    );
    assert!(
        fixture
            .history
            .session_calls
            .lock()
            .expect("history lock")
            .is_empty()
    );
}
