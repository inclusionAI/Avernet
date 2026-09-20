use super::*;

#[tokio::test]
async fn sender_identity_forwarding_does_not_change_new_command_routing() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    let mut binding = active_binding(
        "binding_bot_identity",
        "robot_1",
        BindingTarget::Bot {
            bot_id: "target_bot".to_string(),
        },
        Visibility::FullTranscript,
    );
    binding.config[FORWARD_SENDER_IDENTITY_CONFIG] = serde_json::json!(true);
    harness.binding_repo.create(binding).await?;

    let mut command = new_command("conv_1", "410025", "msg_new");
    command.text = "  /new  ".to_string();
    harness.service.handle_inbound(command).await?;
    assert!(harness.message_flow.web_sends.lock().await.is_empty());

    let mut ordinary = new_command("conv_1", "410025", "msg_new_foo");
    ordinary.text = "/new foo".to_string();
    harness.service.handle_inbound(ordinary).await?;
    let sends = harness.message_flow.web_sends.lock().await;
    assert_eq!(sends.len(), 1);
    assert_eq!(sends[0].message, "/new foo");
    assert_eq!(
        sends[0]
            .channel_sender_identity
            .as_ref()
            .map(|identity| identity.user_id.as_str()),
        Some("410025")
    );
    Ok(())
}

#[tokio::test]
async fn new_command_without_session_replies_nothing_to_reset() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    create_group_binding(&harness, "group_1", GroupChatScope::ConversationShared).await?;

    harness
        .service
        .handle_inbound(new_command("conv_1", "u1", "msg_new"))
        .await?;

    let events = harness.delivery.events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(
        events[0].text.as_deref(),
        Some(crate::commands::NOTHING_TO_RESET_TEXT)
    );
    assert_eq!(events[0].kind, ChannelOutboundEventKind::System);
    assert_eq!(events[0].source_im_message_id.as_deref(), Some("msg_new"));
    assert!(events[0].bcs_session_id.is_empty());
    assert!(harness.message_flow.web_sends.lock().await.is_empty());
    assert!(harness.session_repo.sessions.lock().await.is_empty());
    assert!(
        harness
            .conversation_repo
            .get(
                "generated_id",
                "conv_1",
                SessionScope::Conversation,
                Some("u1"),
            )
            .await?
            .is_none()
    );
    Ok(())
}

#[tokio::test]
async fn new_command_completes_idle_session_and_next_message_rolls_over() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    create_group_binding(&harness, "group_1", GroupChatScope::ConversationShared).await?;
    harness
        .service
        .handle_inbound(inbound("conv_1", "u1", Some("张三"), "msg_1"))
        .await?;
    let mapping = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_1",
            SessionScope::Conversation,
            Some("u1"),
        )
        .await?
        .expect("mapping after first message");
    let old_session_id = mapping.bcs_session_id.clone();

    harness
        .service
        .handle_inbound(new_command("conv_1", "u1", "msg_new"))
        .await?;

    let old_session = harness
        .session_repo
        .get(&old_session_id)
        .await
        .expect("old session");
    assert_eq!(old_session.status, SessionStatus::Completed);
    assert!(
        old_session
            .output
            .as_ref()
            .is_some_and(|output| output.to_string().contains("channel_command_new"))
    );
    // 映射原地保留，作为下一条消息 rollover 的触发器。
    let mapping_after = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_1",
            SessionScope::Conversation,
            Some("u1"),
        )
        .await?
        .expect("mapping kept");
    assert_eq!(mapping_after.bcs_session_id, old_session_id);
    assert_eq!(
        delivered_texts(&harness).await,
        vec![crate::commands::RESET_DONE_TEXT.to_string()]
    );
    assert_eq!(harness.message_flow.web_sends.lock().await.len(), 1);

    // 相同 msg_id 的 webhook 重放被 dedup，不重复回复。
    harness
        .service
        .handle_inbound(new_command("conv_1", "u1", "msg_new"))
        .await?;
    assert_eq!(delivered_texts(&harness).await.len(), 1);

    // 下一条普通消息恰好创建一个新会话并重指映射。
    harness
        .service
        .handle_inbound(inbound("conv_1", "u1", Some("张三"), "msg_2"))
        .await?;
    let mapping_new = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_1",
            SessionScope::Conversation,
            Some("u1"),
        )
        .await?
        .expect("mapping rolled over");
    assert_ne!(mapping_new.bcs_session_id, old_session_id);
    // Historical lookup survives /new without changing the current mapping.
    let historical = harness.service.list_conversations_by_session(
        &old_session_id, Some("dingtalk".to_string())
    ).await?;
    assert_eq!(historical.len(), 1);
    assert_eq!(historical[0].im_conversation_id, "conv_1");
    assert_eq!(historical[0].bcs_session_id, old_session_id);
    assert!(harness.conversation_repo.list_by_bcs_session(&old_session_id).await?.is_empty());
    let current = harness.service.list_conversations_by_session(
        &mapping_new.bcs_session_id, Some("dingtalk".to_string())
    ).await?;
    assert_eq!(current.len(), 1);
    assert_eq!(current[0].bcs_session_id, mapping_new.bcs_session_id);
    let new_session = harness
        .session_repo
        .get(&mapping_new.bcs_session_id)
        .await
        .expect("new session");
    assert_eq!(new_session.status, SessionStatus::Running);
    assert_eq!(harness.session_repo.sessions.lock().await.len(), 2);
    let web_sends = harness.message_flow.web_sends.lock().await;
    assert_eq!(web_sends.len(), 2);
    assert_eq!(
        web_sends[1].session_id.as_deref(),
        Some(mapping_new.bcs_session_id.as_str())
    );
    Ok(())
}

#[tokio::test]
async fn new_command_queues_behind_active_run_and_executes_on_chat_final() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    create_group_binding(&harness, "group_1", GroupChatScope::ConversationShared).await?;
    *harness.message_flow.active_run_ids.lock().await = vec!["run_1".to_string()];
    harness
        .service
        .handle_inbound(inbound("conv_1", "u1", Some("张三"), "msg_1"))
        .await?;
    let old_session_id = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_1",
            SessionScope::Conversation,
            Some("u1"),
        )
        .await?
        .expect("mapping")
        .bcs_session_id;

    harness
        .service
        .handle_inbound(new_command("conv_1", "u1", "msg_new"))
        .await?;

    assert_eq!(
        harness
            .session_repo
            .get(&old_session_id)
            .await
            .expect("session")
            .status,
        SessionStatus::Running
    );
    assert_eq!(
        delivered_texts(&harness).await,
        vec![crate::commands::RESET_QUEUED_TEXT.to_string()]
    );

    // run 终态（ChatFinal）经过 try_outbound → 自动执行排队的重置。
    harness
        .service
        .try_outbound(outbound(&old_session_id, ParticipantRole::Worker, false))
        .await?;

    assert_eq!(
        harness
            .session_repo
            .get(&old_session_id)
            .await
            .expect("session")
            .status,
        SessionStatus::Completed
    );
    let events = harness.delivery.events.lock().await;
    let done = events
        .iter()
        .find(|event| event.text.as_deref() == Some(crate::commands::RESET_DONE_TEXT))
        .expect("deferred reset confirmation");
    assert_eq!(done.source_im_message_id.as_deref(), Some("msg_new"));
    assert_eq!(done.bcs_session_id, old_session_id);
    Ok(())
}

#[tokio::test]
async fn new_command_queued_reset_executes_on_error_terminal() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    create_group_binding(&harness, "group_1", GroupChatScope::ConversationShared).await?;
    *harness.message_flow.active_run_ids.lock().await = vec!["run_1".to_string()];
    harness
        .service
        .handle_inbound(inbound("conv_1", "u1", Some("张三"), "msg_1"))
        .await?;
    let old_session_id = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_1",
            SessionScope::Conversation,
            Some("u1"),
        )
        .await?
        .expect("mapping")
        .bcs_session_id;
    harness
        .service
        .handle_inbound(new_command("conv_1", "u1", "msg_new"))
        .await?;

    let mut terminal = outbound(&old_session_id, ParticipantRole::Worker, false);
    terminal.kind = ChannelOutboundEventKind::System;
    terminal.raw_payload = serde_json::json!({"state": "error"});
    terminal.text = Some("机器人连接或执行失败，请稍后重试。".to_string());
    harness.service.try_outbound(terminal).await?;

    assert_eq!(
        harness
            .session_repo
            .get(&old_session_id)
            .await
            .expect("session")
            .status,
        SessionStatus::Completed
    );
    assert!(
        delivered_texts(&harness)
            .await
            .contains(&crate::commands::RESET_DONE_TEXT.to_string())
    );
    Ok(())
}

#[tokio::test]
async fn new_command_per_sender_group_resets_only_sender_session() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    create_group_binding(&harness, "group_1", GroupChatScope::PerSender).await?;
    harness
        .service
        .handle_inbound(group_inbound("conv_1", "u1", Some("张三"), "msg_1", true))
        .await?;
    harness
        .service
        .handle_inbound(group_inbound("conv_1", "u2", Some("李四"), "msg_2", true))
        .await?;
    let session_u1 = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_1",
            SessionScope::PerSender,
            Some("u1"),
        )
        .await?
        .expect("u1 mapping")
        .bcs_session_id;
    let session_u2 = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_1",
            SessionScope::PerSender,
            Some("u2"),
        )
        .await?
        .expect("u2 mapping")
        .bcs_session_id;
    assert_ne!(session_u1, session_u2);

    harness
        .service
        .handle_inbound(group_new_command("conv_1", "u1", "msg_new"))
        .await?;

    assert_eq!(
        harness
            .session_repo
            .get(&session_u1)
            .await
            .expect("u1 session")
            .status,
        SessionStatus::Completed
    );
    assert_eq!(
        harness
            .session_repo
            .get(&session_u2)
            .await
            .expect("u2 session")
            .status,
        SessionStatus::Running
    );
    let events = harness.delivery.events.lock().await;
    let done = events
        .iter()
        .find(|event| event.text.as_deref() == Some(crate::commands::RESET_DONE_TEXT))
        .expect("reset confirmation");
    assert_eq!(done.im_user_id.as_deref(), Some("u1"));
    Ok(())
}

#[tokio::test]
async fn new_command_shared_group_resets_shared_session() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    create_group_binding(&harness, "group_1", GroupChatScope::ConversationShared).await?;
    harness
        .service
        .handle_inbound(group_inbound("conv_1", "u1", Some("张三"), "msg_1", true))
        .await?;
    harness
        .service
        .handle_inbound(group_inbound("conv_1", "u2", Some("李四"), "msg_2", true))
        .await?;
    let shared = harness
        .conversation_repo
        .get("generated_id", "conv_1", SessionScope::Conversation, None)
        .await?
        .expect("shared mapping")
        .bcs_session_id;

    harness
        .service
        .handle_inbound(group_new_command("conv_1", "u2", "msg_new"))
        .await?;

    assert_eq!(
        harness
            .session_repo
            .get(&shared)
            .await
            .expect("shared session")
            .status,
        SessionStatus::Completed
    );
    let events = harness.delivery.events.lock().await;
    let done = events
        .iter()
        .find(|event| event.text.as_deref() == Some(crate::commands::RESET_DONE_TEXT))
        .expect("reset confirmation");
    assert_eq!(done.im_user_id, None);
    Ok(())
}

#[tokio::test]
async fn new_command_state_machine_idle_resets_immediately() -> TestResult {
    let clock = Arc::new(AtomicU64::new(100_000));
    let harness = TestHarness::new_with_clock(state_machine_group("group_sm"), clock).await?;
    create_group_binding(&harness, "group_sm", GroupChatScope::ConversationShared).await?;
    let session = harness
        .session_repo
        .create("group_sm", NewSessionParams::default())
        .await?;
    harness
        .conversation_repo
        .upsert(bcs_domain::ConversationSessionMap {
            binding_id: "generated_id".to_string(),
            im_conversation_id: "conv_sm".to_string(),
            im_conversation_type: "2".to_string(),
            session_scope: SessionScope::Conversation,
            im_user_id: None,
            bcs_session_id: session.id.clone(),
            last_active_at: 0,
        })
        .await?;

    harness
        .service
        .handle_inbound(group_new_command("conv_sm", "u1", "msg_new"))
        .await?;

    assert_eq!(
        harness
            .session_repo
            .get(&session.id)
            .await
            .expect("session")
            .status,
        SessionStatus::Completed
    );
    assert!(
        delivered_texts(&harness)
            .await
            .contains(&crate::commands::RESET_DONE_TEXT.to_string())
    );
    assert!(harness.collaboration_runtime.starts.lock().await.is_empty());
    Ok(())
}

#[tokio::test]
async fn new_command_state_machine_running_run_queues_then_terminal_executes() -> TestResult {
    let harness = TestHarness::new(state_machine_group("group_sm")).await?;
    create_group_binding(&harness, "group_sm", GroupChatScope::ConversationShared).await?;
    harness
        .service
        .handle_inbound(group_inbound(
            "conv_sm",
            "u1",
            Some("张三"),
            "msg_start",
            true,
        ))
        .await?;
    let session_id = harness.collaboration_runtime.starts.lock().await[0]
        .session_id
        .clone()
        .expect("state-machine session");

    harness
        .service
        .handle_inbound(group_new_command("conv_sm", "u1", "msg_new"))
        .await?;

    assert_eq!(
        harness
            .session_repo
            .get(&session_id)
            .await
            .expect("session")
            .status,
        SessionStatus::Running
    );
    assert!(
        delivered_texts(&harness)
            .await
            .contains(&crate::commands::RESET_QUEUED_TEXT.to_string())
    );

    SessionChannelOutboundPort::publish_state_machine_terminal(
        &harness.service,
        bcs_service_api::StateMachineTerminalEvent {
            group_id: "group_sm".to_string(),
            session_id: session_id.clone(),
            run_id: "state_run_1".to_string(),
            workflow_name: "wf".to_string(),
            status: bcs_service_api::StateMachineTerminalStatus::Completed,
            output: None,
        },
    )
    .await?;

    assert_eq!(
        harness
            .session_repo
            .get(&session_id)
            .await
            .expect("session")
            .status,
        SessionStatus::Completed
    );
    let events = harness.delivery.events.lock().await;
    let done = events
        .iter()
        .find(|event| event.text.as_deref() == Some(crate::commands::RESET_DONE_TEXT))
        .expect("deferred reset confirmation");
    assert_eq!(done.source_im_message_id.as_deref(), Some("msg_new"));
    Ok(())
}

#[tokio::test]
async fn new_command_state_machine_starting_window_replies_wait() -> TestResult {
    let harness = TestHarness::new(state_machine_group("group_sm")).await?;
    create_group_binding(&harness, "group_sm", GroupChatScope::ConversationShared).await?;
    let session = harness
        .session_repo
        .create("group_sm", NewSessionParams::default())
        .await?;
    harness
        .conversation_repo
        .upsert(bcs_domain::ConversationSessionMap {
            binding_id: "generated_id".to_string(),
            im_conversation_id: "conv_sm".to_string(),
            im_conversation_type: "2".to_string(),
            session_scope: SessionScope::Conversation,
            im_user_id: None,
            bcs_session_id: session.id.clone(),
            last_active_at: 42,
        })
        .await?;

    harness
        .service
        .handle_inbound(group_new_command("conv_sm", "u1", "msg_new"))
        .await?;

    assert_eq!(
        harness
            .session_repo
            .get(&session.id)
            .await
            .expect("session")
            .status,
        SessionStatus::Running
    );
    let texts = delivered_texts(&harness).await;
    assert_eq!(texts.len(), 1);
    assert!(texts[0].contains("流程正在启动"));
    Ok(())
}

#[tokio::test]
async fn new_command_is_not_consumed_by_active_human_input() -> TestResult {
    let harness = TestHarness::new(state_machine_group("group_sm")).await?;
    create_group_binding(&harness, "group_sm", GroupChatScope::ConversationShared).await?;
    harness
        .service
        .handle_inbound(group_inbound(
            "conv_sm",
            "u1",
            Some("张三"),
            "msg_start",
            true,
        ))
        .await?;
    let session_id = harness.collaboration_runtime.starts.lock().await[0]
        .session_id
        .clone()
        .expect("state-machine session");
    SessionChannelOutboundPort::publish_human_input_ready(
        &harness.service,
        HumanInputReadyEvent {
            event_id: "human-ready-state-run-1-human-review".to_string(),
            group_id: "group_sm".to_string(),
            session_id,
            run_id: "state_run_1".to_string(),
            node_id: "human_review".to_string(),
            display_name: "Human review".to_string(),
            instruction: "Review the draft".to_string(),
            assignee_actor_id: "human_u1".to_string(),
            channel_type: channel_type(),
            notification_mode: HumanInputNotificationMode::FixedGroup,
            fixed_group_conversation_id: Some("conv_sm".to_string()),
            response_ref: "state_run_1:human_review".to_string(),
            judge_outcomes: vec!["approve".to_string(), "reject".to_string()],
            timeout_deadline_ms: Some(60_000),
            loop_context: None,
            upstream_artifacts: Vec::new(),
        },
    )
    .await?;

    harness
        .service
        .handle_inbound(group_new_command("conv_sm", "u1", "msg_new"))
        .await?;

    // /new 不得被 HumanInput 卡片吞作回复；run 在途 → 重置排队。
    assert!(
        harness
            .collaboration_runtime
            .human_responses
            .lock()
            .await
            .is_empty()
    );
    assert!(
        delivered_texts(&harness)
            .await
            .contains(&crate::commands::RESET_QUEUED_TEXT.to_string())
    );
    Ok(())
}

#[tokio::test]
async fn concurrent_new_commands_complete_session_once() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    create_group_binding(&harness, "group_1", GroupChatScope::ConversationShared).await?;
    harness
        .service
        .handle_inbound(inbound("conv_1", "u1", Some("张三"), "msg_1"))
        .await?;
    let old_session_id = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_1",
            SessionScope::Conversation,
            Some("u1"),
        )
        .await?
        .expect("mapping")
        .bcs_session_id;

    let (first, second) = tokio::join!(
        harness
            .service
            .handle_inbound(new_command("conv_1", "u1", "msg_new_a")),
        harness
            .service
            .handle_inbound(new_command("conv_1", "u1", "msg_new_b")),
    );
    first?;
    second?;

    assert_eq!(
        harness
            .session_repo
            .get(&old_session_id)
            .await
            .expect("session")
            .status,
        SessionStatus::Completed
    );
    assert_eq!(harness.session_repo.sessions.lock().await.len(), 1);
    // 竞态下回复组合不确定（赢家 DONE；输家看到 Completed 会答 NOTHING_TO_RESET），
    // 不变量：恰好各答一条、至少一条 DONE、只归档一次。
    let texts = delivered_texts(&harness).await;
    assert_eq!(texts.len(), 2);
    assert!(
        texts
            .iter()
            .all(|text| text.as_str() == crate::commands::RESET_DONE_TEXT
                || text.as_str() == crate::commands::NOTHING_TO_RESET_TEXT)
    );
    assert!(
        texts
            .iter()
            .any(|text| text.as_str() == crate::commands::RESET_DONE_TEXT)
    );

    // 归档后并发普通消息：恰好创建一个新会话。
    let (msg_a, msg_b) = tokio::join!(
        harness
            .service
            .handle_inbound(inbound("conv_1", "u1", Some("张三"), "msg_a")),
        harness
            .service
            .handle_inbound(inbound("conv_1", "u1", Some("张三"), "msg_b")),
    );
    msg_a?;
    msg_b?;
    assert_eq!(harness.session_repo.sessions.lock().await.len(), 2);
    let web_sends = harness.message_flow.web_sends.lock().await;
    assert_eq!(web_sends.len(), 3);
    assert_eq!(web_sends[1].session_id, web_sends[2].session_id);
    Ok(())
}

#[tokio::test]
async fn stale_pending_reset_executes_on_next_inbound() -> TestResult {
    let clock = Arc::new(AtomicU64::new(42));
    let harness = TestHarness::new_with_clock(manager_group("group_1"), clock.clone()).await?;
    create_group_binding(&harness, "group_1", GroupChatScope::ConversationShared).await?;
    *harness.message_flow.active_run_ids.lock().await = vec!["run_1".to_string()];
    harness
        .service
        .handle_inbound(inbound("conv_1", "u1", Some("张三"), "msg_1"))
        .await?;
    let old_session_id = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_1",
            SessionScope::Conversation,
            Some("u1"),
        )
        .await?
        .expect("mapping")
        .bcs_session_id;
    harness
        .service
        .handle_inbound(new_command("conv_1", "u1", "msg_new"))
        .await?;
    assert_eq!(
        harness
            .session_repo
            .get(&old_session_id)
            .await
            .expect("session")
            .status,
        SessionStatus::Running
    );

    // bot 假死、终态事件不到达：超过兜底阈值后由下一条消息顺带执行。
    clock.store(
        42 + crate::commands::PENDING_RESET_STALE_MS + 1,
        Ordering::SeqCst,
    );
    harness
        .service
        .handle_inbound(inbound("conv_1", "u1", Some("张三"), "msg_2"))
        .await?;

    assert_eq!(
        harness
            .session_repo
            .get(&old_session_id)
            .await
            .expect("old session")
            .status,
        SessionStatus::Completed
    );
    assert!(
        delivered_texts(&harness)
            .await
            .contains(&crate::commands::RESET_DONE_TEXT.to_string())
    );
    // 该消息进入 rollover 后的新会话。
    let mapping = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_1",
            SessionScope::Conversation,
            Some("u1"),
        )
        .await?
        .expect("mapping rolled over");
    assert_ne!(mapping.bcs_session_id, old_session_id);
    let web_sends = harness.message_flow.web_sends.lock().await;
    assert_eq!(web_sends.len(), 2);
    assert_eq!(
        web_sends[1].session_id.as_deref(),
        Some(mapping.bcs_session_id.as_str())
    );
    Ok(())
}
