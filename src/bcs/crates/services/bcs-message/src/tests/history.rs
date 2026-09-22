use super::*;

#[tokio::test]
async fn manager_worker_pending_history_reuses_durable_owner_visibility() {
    let (mut service, _repo, _sessions, _fallback, session_id) =
        service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
    service.pending_messages = Arc::new(StaticPendingMessages(vec![
        PendingGroupMessage {
            run_id: "manager-run".to_string(),
            bot_id: "mgr".to_string(),
            session_id: Some(session_id.clone()),
            created_at_ms: 30,
            kind: PendingGroupMessageKind::Chat {
                text: "manager partial".to_string(),
            },
        },
        PendingGroupMessage {
            run_id: "worker-a-run".to_string(),
            bot_id: "worker-a".to_string(),
            session_id: Some(session_id.clone()),
            created_at_ms: 20,
            kind: PendingGroupMessageKind::ToolCall {
                tool_call_id: "call-1".to_string(),
                tool_name: "search".to_string(),
                tool_args: serde_json::json!({"q": "secret"}),
            },
        },
        PendingGroupMessage {
            run_id: "worker-b-run".to_string(),
            bot_id: "worker-b".to_string(),
            session_id: Some(session_id.clone()),
            created_at_ms: 10,
            kind: PendingGroupMessageKind::Chat {
                text: "worker b private".to_string(),
            },
        },
    ]));

    let durable_only = service
        .get_session_history(session_cmd("group-1", &session_id, None))
        .await
        .expect("default durable history");
    assert!(durable_only.messages.is_empty());

    let human = service
        .get_session_history_with_options(
            session_cmd("group-1", &session_id, None),
            MessageHistoryOptions {
                include_pending: true,
            },
        )
        .await
        .expect("human history");
    assert_eq!(human.messages.len(), 1);
    assert_eq!(human.messages[0].run_id, "manager-run");
    assert_eq!(human.messages[0].id, "bcs-run:manager-run:mgr");

    let mut participant_human = Participant::human("human-1", ParticipantRole::Observer);
    participant_human.message_view_scope = MessageViewScope::Participant;
    _sessions
        .add_participant(&session_id, participant_human)
        .await
        .expect("add participant Human");
    let participant_history = service
        .get_session_history_with_options(
            session_cmd("group-1", &session_id, Some("human-1")),
            MessageHistoryOptions {
                include_pending: true,
            },
        )
        .await
        .expect("participant Human history");
    assert!(
        participant_history.messages.is_empty(),
        "participant Human must not receive FullOnly pending ManagerWorker content"
    );

    let worker = service
        .get_session_history_with_options(
            session_cmd("group-1", &session_id, Some("worker-a")),
            MessageHistoryOptions {
                include_pending: true,
            },
        )
        .await
        .expect("worker history");
    assert_eq!(worker.messages.len(), 1);
    assert_eq!(worker.messages[0].run_id, "worker-a-run");
    assert_eq!(
        worker.messages[0]
            .metadata
            .as_ref()
            .and_then(|metadata| metadata.get("tool_call_id"))
            .and_then(serde_json::Value::as_str),
        Some("call-1")
    );
}

#[tokio::test]
async fn chat_pending_history_does_not_hide_provider_fallback_history() {
    let (mut service, _repo, _sessions, fallback, _session_id) = service_fixture(
        GroupStrategy::Chat,
        0,
        u64::MAX,
        vec![fallback_message("provider durable")],
    )
    .await;
    service.pending_messages = Arc::new(StaticPendingMessages(vec![PendingGroupMessage {
        run_id: "active-run".to_string(),
        bot_id: "worker-a".to_string(),
        session_id: None,
        created_at_ms: 2,
        kind: PendingGroupMessageKind::Chat {
            text: "tracker pending".to_string(),
        },
    }]));

    let durable_only = service
        .get_history(group_cmd("group-1", Some("worker-a")))
        .await
        .expect("default durable history");
    assert_eq!(durable_only.messages.len(), 1);
    assert_eq!(durable_only.messages[0].content, "provider durable");

    let result = service
        .get_history_with_options(
            group_cmd("group-1", Some("worker-a")),
            MessageHistoryOptions {
                include_pending: true,
            },
        )
        .await
        .expect("provider and pending history");

    assert_eq!(fallback.group_calls().await, 2);
    let contents = result
        .messages
        .iter()
        .map(|message| message.content.as_str())
        .collect::<Vec<_>>();
    assert_eq!(contents, vec!["tracker pending", "provider durable"]);
}

#[tokio::test]
async fn chat_history_uses_chat_cutoff_and_keeps_owner_filter_disabled() {
    let (service, repo, _sessions, fallback, session_id) =
        service_fixture(GroupStrategy::Chat, 0, u64::MAX, Vec::new()).await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "bot-a",
        "visible",
        Some("worker-a"),
    )
    .await;

    let result = service
        .get_session_history(session_cmd("group-1", &session_id, Some("worker-a")))
        .await
        .expect("chat history");

    assert_eq!(fallback.session_calls().await, 0);
    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].content, "visible");
}

#[tokio::test]
async fn public_state_machine_panel_round_trips_for_participant_chat_history() {
    let (service, repo, sessions, fallback, session_id) =
        service_fixture(GroupStrategy::Chat, 0, u64::MAX, Vec::new()).await;
    let mut human = Participant::human("human-1", ParticipantRole::Observer);
    human.message_view_scope = MessageViewScope::Participant;
    sessions
        .add_participant(&session_id, human)
        .await
        .expect("add participant Human");
    let run_id = "sm-run-1";
    let stable_message_id = format!("{run_id}:000-panel");
    let panel_content = "<AixUI type=\"panel\" component=\"bcsPanel.StateMachineRunView\" />";
    repo.append_message(NewMessage {
        group_id: "group-1".to_string(),
        session_id: session_id.clone(),
        sender_id: bcs_domain::BCS_STATE_MACHINE_MESSAGE_SENDER.to_string(),
        sender_type: SenderType::Bot,
        message_type: STATE_MACHINE_PANEL_MESSAGE_TYPE.to_string(),
        content: serde_json::json!({
            "text": panel_content,
            "bot_name": BCS_STATE_MACHINE_MESSAGE_SENDER_NAME,
            "metadata": {
                "state_machine": {
                    "event": "panel",
                    "run_id": run_id,
                    "component": "bcsPanel.StateMachineRunView",
                }
            }
        }),
        client_msg_id: Some(stable_message_id.clone()),
        created_at: 2,
        run_id: run_id.to_string(),
        owner_bot_id: None,
        visibility_domain: bcs_domain::MessageVisibilityDomain::StateMachine,
        audience: Some(bcs_domain::MessageAudience::Public),
    })
    .await
    .expect("append state-machine panel");

    let result = service
        .get_session_history(session_cmd("group-1", &session_id, Some("human-1")))
        .await
        .expect("state-machine panel history");

    assert_eq!(fallback.session_calls().await, 0);
    assert_eq!(result.messages.len(), 1);
    let panel = &result.messages[0];
    assert_eq!(panel.id, stable_message_id);
    assert_eq!(panel.content, panel_content);
    assert_eq!(
        panel.bot_name.as_deref(),
        Some(BCS_STATE_MACHINE_MESSAGE_SENDER_NAME)
    );
    assert_eq!(panel.role, MessageRole::Assistant);
    assert!(panel.run_id.is_empty());
    assert_eq!(panel.metadata.as_ref().unwrap()["state_machine"]["run_id"], run_id);
    assert_eq!(
        panel
            .metadata
            .as_ref()
            .and_then(|metadata| metadata["state_machine"]["event"].as_str()),
        Some("panel")
    );
}

#[tokio::test]
async fn directed_human_input_prompt_round_trips_through_manager_worker_history() {
    let (service, repo, sessions, fallback, session_id) =
        service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
    let mut human = Participant::human("human_1001", ParticipantRole::Observer);
    human.message_view_scope = MessageViewScope::Participant;
    sessions
        .add_participant(&session_id, human)
        .await
        .expect("add assigned Human participant");
    let stable_message_id = "sm-run-1:review:1:human-input-prompt";
    let prompt = "回复\"确认\"表示方向通过。";
    repo.append_message(NewMessage {
        group_id: "group-1".to_string(),
        session_id: session_id.clone(),
        sender_id: bcs_domain::BCS_STATE_MACHINE_MESSAGE_SENDER.to_string(),
        sender_type: SenderType::Bot,
        message_type: STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE.to_string(),
        content: serde_json::json!({
            "text": prompt,
            "bot_name": BCS_STATE_MACHINE_MESSAGE_SENDER_NAME,
            "metadata": {
                "state_machine": {
                    "event": "human_input_prompt",
                    "run_id": "sm-run-1",
                    "node_id": "review",
                }
            }
        }),
        client_msg_id: Some(stable_message_id.to_string()),
        created_at: 2,
        run_id: "sm-run-1".to_string(),
        owner_bot_id: None,
        visibility_domain: bcs_domain::MessageVisibilityDomain::StateMachine,
        audience: Some(bcs_domain::MessageAudience::Directed {
            actor_ids: vec!["human_1001".to_string()],
        }),
    })
    .await
    .expect("append directed HumanInput prompt");

    let result = service
        .get_session_history(session_cmd("group-1", &session_id, Some("human_1001")))
        .await
        .expect("assigned Human history");

    assert_eq!(fallback.session_calls().await, 0);
    assert_eq!(result.messages.len(), 1);
    let message = &result.messages[0];
    assert_eq!(message.id, stable_message_id);
    assert_eq!(message.content, prompt);
    assert_eq!(
        message.bot_name.as_deref(),
        Some(BCS_STATE_MACHINE_MESSAGE_SENDER_NAME)
    );
    assert_eq!(message.role, MessageRole::Assistant);
    assert_eq!(
        message
            .metadata
            .as_ref()
            .and_then(|metadata| metadata["state_machine"]["event"].as_str()),
        Some("human_input_prompt")
    );
}

#[tokio::test]
async fn directed_human_input_response_round_trips_as_user_message() {
    let (service, repo, sessions, fallback, session_id) =
        service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
    let mut human = Participant::human("human_1001", ParticipantRole::Observer);
    human.message_view_scope = MessageViewScope::Participant;
    sessions
        .add_participant(&session_id, human)
        .await
        .expect("add responding Human participant");
    let stable_message_id = "sm-run-1:review:0:1-output";
    repo.append_message(NewMessage {
        group_id: "group-1".to_string(),
        session_id: session_id.clone(),
        sender_id: "human_1001".to_string(),
        sender_type: SenderType::Human,
        message_type: STATE_MACHINE_HUMAN_INPUT_RESPONSE_MESSAGE_TYPE.to_string(),
        content: serde_json::json!({
            "text": "通过",
            "metadata": {
                "state_machine": {
                    "event": "output",
                    "run_id": "sm-run-1",
                    "node_id": "review",
                }
            }
        }),
        client_msg_id: Some(stable_message_id.to_string()),
        created_at: 3,
        run_id: "sm-run-1".to_string(),
        owner_bot_id: None,
        visibility_domain: bcs_domain::MessageVisibilityDomain::StateMachine,
        audience: Some(bcs_domain::MessageAudience::Directed {
            actor_ids: vec!["human_1001".to_string()],
        }),
    })
    .await
    .expect("append directed HumanInput response");

    let result = service
        .get_session_history(session_cmd("group-1", &session_id, Some("human_1001")))
        .await
        .expect("responding Human history");

    assert_eq!(fallback.session_calls().await, 0);
    assert_eq!(result.messages.len(), 1);
    let message = &result.messages[0];
    assert_eq!(message.id, stable_message_id);
    assert_eq!(message.content, "通过");
    assert_eq!(message.sender, "human_1001");
    assert_eq!(message.role, MessageRole::User);
    assert_eq!(
        message
            .metadata
            .as_ref()
            .and_then(|metadata| metadata["state_machine"]["event"].as_str()),
        Some("output")
    );
}

#[tokio::test]
async fn chat_error_history_is_visible_to_humans_but_not_bot_context() {
    let (service, repo, _sessions, fallback, session_id) =
        service_fixture(GroupStrategy::Chat, 0, u64::MAX, Vec::new()).await;
    repo.append_message(NewMessage {
        group_id: "group-1".into(), session_id: session_id.clone(), sender_id: "worker-a".into(),
        sender_type: SenderType::Bot, message_type: bcs_domain::CHAT_ERROR_MESSAGE_TYPE.into(),
        content: serde_json::json!("请求超时"), client_msg_id: Some("chat-error:run".into()),
        created_at: 1, run_id: "run".into(), owner_bot_id: None,
        visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
        audience: None,
    }).await.unwrap();
    let human = SessionHistoryCommand {
        caller: CallerContext::Human(HumanActor { actor_id: "human-1".into(), staff_no: "human-1".into() }),
        group_id: "group-1".into(), session_id: session_id.clone(), session_participants: Vec::new(),
        view_bot_id: None, limit: 50, before: None,
    };
    let result = service.get_session_history(human).await.unwrap();
    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].metadata.as_ref().unwrap()["terminal_state"], "error");
    let result = service.get_session_history(SessionHistoryCommand {
        caller: CallerContext::Bot(BotActor { bot_uuid: "worker-a".into() }),
        group_id: "group-1".into(), session_id, session_participants: Vec::new(),
        view_bot_id: Some("worker-a".into()), limit: 50, before: None,
    }).await.unwrap();
    assert!(result.messages.is_empty());
    assert_eq!(fallback.session_calls().await, 0);
}

#[tokio::test]
async fn session_opening_message_is_visible_to_humans_and_hidden_from_bots() {
    let (service, repo, _sessions, fallback, session_id) =
        service_fixture(GroupStrategy::Chat, 0, u64::MAX, Vec::new()).await;
    let stable_message_id = format!("{session_id}:000-opening");
    repo.append_message(NewMessage {
        group_id: "group-1".to_string(),
        session_id: session_id.clone(),
        sender_id: bcs_domain::BCS_SESSION_OPENING_MESSAGE_SENDER.to_string(),
        sender_type: SenderType::Bot,
        message_type: SESSION_OPENING_MESSAGE_TYPE.to_string(),
        content: serde_json::json!({
            "text": "欢迎来到研发群",
            "bot_name": BCS_SESSION_OPENING_MESSAGE_SENDER_NAME,
            "metadata": {
                "opening_message": {
                    "scope": "session",
                    "strategy": "chat",
                }
            }
        }),
        client_msg_id: Some(stable_message_id.clone()),
        created_at: 1,
        run_id: format!("{session_id}:opening"),
        owner_bot_id: None,
        visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
        audience: Some(bcs_domain::MessageAudience::Public),
    })
    .await
    .expect("append opening message");

    let human_result = service
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human-1".to_string(),
                staff_no: "human-1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: session_id.clone(),
            session_participants: Vec::new(),
            view_bot_id: None,
            limit: 50,
            before: None,
        })
        .await
        .expect("human history");
    assert_eq!(human_result.messages.len(), 1);
    assert_eq!(human_result.messages[0].id, stable_message_id);
    assert_eq!(human_result.messages[0].content, "欢迎来到研发群");
    assert_eq!(
        human_result.messages[0].bot_name.as_deref(),
        Some(BCS_SESSION_OPENING_MESSAGE_SENDER_NAME)
    );

    let bot_result = service
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "worker-a".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id,
            session_participants: Vec::new(),
            view_bot_id: Some("worker-a".to_string()),
            limit: 50,
            before: None,
        })
        .await
        .expect("bot history");
    assert!(bot_result.messages.is_empty());
    assert_eq!(fallback.session_calls().await, 0);
}

#[tokio::test]
async fn chat_history_without_join_anchor_compensates_new_state_machine_projections() {
    let (mut service, repo, _sessions, fallback, session_id) =
        service_fixture(GroupStrategy::Chat, 0, 0, Vec::new()).await;
    service.new_participant_visible_limit = 5;
    for n in 1..=10 {
        append_history(&repo, "group-1", &session_id, "mgr", &format!("ordinary-{n}"), None).await;
    }
    for n in 0..25 {
        repo.append_message_with_id(format!("history-{n}"), NewMessage {
            group_id: "group-1".into(), session_id: session_id.clone(), sender_id: "mgr".into(),
            sender_type: SenderType::Bot, message_type: "state_machine_output".into(),
            content: serde_json::json!({"text":"accepted output","metadata":{"state_machine":{
                "history_schema_version":1,"run_id":"run","node_id":format!("node-{n}"),"attempt":0,"event":"output"
            }}}), client_msg_id: None, owner_bot_id: None,
            visibility_domain: bcs_domain::MessageVisibilityDomain::StateMachine,
            audience: Some(bcs_domain::MessageAudience::FullOnly), created_at: 2, run_id: "run".into(),
        }).await.unwrap();
    }
    for persisted in [false, true] {
        service.persisted_state_machine_history = persisted;
        let result = service.get_session_history(session_cmd("group-1", &session_id, Some("worker-a"))).await.unwrap();
        let ordinary = result.messages.iter().filter(|m| m.content.starts_with("ordinary-"))
            .map(|m| m.content.as_str()).collect::<Vec<_>>();
        assert_eq!(ordinary, ["ordinary-10", "ordinary-9", "ordinary-8", "ordinary-7", "ordinary-6"]);
    }
    assert_eq!(fallback.session_calls().await, 0);
}
