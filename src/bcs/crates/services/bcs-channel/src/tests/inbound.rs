use super::*;

#[tokio::test]
async fn inbound_manager_worker_materializes_human_and_isolates_dm_conversations() -> TestResult
{
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;

    harness
        .service
        .handle_inbound(inbound("conv_a", "u1", Some("张三"), "msg_a"))
        .await?;
    harness
        .service
        .handle_inbound(inbound("conv_b", "u2", None, "msg_b"))
        .await?;

    let ensured = harness.registry.ensured.lock().await.clone();
    assert_eq!(
        ensured,
        vec![
            ("u1".to_string(), "张三".to_string()),
            ("u2".to_string(), "u2".to_string()),
        ]
    );

    let u1 = harness
        .participant_repo
        .get(channel_type(), "robot_1", "u1")
        .await?
        .ok_or_else(|| ServiceError::InternalError("missing u1 participant".to_string()))?;
    assert_eq!(u1.actor_id, "human_u1");
    assert_eq!(u1.display_name.as_deref(), Some("张三"));

    let conv_a = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_a",
            SessionScope::Conversation,
            Some("u1"),
        )
        .await?
        .ok_or_else(|| ServiceError::InternalError("missing conv_a".to_string()))?;
    let conv_b = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_b",
            SessionScope::Conversation,
            Some("u2"),
        )
        .await?
        .ok_or_else(|| ServiceError::InternalError("missing conv_b".to_string()))?;
    assert_ne!(conv_a.bcs_session_id, conv_b.bcs_session_id);
    assert!(
        conv_a
            .bcs_session_id
            .starts_with("group_1:channel_dingtalk_")
    );
    assert!(
        conv_b
            .bcs_session_id
            .starts_with("group_1:channel_dingtalk_")
    );

    let web_sends = harness.message_flow.web_sends.lock().await;
    assert_eq!(web_sends.len(), 2);
    assert_eq!(web_sends[0].from_actor_id, "human_u1");
    assert_eq!(
        web_sends[0].session_id.as_deref(),
        Some(conv_a.bcs_session_id.as_str())
    );
    assert_eq!(web_sends[0].idempotency_key.as_deref(), Some("msg_a"));
    assert_eq!(web_sends[0].source_im_message_id.as_deref(), Some("msg_a"));
    assert_eq!(web_sends[1].source_im_message_id.as_deref(), Some("msg_b"));

    let added = harness.session_repo.added_participants.lock().await;
    assert_eq!(added.len(), 2);
    assert_eq!(added[0].1.bot_uuid, "human_u1");
    assert_eq!(added[0].1.mode, Some(ParticipantMode::Present));

    Ok(())
}

#[tokio::test]
async fn inbound_free_chat_group_binding_dispatches_to_group_session() -> TestResult {
    let harness = TestHarness::new(chat_group("group_chat")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_chat".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;

    harness
        .service
        .handle_inbound(inbound("conv_chat", "u1", Some("张三"), "msg_chat"))
        .await?;

    let web_sends = harness.message_flow.web_sends.lock().await;
    assert_eq!(web_sends.len(), 1);
    assert_eq!(web_sends[0].group_id, "group_chat");
    assert_eq!(web_sends[0].from_actor_id, "human_u1");
    assert_eq!(web_sends[0].idempotency_key.as_deref(), Some("msg_chat"));

    Ok(())
}

#[tokio::test(flavor = "current_thread")]
async fn inbound_chat_logs_binding_actor_session_and_dispatch() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;

    let (result, logs) = capture_tracing_logs(async {
        harness
            .service
            .handle_inbound(inbound("conv_a", "u1", Some("张三"), "msg_a"))
            .await
    })
    .await;
    result?;

    for expected in [
        "channel inbound: received",
        "channel inbound: binding resolved",
        "channel inbound: actor resolved",
        "channel inbound: session resolved",
        "channel inbound: dispatched",
        "msg_id=msg_a",
        "binding_id=generated_id",
        "actor_id=human_u1",
        "bcs_session_id=group_1:channel_dingtalk_",
    ] {
        assert!(
            logs.contains(expected),
            "expected log fragment {expected:?}, got:\n{logs}"
        );
    }

    Ok(())
}

#[tokio::test]
async fn single_chat_outbound_preserves_im_user_for_delivery() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;
    harness
        .service
        .handle_inbound(inbound("conv_a", "u1", Some("张三"), "msg_a"))
        .await?;
    let conv = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_a",
            SessionScope::Conversation,
            Some("u1"),
        )
        .await?
        .ok_or_else(|| ServiceError::InternalError("missing conv_a".to_string()))?;

    harness
        .service
        .try_outbound(outbound(
            &conv.bcs_session_id,
            ParticipantRole::Worker,
            false,
        ))
        .await?;

    let events = harness.delivery.events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].im_conversation_id, "conv_a");
    assert_eq!(events[0].im_conversation_type, "1");
    assert_eq!(events[0].im_user_id.as_deref(), Some("u1"));
    assert_eq!(events[0].im_user_display_name.as_deref(), Some("张三"));

    Ok(())
}

#[tokio::test]
async fn direct_bot_single_chat_session_includes_target_bot_and_human() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Bot {
                bot_id: "target_bot".to_string(),
            },
            group_chat_scope: None,
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;

    harness
        .service
        .handle_inbound(inbound("conv_direct", "u1", Some("张三"), "msg_direct"))
        .await?;

    let conv = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_direct",
            SessionScope::Conversation,
            Some("u1"),
        )
        .await?
        .ok_or_else(|| {
            ServiceError::InternalError("missing direct conversation".to_string())
        })?;
    let session = harness
        .session_repo
        .get(&conv.bcs_session_id)
        .await
        .ok_or_else(|| ServiceError::InternalError("missing direct session".to_string()))?;

    assert_eq!(session.participants.len(), 2);
    assert!(session.participants.iter().any(|participant| {
        participant.bot_uuid == "target_bot"
            && participant.actor_kind == ActorKind::Bot
            && participant.mode == Some(ParticipantMode::Auto)
    }));
    assert!(session.participants.iter().any(|participant| {
        participant.bot_uuid == "human_u1"
            && participant.actor_kind == ActorKind::Human
            && participant.mode == Some(ParticipantMode::Present)
    }));

    Ok(())
}

#[tokio::test]
async fn inbound_ignores_unmentioned_group_messages_and_duplicate_msg_ids() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;

    harness
        .service
        .handle_inbound(group_inbound(
            "conv_group",
            "u1",
            Some("张三"),
            "msg_ignored",
            false,
        ))
        .await?;
    assert!(harness.registry.ensured.lock().await.is_empty());
    assert!(harness.message_flow.web_sends.lock().await.is_empty());

    let mut whitespace_group =
        group_inbound("conv_group", "u1", Some("张三"), "msg_whitespace", false);
    whitespace_group.conversation_type = " 2 ".to_string();
    harness.service.handle_inbound(whitespace_group).await?;
    assert!(harness.registry.ensured.lock().await.is_empty());
    assert!(harness.message_flow.web_sends.lock().await.is_empty());

    let error = harness
        .service
        .handle_inbound(group_inbound("conv_group", "u1", Some("张三"), " ", true))
        .await
        .expect_err("empty message id must be rejected");
    assert_inbound_error(
        error,
        ChannelInboundFailureKind::InvalidInbound,
        false,
        "msg_id",
    );
    assert!(harness.registry.ensured.lock().await.is_empty());
    assert!(harness.message_flow.web_sends.lock().await.is_empty());

    let first = group_inbound("conv_group", "u1", Some("张三"), "msg_once", true);
    harness.service.handle_inbound(first.clone()).await?;
    harness.service.handle_inbound(first).await?;

    assert_eq!(harness.registry.ensured.lock().await.len(), 1);
    assert_eq!(harness.message_flow.web_sends.lock().await.len(), 1);

    Ok(())
}

#[tokio::test]
async fn inbound_classifies_missing_binding_and_lookup_failure() -> TestResult {
    let missing_binding = inbound_service(
        Arc::new(MemoryChannelBindingRepo::new("pre")),
        Arc::new(MemoryImParticipantRepo::new()),
        Arc::new(RecordingSessionRepo::default()),
        Arc::new(RecordingMessageFlow::default()),
        Arc::new(RecordingRegistry::default()),
    )
    .await;

    let error = missing_binding
        .handle_inbound(inbound("conv_missing", "u1", Some("张三"), "msg_missing"))
        .await
        .expect_err("missing binding must be reported");
    assert_inbound_error(
        error,
        ChannelInboundFailureKind::BindingNotFound,
        false,
        "active binding",
    );

    let lookup_failure = inbound_service(
        Arc::new(FailingBindingLookupRepo),
        Arc::new(MemoryImParticipantRepo::new()),
        Arc::new(RecordingSessionRepo::default()),
        Arc::new(RecordingMessageFlow::default()),
        Arc::new(RecordingRegistry::default()),
    )
    .await;

    let error = lookup_failure
        .handle_inbound(inbound("conv_lookup", "u1", Some("张三"), "msg_lookup"))
        .await
        .expect_err("binding lookup failure must be reported");
    assert_inbound_error(
        error,
        ChannelInboundFailureKind::BindingLookupFailed,
        true,
        "binding lookup failed",
    );

    Ok(())
}

#[tokio::test]
async fn inbound_classifies_invalid_input_and_context_resolution_failure() -> TestResult {
    let invalid_input = inbound_service(
        Arc::new(MemoryChannelBindingRepo::new("pre")),
        Arc::new(MemoryImParticipantRepo::new()),
        Arc::new(RecordingSessionRepo::default()),
        Arc::new(RecordingMessageFlow::default()),
        Arc::new(RecordingRegistry::default()),
    )
    .await;
    let mut invalid_message = inbound("conv_invalid", "u1", Some("张三"), "msg_invalid");
    invalid_message.account_ref = " ".to_string();

    let error = invalid_input
        .handle_inbound(invalid_message)
        .await
        .expect_err("missing account reference must be reported");
    assert_inbound_error(
        error,
        ChannelInboundFailureKind::InvalidInbound,
        false,
        "account_ref",
    );

    let bindings = Arc::new(MemoryChannelBindingRepo::new("pre"));
    bindings
        .create(active_binding(
            "binding_context",
            "robot_1",
            BindingTarget::Group {
                group_id: "missing_group".to_string(),
            },
            Visibility::FullTranscript,
        ))
        .await?;
    let context_failure = inbound_service(
        bindings,
        Arc::new(MemoryImParticipantRepo::new()),
        Arc::new(RecordingSessionRepo::default()),
        Arc::new(RecordingMessageFlow::default()),
        Arc::new(RecordingRegistry::default()),
    )
    .await;

    let error = context_failure
        .handle_inbound(inbound("conv_context", "u1", Some("张三"), "msg_context"))
        .await
        .expect_err("missing bound group must be reported");
    assert_inbound_error(
        error,
        ChannelInboundFailureKind::ContextResolutionFailed,
        false,
        "missing_group",
    );

    Ok(())
}

#[tokio::test]
async fn inbound_classifies_actor_participant_session_and_dispatch_failures() -> TestResult {
    let bindings = active_inbound_binding_repo().await;
    let registry = Arc::new(RecordingRegistry::default());
    *registry.fail_ensure_human.lock().await = Some("actor write failed".to_string());
    let actor_failure = inbound_service(
        bindings,
        Arc::new(MemoryImParticipantRepo::new()),
        Arc::new(RecordingSessionRepo::default()),
        Arc::new(RecordingMessageFlow::default()),
        registry,
    )
    .await;

    let error = actor_failure
        .handle_inbound(inbound("conv_actor", "u1", Some("张三"), "msg_actor"))
        .await
        .expect_err("actor write failure must be reported");
    assert_inbound_error(
        error,
        ChannelInboundFailureKind::ActorResolutionFailed,
        true,
        "actor write failed",
    );

    let participant_failure = inbound_service(
        active_inbound_binding_repo().await,
        Arc::new(FailingParticipantRepo),
        Arc::new(RecordingSessionRepo::default()),
        Arc::new(RecordingMessageFlow::default()),
        Arc::new(RecordingRegistry::default()),
    )
    .await;

    let error = participant_failure
        .handle_inbound(inbound(
            "conv_participant",
            "u1",
            Some("张三"),
            "msg_participant",
        ))
        .await
        .expect_err("participant mapping failure must be reported");
    assert_inbound_error(
        error,
        ChannelInboundFailureKind::ActorResolutionFailed,
        true,
        "actor write failed",
    );

    let session_repo = Arc::new(RecordingSessionRepo::default());
    *session_repo.fail_create.lock().await = Some("session create failed".to_string());
    let session_failure = inbound_service(
        active_inbound_binding_repo().await,
        Arc::new(MemoryImParticipantRepo::new()),
        session_repo,
        Arc::new(RecordingMessageFlow::default()),
        Arc::new(RecordingRegistry::default()),
    )
    .await;

    let error = session_failure
        .handle_inbound(inbound("conv_session", "u1", Some("张三"), "msg_session"))
        .await
        .expect_err("session failure must be reported");
    assert_inbound_error(
        error,
        ChannelInboundFailureKind::SessionResolutionFailed,
        true,
        "session create failed",
    );

    let message_flow = Arc::new(RecordingMessageFlow::default());
    *message_flow.failed_dispatch_count.lock().await = 1;
    let dispatch_failure = inbound_service(
        active_inbound_binding_repo().await,
        Arc::new(MemoryImParticipantRepo::new()),
        Arc::new(RecordingSessionRepo::default()),
        message_flow,
        Arc::new(RecordingRegistry::default()),
    )
    .await;

    let error = dispatch_failure
        .handle_inbound(inbound("conv_dispatch", "u1", Some("张三"), "msg_dispatch"))
        .await
        .expect_err("failed message flow dispatch must be reported");
    assert_inbound_error(
        error,
        ChannelInboundFailureKind::DispatchFailed,
        true,
        "failed deliveries",
    );

    Ok(())
}

#[tokio::test]
async fn inbound_returns_error_when_session_participant_write_fails() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;
    *harness.session_repo.fail_add_participant.lock().await =
        Some("participant write failed".to_string());

    let result = harness
        .service
        .handle_inbound(group_inbound(
            "conv_group",
            "u1",
            Some("张三"),
            "msg_fail",
            true,
        ))
        .await;

    assert_inbound_error(
        result.expect_err("participant write failure must be reported"),
        ChannelInboundFailureKind::SessionResolutionFailed,
        true,
        "participant write failed",
    );
    assert!(harness.message_flow.web_sends.lock().await.is_empty());

    Ok(())
}

#[tokio::test]
async fn inbound_group_per_sender_scope_isolates_same_conversation() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::PerSender),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;

    harness
        .service
        .handle_inbound(group_inbound(
            "conv_group",
            "u1",
            Some("张三"),
            "msg_u1",
            true,
        ))
        .await?;
    harness
        .service
        .handle_inbound(group_inbound(
            "conv_group",
            "u2",
            Some("李四"),
            "msg_u2",
            true,
        ))
        .await?;

    let u1 = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_group",
            SessionScope::PerSender,
            Some("u1"),
        )
        .await?
        .ok_or_else(|| ServiceError::InternalError("missing u1 conversation".to_string()))?;
    let u2 = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_group",
            SessionScope::PerSender,
            Some("u2"),
        )
        .await?
        .ok_or_else(|| ServiceError::InternalError("missing u2 conversation".to_string()))?;

    assert_ne!(u1.bcs_session_id, u2.bcs_session_id);
    assert_eq!(harness.message_flow.web_sends.lock().await.len(), 2);

    Ok(())
}

#[tokio::test]
async fn inbound_image_attachment_reaches_message_flow() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;
    let mut inbound = group_inbound("conv_group", "u1", Some("张三"), "msg-image", true);
    inbound.attachments = Some(vec![serde_json::from_value(serde_json::json!({
        "attachment_id": "att-1",
        "type": "image",
        "file_name": "image",
        "url": "https://download.example.com/image?token=temporary"
    }))?]);

    harness.service.handle_inbound(inbound).await?;

    let web_sends = harness.message_flow.web_sends.lock().await;
    assert_eq!(web_sends.len(), 1);
    let attachments = serde_json::to_value(&web_sends[0].attachments)?;
    assert_eq!(attachments[0]["attachment_id"], "att-1");
    assert_eq!(
        attachments[0]["url"],
        "https://download.example.com/image?token=temporary"
    );
    Ok(())
}

#[tokio::test]
async fn dingtalk_file_attachment_requires_dm_or_per_sender_scope() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;
    let mut inbound = group_inbound("conv_group", "u1", Some("张三"), "msg-file", true);
    inbound.text.clear();
    inbound.attachments = Some(vec![serde_json::from_value(serde_json::json!({
        "attachment_id": "att-file",
        "type": "file",
        "file_name": "design.pdf",
        "url": "https://download.example.com/temporary"
    }))?]);

    let error = harness.service.handle_inbound(inbound).await.unwrap_err();

    assert_eq!(error.kind, ChannelInboundFailureKind::UnsupportedAttachment);
    assert!(harness.message_flow.web_sends.lock().await.is_empty());
    Ok(())
}

#[tokio::test]
async fn per_sender_accepts_pure_file_message_without_fabricated_text() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    let mut binding = active_binding(
        "binding_per_sender_file",
        "robot_1",
        BindingTarget::Group {
            group_id: "group_1".to_string(),
        },
        Visibility::FullTranscript,
    );
    binding.group_chat_scope = Some(GroupChatScope::PerSender);
    harness.binding_repo.create(binding).await?;
    let mut inbound = group_inbound("conv_group", "u1", Some("张三"), "msg-file", true);
    inbound.text.clear();
    inbound.attachments = Some(vec![serde_json::from_value(serde_json::json!({
        "attachment_id": "att-file",
        "type": "file",
        "file_name": "design.pdf",
        "url": "https://download.example.com/temporary"
    }))?]);

    harness.service.handle_inbound(inbound).await?;

    let sends = harness.message_flow.web_sends.lock().await;
    assert_eq!(sends.len(), 1);
    assert_eq!(sends[0].message, "");
    assert_eq!(
        sends[0].attachments.as_ref().unwrap()[0].attachment_type,
        bcs_domain::AttachmentType::File
    );
    Ok(())
}
