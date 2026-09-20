use super::*;

#[tokio::test]
async fn inbound_state_machine_group_creates_service_invocation_and_starts_runtime()
-> TestResult {
    let harness = TestHarness::new(state_machine_group("group_sm")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_sm".to_string(),
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
        .handle_inbound(group_inbound("conv_sm", "u1", Some("张三"), "msg_sm", true))
        .await?;

    let starts = harness.collaboration_runtime.starts.lock().await.clone();
    assert_eq!(starts.len(), 1);
    assert_eq!(starts[0].group_id, "group_sm");
    assert_eq!(starts[0].caller_id.as_deref(), Some("human_u1"));
    assert_eq!(
        starts[0]
            .authenticated_human
            .as_ref()
            .map(|human| human.actor_id.as_str()),
        Some("human_u1")
    );
    assert_eq!(starts[0].input["source"], "dingtalk");
    assert_eq!(starts[0].input["sender"]["actor_id"], "human_u1");
    assert_eq!(starts[0].input["conversation"]["id"], "conv_sm");

    let session_id = starts[0]
        .session_id
        .as_deref()
        .ok_or_else(|| ServiceError::InternalError("missing runtime session_id".to_string()))?;
    let session =
        harness.session_repo.get(session_id).await.ok_or_else(|| {
            ServiceError::InternalError("missing service session".to_string())
        })?;
    assert_eq!(session.session_kind, SessionKind::ServiceInvocation);
    assert_eq!(session.caller_id.as_deref(), Some("human_u1"));
    assert_eq!(
        session.caller_principal.as_deref(),
        Some("dingtalk:conv_sm")
    );

    let channel = session
        .meta
        .as_ref()
        .and_then(|meta| meta.get("channel"))
        .ok_or_else(|| ServiceError::InternalError("missing channel meta".to_string()))?;
    assert_eq!(channel["binding_id"], "generated_id");
    assert_eq!(channel["conversation_id"], "conv_sm");
    assert_eq!(channel["session_scope"], "conversation");
    assert_eq!(channel["context_projection"], "group");
    assert_eq!(
        channel.get("im_user_id").and_then(|value| value.as_str()),
        None
    );

    let mapped = harness
        .conversation_repo
        .get("generated_id", "conv_sm", SessionScope::Conversation, None)
        .await?
        .ok_or_else(|| {
            ServiceError::InternalError("missing state machine conversation".to_string())
        })?;
    assert_eq!(mapped.bcs_session_id, session_id);
    assert!(harness.message_flow.web_sends.lock().await.is_empty());
    assert!(
        harness
            .session_repo
            .added_participants
            .lock()
            .await
            .is_empty()
    );

    Ok(())
}

#[tokio::test]
async fn inbound_state_machine_group_preserves_source_message_while_starting() -> TestResult {
    let harness = TestHarness::new(state_machine_group("group_sm")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_sm".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;
    harness
        .session_repo
        .create(
            "group_sm",
            NewSessionParams {
                id: Some("pending-session".to_string()),
                session_kind: SessionKind::ServiceInvocation,
                ..Default::default()
            },
        )
        .await?;
    harness
        .conversation_repo
        .upsert(bcs_domain::ConversationSessionMap {
            binding_id: "generated_id".to_string(),
            im_conversation_id: "conv_sm".to_string(),
            im_conversation_type: "2".to_string(),
            session_scope: SessionScope::Conversation,
            im_user_id: None,
            bcs_session_id: "pending-session".to_string(),
            last_active_at: 42,
        })
        .await?;

    harness
        .service
        .handle_inbound(group_inbound(
            "conv_sm",
            "u1",
            Some("张三"),
            "msg_while_starting",
            true,
        ))
        .await?;

    assert!(harness.collaboration_runtime.starts.lock().await.is_empty());
    let events = harness.delivery.events.lock().await;
    let response = events.last().expect("state-machine starting response");
    assert_eq!(response.purpose, ChannelOutboundPurpose::HumanInputAck);
    assert_eq!(
        response.source_im_message_id.as_deref(),
        Some("msg_while_starting")
    );
    assert!(
        response
            .text
            .as_deref()
            .is_some_and(|text| text.contains("流程正在启动"))
    );

    Ok(())
}

#[tokio::test]
async fn inbound_state_machine_group_reuses_active_run_and_routes_human_response() -> TestResult
{
    let harness = TestHarness::new(state_machine_group("group_sm")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_sm".to_string(),
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

    let mut response = group_inbound("conv_sm", "u1", Some("张三"), "msg_response", true);
    response.text = "looks good".to_string();
    harness.service.handle_inbound(response).await?;

    assert_eq!(harness.collaboration_runtime.starts.lock().await.len(), 1);
    let responses = harness.collaboration_runtime.human_responses.lock().await;
    assert_eq!(responses.len(), 1);
    assert_eq!(responses[0].run_id, "state_run_1");
    assert_eq!(responses[0].node_id, "human_review");
    assert_eq!(responses[0].caller_actor_id, "human_u1");
    assert_eq!(responses[0].content, "looks good");
    assert!(matches!(
        &responses[0].source,
        HumanResponseSource::Channel {
            binding_id,
            conversation_id,
            message_id,
        } if binding_id == "generated_id"
            && conversation_id == "conv_sm"
            && message_id == "msg_response"
    ));
    drop(responses);
    let events = harness.delivery.events.lock().await;
    let acknowledgement = events.last().expect("HumanInput acknowledgement");
    assert_eq!(
        acknowledgement.purpose,
        ChannelOutboundPurpose::HumanInputAck
    );
    assert_eq!(
        acknowledgement.source_im_message_id.as_deref(),
        Some("msg_response")
    );

    Ok(())
}

#[tokio::test]
async fn inbound_state_machine_group_rejects_message_without_pending_human_input() -> TestResult
{
    let harness = TestHarness::new(state_machine_group("group_sm")).await?;
    start_state_machine_channel(&harness).await?;

    let mut response = group_inbound("conv_sm", "u1", Some("张三"), "msg_response", true);
    response.text = "send this nowhere".to_string();
    harness.service.handle_inbound(response).await?;

    assert!(
        harness
            .collaboration_runtime
            .human_responses
            .lock()
            .await
            .is_empty()
    );
    assert!(harness.message_flow.web_sends.lock().await.is_empty());
    let events = harness.delivery.events.lock().await;
    assert!(
        events
            .last()
            .and_then(|event| event.text.as_deref())
            .is_some_and(|text| {
                text.contains("没有可通过此会话回复") && text.contains("消息未被接收")
            })
    );
    assert_eq!(
        events
            .last()
            .and_then(|event| event.source_im_message_id.as_deref()),
        Some("msg_response")
    );

    Ok(())
}

#[tokio::test]
async fn inbound_state_machine_group_rejects_multiple_pending_human_inputs() -> TestResult {
    let harness = TestHarness::new(state_machine_group("group_sm")).await?;
    start_state_machine_channel(&harness).await?;
    *harness
        .collaboration_runtime
        .pending_human_nodes
        .lock()
        .await = vec![
        pending_human_node("review_a"),
        pending_human_node("review_b"),
    ];

    let mut response = group_inbound("conv_sm", "u1", Some("张三"), "msg_response", true);
    response.text = "do not guess the target".to_string();
    harness.service.handle_inbound(response).await?;

    assert!(
        harness
            .collaboration_runtime
            .human_responses
            .lock()
            .await
            .is_empty()
    );
    let events = harness.delivery.events.lock().await;
    assert!(
        events
            .last()
            .and_then(|event| event.text.as_deref())
            .is_some_and(|text| {
                text.contains("没有可通过此会话回复") && text.contains("消息未被接收")
            })
    );

    Ok(())
}

#[tokio::test]
async fn inbound_state_machine_start_failure_cleans_conversation_mapping_and_session()
-> TestResult {
    let harness = TestHarness::new(state_machine_group("group_sm")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_sm".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;
    *harness.collaboration_runtime.start_error.lock().await =
        Some("definition unavailable".to_string());

    let result = harness
        .service
        .handle_inbound(group_inbound(
            "conv_sm",
            "u1",
            Some("张三"),
            "msg_start",
            true,
        ))
        .await;
    assert!(result.is_err());
    assert!(
        harness
            .conversation_repo
            .get("generated_id", "conv_sm", SessionScope::Conversation, None)
            .await?
            .is_none()
    );
    let starts = harness.collaboration_runtime.starts.lock().await;
    let session_id = starts[0].session_id.as_deref().expect("session id");
    let session = harness
        .session_repo
        .get(session_id)
        .await
        .expect("orphan session should remain auditable");
    assert_eq!(session.status, SessionStatus::Completed);
    assert_eq!(
        session.error_message.as_deref(),
        Some("state_machine_start_failed")
    );

    Ok(())
}

#[tokio::test]
async fn human_input_ready_uses_existing_channel_conversation_mapping() -> TestResult {
    human_input_ready_uses_channel_mapping(false).await?;
    human_input_ready_uses_channel_mapping(true).await
}

async fn human_input_ready_uses_channel_mapping(new_session_per_message: bool) -> TestResult {
    let mut config = dingtalk_config("robot_1");
    config[GROUP_CHAT_NEW_SESSION_CONFIG] = serde_json::json!(new_session_per_message);
    let harness = TestHarness::new(state_machine_group("group_sm")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_sm".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config,
        })
        .await?;
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
        .expect("session id");

    let outcome = SessionChannelOutboundPort::publish_human_input_ready(
        &harness.service,
        HumanInputReadyEvent {
            event_id: "event-1".to_string(),
            group_id: "group_sm".to_string(),
            session_id,
            run_id: "state_run_1".to_string(),
            node_id: "human_review".to_string(),
            display_name: "Human review".to_string(),
            instruction: "请审核上游结果".to_string(),
            assignee_actor_id: "human_u1".to_string(),
            channel_type: channel_type(),
            notification_mode: HumanInputNotificationMode::FixedGroup,
            fixed_group_conversation_id: Some("conv_sm".to_string()),
            response_ref: "state_run_1:human_review".to_string(),
            upstream_artifacts: vec![bcs_service_api::JudgeArtifact {
                node_id: "draft".to_string(),
                text: "draft content".to_string(),
            }],
            judge_outcomes: vec!["approve".to_string(), "reject".to_string()],
            timeout_deadline_ms: Some(1234),
            loop_context: None,
        },
    )
    .await?;
    assert_eq!(outcome, SessionChannelDeliveryOutcome::Delivered);
    let events = harness.delivery.events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].kind, ChannelOutboundEventKind::System);
    assert!(
        events[0]
            .text
            .as_deref()
            .is_some_and(|text| text.contains("请直接 @ 机器人回复"))
    );
    assert!(
        !events[0]
            .text
            .as_deref()
            .is_some_and(|text| text.contains("response_ref:"))
    );
    assert_eq!(events[0].purpose, ChannelOutboundPurpose::HumanInputRequest);
    assert_eq!(events[0].raw_payload["request_id"], "event-1");

    drop(events);
    harness.service.handle_inbound(group_inbound(
        "conv_sm", "u1", Some("张三"), "msg_reply", true,
    )).await?;
    assert_eq!(harness.collaboration_runtime.starts.lock().await.len(), 1);
    assert_eq!(harness.collaboration_runtime.human_responses.lock().await.len(), 1);
    Ok(())
}

#[tokio::test]
async fn direct_human_input_resolves_actor_without_mapping_and_queues_same_scope() -> TestResult
{
    let harness = TestHarness::new(state_machine_group("group_sm")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_sm".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;

    let mut invalid_actor =
        human_input_ready_event("direct-invalid", HumanInputNotificationMode::DirectAssignee);
    invalid_actor.assignee_actor_id = "bot_u1".to_string();
    let invalid_result =
        SessionChannelOutboundPort::publish_human_input_ready(&harness.service, invalid_actor)
            .await;
    assert!(matches!(
        invalid_result,
        Err(ServiceError::InvalidOperation { .. })
    ));

    for event_id in ["direct-first", "direct-second"] {
        assert_eq!(
            SessionChannelOutboundPort::publish_human_input_ready(
                &harness.service,
                human_input_ready_event(event_id, HumanInputNotificationMode::DirectAssignee,),
            )
            .await?,
            SessionChannelDeliveryOutcome::Delivered
        );
    }

    let first = harness
        .human_input_requests
        .get("direct-first")
        .await?
        .expect("first direct request");
    assert_eq!(first.status, HumanInputRequestStatus::Active);
    assert_eq!(first.im_conversation_id, "u1");
    assert_eq!(first.im_conversation_type, "1");
    assert_eq!(first.im_user_id.as_deref(), Some("u1"));

    let second = harness
        .human_input_requests
        .get("direct-second")
        .await?
        .expect("queued direct request");
    assert_eq!(second.status, HumanInputRequestStatus::Queued);
    assert_eq!(
        harness
            .human_input_requests
            .count_queued(&second.reply_scope_key)
            .await?,
        1
    );

    let events = harness.delivery.events.lock().await;
    assert_eq!(events.len(), 2);
    assert_eq!(events[0].purpose, ChannelOutboundPurpose::HumanInputRequest);
    assert_eq!(
        events[1].purpose,
        ChannelOutboundPurpose::HumanInputQueueSummary
    );
    assert!(
        events[0]
            .text
            .as_deref()
            .is_some_and(|text| text.contains("上游结果") && text.contains("请直接回复本会话"))
    );
    assert!(
        events[1]
            .text
            .as_deref()
            .is_some_and(|text| text.contains("另有 1 项排队"))
    );

    Ok(())
}

#[tokio::test]
async fn human_input_delivery_failure_is_persisted() -> TestResult {
    let harness = TestHarness::new(state_machine_group("group_sm")).await?;
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_sm".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;
    *harness.delivery.fail_error.lock().await = Some("provider unavailable".to_string());

    let result = SessionChannelOutboundPort::publish_human_input_ready(
        &harness.service,
        human_input_ready_event("delivery-failed", HumanInputNotificationMode::FixedGroup),
    )
    .await;
    assert!(matches!(result, Err(ServiceError::InternalError(_))));

    let request = harness
        .human_input_requests
        .get("delivery-failed")
        .await?
        .expect("failed request");
    assert_eq!(request.status, HumanInputRequestStatus::DeliveryFailed);
    assert_eq!(request.delivery_attempts, 1);
    assert!(
        request
            .last_delivery_error
            .as_deref()
            .is_some_and(|error| error.contains("provider unavailable"))
    );

    Ok(())
}
