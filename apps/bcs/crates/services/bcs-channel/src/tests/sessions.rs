use super::*;

#[tokio::test]
async fn inbound_group_target_injects_initial_context_once_for_new_session() -> TestResult {
    let mut group = manager_group("group_1");
    group.label = Some("跨团队协作".to_string());
    group.context = Some("发布前完成风险检查".to_string());
    let harness = TestHarness::new(group).await?;
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
        .handle_inbound(inbound("conv_1", "u1", Some("张三"), "msg_1"))
        .await?;
    harness
        .service
        .handle_inbound(inbound("conv_1", "u1", Some("张三"), "msg_2"))
        .await?;

    let notifications = harness.system_message.notifications.lock().await;
    assert_eq!(notifications.len(), 1);
    let notification = &notifications[0];
    assert_eq!(notification.group_id, "group_1");
    assert!(
        notification
            .session_id
            .starts_with("group_1:channel_dingtalk_")
    );
    assert_eq!(notification.participants.len(), 2);
    assert!(notification.participants.iter().all(Participant::is_bot));
    let SystemMessageEvent::SessionContext {
        group_id,
        session_id,
        reason,
        session_input,
        task_ledger,
        driver_delivery,
        ..
    } = &notification.event
    else {
        return Err("expected session context notification".into());
    };
    assert_eq!(group_id, "group_1");
    assert_eq!(session_id, &notification.session_id);
    assert_eq!(reason, "跨团队协作");
    assert_eq!(session_input, &None);
    assert_eq!(task_ledger, &None);
    assert_eq!(driver_delivery, &None);

    Ok(())
}

#[tokio::test]
async fn inbound_group_target_injects_driver_context_when_binding_configures_inject() -> TestResult {
    let harness = TestHarness::new(chat_group("group_1")).await?;
    let mut config = dingtalk_config("robot_1");
    config[GROUP_CONTEXT_DELIVERY_CONFIG] = serde_json::json!("inject");
    harness.service.create_binding(CreateBindingCommand {
        channel_type: channel_type(), account_ref: "robot_1".to_string(),
        target: BindingTarget::Group { group_id: "group_1".to_string() },
        group_chat_scope: Some(GroupChatScope::ConversationShared),
        outbound_visibility: Visibility::FullTranscript, env: "dev".to_string(),
        created_by: Some("creator".to_string()), config,
    }).await?;

    harness.service.handle_inbound(group_inbound("conv_1", "u1", Some("张三"), "msg_1", true)).await?;

    let notifications = harness.system_message.notifications.lock().await;
    assert_eq!(notifications.len(), 1);
    let SystemMessageEvent::SessionContext { driver_delivery, .. } = &notifications[0].event else {
        return Err("expected session context notification".into());
    };
    assert_eq!(driver_delivery, &Some(bcs_domain::DeliveryType::Inject),
        "binding group_context_delivery=inject must override the driver's send");
    Ok(())
}

#[tokio::test]
async fn group_context_delivery_config_validates_create_and_update() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    let mut config = dingtalk_config("robot_1");
    config[GROUP_CONTEXT_DELIVERY_CONFIG] = serde_json::json!("both");
    let mut command = CreateBindingCommand {
        channel_type: channel_type(), account_ref: "robot_1".to_string(),
        target: BindingTarget::Group { group_id: "group_1".to_string() },
        group_chat_scope: Some(GroupChatScope::ConversationShared),
        outbound_visibility: Visibility::FullTranscript, env: "dev".to_string(),
        created_by: Some("creator".to_string()), config,
    };
    assert!(matches!(harness.service.create_binding(command.clone()).await, Err(ChannelUseCaseError::InvalidParams(_))));
    assert!(harness.binding_repo.list().await?.is_empty());
    command.config[GROUP_CONTEXT_DELIVERY_CONFIG] = serde_json::json!("inject");
    let binding = harness.service.create_binding(command).await?;
    assert_eq!(binding.config[GROUP_CONTEXT_DELIVERY_CONFIG], "inject");
    for invalid in [serde_json::Value::Null, serde_json::json!(1), serde_json::json!("true")] {
        let mut config = dingtalk_config("robot_1");
        config[GROUP_CONTEXT_DELIVERY_CONFIG] = invalid;
        assert!(matches!(harness.service.update_binding_config(&binding.id, config).await, Err(ChannelUseCaseError::InvalidParams(_))));
    }
    let stored = harness.binding_repo.get(&binding.id).await?.expect("binding");
    assert_eq!(stored.config[GROUP_CONTEXT_DELIVERY_CONFIG], "inject");
    let mut config = dingtalk_config("robot_1");
    config[GROUP_CONTEXT_DELIVERY_CONFIG] = serde_json::json!("send");
    harness.service.update_binding_config(&binding.id, config).await?;
    assert_eq!(harness.binding_repo.get(&binding.id).await?.expect("binding").config[GROUP_CONTEXT_DELIVERY_CONFIG], "send");
    Ok(())
}

#[test]
fn group_context_delivery_config_resolves_supported_and_defensive_values() {
    for (config, expected) in [
        (serde_json::json!({}), None),
        (
            serde_json::json!({ "group_context_delivery": "send" }),
            Some(bcs_domain::DeliveryType::Send),
        ),
        (
            serde_json::json!({ "group_context_delivery": "inject" }),
            Some(bcs_domain::DeliveryType::Inject),
        ),
        (
            serde_json::json!({ "group_context_delivery": "unsupported" }),
            None,
        ),
        (
            serde_json::json!({ "group_context_delivery": 1 }),
            None,
        ),
    ] {
        assert_eq!(crate::binding_group_context_delivery(&config), expected);
    }
}

#[tokio::test]
async fn inbound_bot_target_does_not_inject_initial_group_context() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .binding_repo
        .create(active_binding(
            "binding_bot",
            "robot_1",
            BindingTarget::Bot {
                bot_id: "target_bot".to_string(),
            },
            Visibility::FullTranscript,
        ))
        .await?;

    harness
        .service
        .handle_inbound(inbound("conv_1", "u1", Some("张三"), "msg_1"))
        .await?;

    assert!(harness.system_message.notifications.lock().await.is_empty());

    Ok(())
}

#[tokio::test]
async fn direct_bot_group_chat_uses_bounded_channel_session_id() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    let binding_id = "0d86bd1b-6efd-4b5c-8906-1be1b5717c74";
    let mut binding = active_binding(
        binding_id,
        "robot_1",
        BindingTarget::Bot {
            bot_id: "20260625_fkxorj0t:410025".to_string(),
        },
        Visibility::FullTranscript,
    );
    binding.group_chat_scope = Some(GroupChatScope::PerSender);
    harness.binding_repo.create(binding).await?;

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

    let mapped = harness
        .conversation_repo
        .get(
            binding_id,
            "conv_group",
            SessionScope::PerSender,
            Some("u1"),
        )
        .await?
        .ok_or_else(|| ServiceError::InternalError("missing conversation".to_string()))?;
    let (group_id, suffix) = mapped
        .bcs_session_id
        .split_once(':')
        .expect("channel session id has one group separator");
    assert!(group_id.starts_with("bcs_grp_dingtalk_"));
    assert!(suffix.starts_with("channel_dingtalk_"));
    assert_eq!(mapped.bcs_session_id.matches(':').count(), 1);
    assert!(mapped.bcs_session_id.len() <= 128);

    Ok(())
}

#[tokio::test]
async fn direct_bot_group_chat_defaults_to_per_sender_scope() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    let binding_id = "binding_bot_default_scope";
    let mut binding = active_binding(
        binding_id,
        "robot_1",
        BindingTarget::Bot {
            bot_id: "target_bot".to_string(),
        },
        Visibility::FullTranscript,
    );
    binding.group_chat_scope = None;
    harness.binding_repo.create(binding).await?;

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
            binding_id,
            "conv_group",
            SessionScope::PerSender,
            Some("u1"),
        )
        .await?
        .ok_or_else(|| ServiceError::InternalError("missing u1 conversation".to_string()))?;
    let u2 = harness
        .conversation_repo
        .get(
            binding_id,
            "conv_group",
            SessionScope::PerSender,
            Some("u2"),
        )
        .await?
        .ok_or_else(|| ServiceError::InternalError("missing u2 conversation".to_string()))?;
    assert_ne!(u1.bcs_session_id, u2.bcs_session_id);

    Ok(())
}

#[tokio::test]
async fn inbound_group_shared_scope_reuses_conversation_session_for_different_senders()
-> TestResult {
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

    let shared = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_group",
            SessionScope::Conversation,
            None,
        )
        .await?
        .ok_or_else(|| {
            ServiceError::InternalError("missing shared conversation".to_string())
        })?;
    let per_sender_u1 = harness
        .conversation_repo
        .get(
            "generated_id",
            "conv_group",
            SessionScope::PerSender,
            Some("u1"),
        )
        .await?;
    assert_eq!(per_sender_u1, None);

    let web_sends = harness.message_flow.web_sends.lock().await;
    assert_eq!(web_sends.len(), 2);
    assert_eq!(
        web_sends[0].session_id.as_deref(),
        Some(shared.bcs_session_id.as_str())
    );
    assert_eq!(
        web_sends[1].session_id.as_deref(),
        Some(shared.bcs_session_id.as_str())
    );
    drop(web_sends);

    let ensured = harness.registry.ensured.lock().await.clone();
    assert_eq!(
        ensured,
        vec![
            ("u1".to_string(), "张三".to_string()),
            ("u2".to_string(), "李四".to_string()),
        ]
    );

    Ok(())
}

#[tokio::test]
async fn group_new_session_per_message_respects_scope_target_and_direct_chats() -> TestResult {
    for target in [
        BindingTarget::Group { group_id: "group_1".to_string() },
        BindingTarget::Bot { bot_id: "target_bot".to_string() },
    ] {
        for scope in [GroupChatScope::ConversationShared, GroupChatScope::PerSender] {
            for enabled in [None, Some(false), Some(true)] {
                for is_group in [false, true] {
                    let harness = TestHarness::new(manager_group("group_1")).await?;
                    let mut binding = active_binding("binding_1", "robot_1", target.clone(), Visibility::FullTranscript);
                    binding.group_chat_scope = Some(scope);
                    if let Some(enabled) = enabled {
                        binding.config[GROUP_CHAT_NEW_SESSION_CONFIG] = serde_json::json!(enabled);
                    }
                    harness.binding_repo.create(binding).await?;
                    for msg_id in ["msg_1", "msg_2", "msg_2"] {
                        let msg = if is_group {
                            group_inbound("conv_1", "u1", Some("张三"), msg_id, true)
                        } else {
                            inbound("conv_1", "u1", Some("张三"), msg_id)
                        };
                        harness.service.handle_inbound(msg).await?;
                    }
                    let sends = harness.message_flow.web_sends.lock().await;
                    assert_eq!(sends.len(), 2, "duplicate messages must be ignored");
                    assert_eq!(sends[0].session_id != sends[1].session_id, is_group && enabled == Some(true));
                }
            }
        }
    }
    Ok(())
}

#[tokio::test]
async fn group_new_session_per_message_preserves_concurrent_reply_routes() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    let mut binding = active_binding("binding_1", "robot_1", BindingTarget::Group {
        group_id: "group_1".to_string(),
    }, Visibility::FullTranscript);
    binding.config[GROUP_CHAT_NEW_SESSION_CONFIG] = serde_json::json!(true);
    harness.binding_repo.create(binding).await?;
    let (first, second) = tokio::join!(
        harness.service.handle_inbound(group_inbound("conv_1", "u1", Some("张三"), "msg_1", true)),
        harness.service.handle_inbound(group_inbound("conv_1", "u2", Some("李四"), "msg_2", true)),
    );
    first?;
    second?;
    let sends = harness.message_flow.web_sends.lock().await;
    assert_eq!(sends.len(), 2);
    assert_ne!(sends[0].session_id, sends[1].session_id);
    for send in sends.iter() {
        let session_id = send.session_id.as_deref().expect("session id");
        assert_eq!(harness.session_repo.get(session_id).await.expect("session").status, SessionStatus::Running);
        harness.service.try_outbound(outbound(session_id, ParticipantRole::Worker, false)).await?;
    }
    let events = harness.delivery.events.lock().await;
    for send in sends.iter() {
        assert!(events.iter().any(|event| Some(&event.bcs_session_id) == send.session_id.as_ref()
            && event.im_conversation_id == "conv_1"));
    }
    Ok(())
}

#[tokio::test]
async fn group_new_session_per_message_starts_independent_workflows() -> TestResult {
    let harness = TestHarness::new(state_machine_group("group_sm")).await?;
    let mut binding = active_binding("binding_1", "robot_1", BindingTarget::Group {
        group_id: "group_sm".to_string(),
    }, Visibility::FullTranscript);
    binding.config[GROUP_CHAT_NEW_SESSION_CONFIG] = serde_json::json!(true);
    harness.binding_repo.create(binding).await?;
    for msg_id in ["msg_1", "msg_2", "msg_2"] {
        harness.service.handle_inbound(group_inbound("conv_1", "u1", Some("张三"), msg_id, true)).await?;
    }
    let starts = harness.collaboration_runtime.starts.lock().await;
    assert_eq!(starts.len(), 2);
    assert_ne!(starts[0].session_id, starts[1].session_id);
    Ok(())
}

#[tokio::test]
async fn group_new_session_per_message_validates_create_and_update() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    let mut config = dingtalk_config("robot_1");
    config[GROUP_CHAT_NEW_SESSION_CONFIG] = serde_json::json!("true");
    let mut command = CreateBindingCommand {
        channel_type: channel_type(),
        account_ref: "robot_1".to_string(),
        target: BindingTarget::Group { group_id: "group_1".to_string() },
        group_chat_scope: Some(GroupChatScope::ConversationShared),
        outbound_visibility: Visibility::FullTranscript,
        env: "dev".to_string(),
        created_by: Some("creator".to_string()),
        config,
    };
    assert!(matches!(harness.service.create_binding(command.clone()).await, Err(ChannelUseCaseError::InvalidParams(_))));
    assert!(harness.binding_repo.list().await?.is_empty());
    command.config[GROUP_CHAT_NEW_SESSION_CONFIG] = serde_json::json!(true);
    let binding = harness.service.create_binding(command).await?;
    assert_eq!(binding.config[GROUP_CHAT_NEW_SESSION_CONFIG], true);
    for invalid in [serde_json::Value::Null, serde_json::json!(1), serde_json::json!("false")] {
        let mut config = dingtalk_config("robot_1");
        config[GROUP_CHAT_NEW_SESSION_CONFIG] = invalid;
        assert!(matches!(harness.service.update_binding_config(&binding.id, config).await, Err(ChannelUseCaseError::InvalidParams(_))));
    }
    let stored = harness.binding_repo.get(&binding.id).await?.expect("binding");
    assert_eq!(stored.config[GROUP_CHAT_NEW_SESSION_CONFIG], true);
    let mut config = dingtalk_config("robot_1");
    config[GROUP_CHAT_NEW_SESSION_CONFIG] = serde_json::json!(false);
    harness.service.update_binding_config(&binding.id, config).await?;
    assert_eq!(harness.binding_repo.get(&binding.id).await?.expect("binding").config[GROUP_CHAT_NEW_SESSION_CONFIG], false);
    Ok(())
}

// ── /new slash 命令 ─────────────────────────────────────────
