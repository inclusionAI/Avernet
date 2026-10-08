use super::*;

#[tokio::test]
async fn list_conversations_by_session_filters_channel_type() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .binding_repo
        .create(active_binding(
            "binding_dingtalk",
            "robot_1",
            BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            Visibility::FullTranscript,
        ))
        .await?;
    let mut other_binding = active_binding(
        "binding_other",
        "other_1",
        BindingTarget::Group {
            group_id: "group_1".to_string(),
        },
        Visibility::FullTranscript,
    );
    other_binding.channel_type = "other".to_string();
    harness.binding_repo.create(other_binding).await?;
    for (binding_id, conversation_id) in [
        ("binding_dingtalk", "ding_conversation"),
        ("binding_other", "other_conversation"),
    ] {
        harness
            .conversation_repo
            .upsert(bcs_domain::ConversationSessionMap {
                binding_id: binding_id.to_string(),
                im_conversation_id: conversation_id.to_string(),
                im_conversation_type: "2".to_string(),
                session_scope: SessionScope::Conversation,
                im_user_id: None,
                bcs_session_id: "group_1:session_1".to_string(),
                last_active_at: 42,
            })
            .await?;
    }

    harness.session_repo.create("group_1", NewSessionParams {
        id: Some("group_1:session_1".to_string()),
        meta: Some(serde_json::json!({"channel": {
            "source": "metadata_only",
            "binding_id": "old_binding",
            "conversation_id": "old_conversation"
        }})),
        ..Default::default()
    }).await?;
    // Existing mappings win even when the requested type only matches metadata.
    assert!(harness.service.list_conversations_by_session(
        "group_1:session_1", Some("metadata_only".to_string())
    ).await?.is_empty());

    let mappings = harness
        .service
        .list_conversations_by_session(" group_1:session_1 ", Some(" dingtalk ".to_string()))
        .await?;

    assert_eq!(mappings.len(), 1);
    assert_eq!(mappings[0].binding_id, "binding_dingtalk");
    assert_eq!(mappings[0].im_conversation_id, "ding_conversation");

    Ok(())
}

#[tokio::test]
async fn list_conversations_by_session_reads_historical_metadata_without_binding() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    let session_id = "group_1:historical";
    let channel = serde_json::json!({
        "source": "dingtalk",
        "binding_id": "deleted_binding",
        "conversation_id": "historical_conversation",
        "conversation_type": "2",
        "session_scope": "per_sender",
        "im_user_id": "u1"
    });
    let session = harness.session_repo.create("group_1", NewSessionParams {
        id: Some(session_id.to_string()),
        meta: Some(serde_json::json!({"channel": channel})),
        ..Default::default()
    }).await?;

    for filter in [None, Some(" dingtalk ".to_string())] {
        let mappings = harness.service
            .list_conversations_by_session(session_id, filter).await?;
        assert_eq!(mappings.len(), 1);
        assert_eq!(mappings[0].binding_id, "deleted_binding");
        assert_eq!(mappings[0].im_conversation_id, "historical_conversation");
        assert_eq!(mappings[0].bcs_session_id, session_id);
        assert_eq!(mappings[0].session_scope, SessionScope::PerSender);
        assert_eq!(mappings[0].im_user_id.as_deref(), Some("u1"));
        assert_eq!(mappings[0].last_active_at, session.updated_at);
    }
    assert!(harness.service.list_conversations_by_session(
        session_id, Some("other".to_string())
    ).await?.is_empty());
    assert!(harness.conversation_repo.list_by_bcs_session(session_id).await?.is_empty());
    assert!(harness.service.list_conversations_by_session(
        "group_1:missing", None
    ).await?.is_empty());

    for invalid in [
        serde_json::Value::Null,
        serde_json::json!({}),
        serde_json::json!({"channel": {"source": "dingtalk"}}),
        serde_json::json!({"channel": {"source": "dingtalk", "binding_id": "b", "conversation_id": " "}}),
        serde_json::json!({"channel": {"binding_id": "b", "conversation_id": "c"}}),
    ] {
        harness.session_repo.sessions.lock().await
            .get_mut(session_id).expect("session").meta = Some(invalid);
        assert!(harness.service.list_conversations_by_session(
            session_id, Some("dingtalk".to_string())
        ).await?.is_empty());
    }
    Ok(())
}

#[tokio::test]
async fn list_conversations_by_session_propagates_historical_read_failure() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    *harness.session_repo.fail_get.lock().await = Some("session read failed".to_string());
    let error = harness.service.list_conversations_by_session(
        "group_1:historical", Some("dingtalk".to_string())
    ).await.expect_err("storage failure must not become an empty result");
    assert!(matches!(error, ChannelUseCaseError::Internal(
        ServiceError::InternalError(message)
    ) if message == "session read failed"));
    Ok(())
}

#[tokio::test]
async fn list_conversations_by_session_rejects_blank_session_id() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;

    let error = harness
        .service
        .list_conversations_by_session("   ", Some("dingtalk".to_string()))
        .await
        .expect_err("blank session id must fail");

    assert!(matches!(error, ChannelUseCaseError::InvalidParams(_)));
    Ok(())
}

#[tokio::test]
async fn update_binding_config_replaces_existing_provider_config() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .binding_repo
        .create(active_binding(
            "binding_1",
            "robot_1",
            BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            Visibility::FullTranscript,
        ))
        .await?;

    let next_config = serde_json::json!({
        "robot_code": "robot_1",
        "client_id": "client_id",
        "client_secret": "secret",
        "valid": true,
        "send_mode": {
            "mode": "streaming_card",
            "card_template_id": "card_tpl_123"
        }
    });

    harness
        .service
        .update_binding_config("binding_1", next_config)
        .await?;

    let binding = harness
        .binding_repo
        .get("binding_1")
        .await?
        .expect("binding exists");
    assert_eq!(binding.config["send_mode"]["mode"], "streaming_card");
    assert_eq!(
        binding.config["send_mode"]["card_template_id"],
        "card_tpl_123"
    );

    Ok(())
}

#[tokio::test]
async fn update_binding_config_rejects_provider_invalid_config() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .binding_repo
        .create(active_binding(
            "binding_1",
            "robot_1",
            BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            Visibility::FullTranscript,
        ))
        .await?;

    let result = harness
        .service
        .update_binding_config("binding_1", invalid_provider_config("robot_1"))
        .await;

    assert!(result.is_err());

    Ok(())
}

#[tokio::test]
async fn sender_identity_config_is_boolean_and_bot_binding_only() -> TestResult {
    for value in [serde_json::json!(false), serde_json::json!(true)] {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        let mut config = dingtalk_config("robot_1");
        config[FORWARD_SENDER_IDENTITY_CONFIG] = value;
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
                config,
            })
            .await?;
    }

    let harness = TestHarness::new(manager_group("group_1")).await?;
    let mut invalid_type = dingtalk_config("robot_1");
    invalid_type[FORWARD_SENDER_IDENTITY_CONFIG] = serde_json::json!("true");
    let error = harness
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
            config: invalid_type,
        })
        .await
        .expect_err("non-boolean sender identity config must fail");
    assert!(matches!(error, ChannelUseCaseError::InvalidParams(_)));

    let harness = TestHarness::new(manager_group("group_1")).await?;
    let mut group_config = dingtalk_config("robot_1");
    group_config[FORWARD_SENDER_IDENTITY_CONFIG] = serde_json::json!(true);
    let error = harness
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
            config: group_config,
        })
        .await
        .expect_err("group binding must reject sender identity forwarding");
    assert!(matches!(error, ChannelUseCaseError::InvalidParams(_)));
    Ok(())
}

#[tokio::test]
async fn sender_identity_update_applies_to_next_ordinary_bot_channel_message() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .binding_repo
        .create(active_binding(
            "binding_bot_identity",
            "robot_1",
            BindingTarget::Bot {
                bot_id: "target_bot".to_string(),
            },
            Visibility::FullTranscript,
        ))
        .await?;

    harness
        .service
        .handle_inbound(inbound("conv_dm", "410025", Some("张三"), "msg_before"))
        .await?;
    assert!(
        harness.message_flow.web_sends.lock().await[0]
            .channel_sender_identity
            .is_none()
    );

    let mut enabled = dingtalk_config("robot_1");
    enabled[FORWARD_SENDER_IDENTITY_CONFIG] = serde_json::json!(true);
    harness
        .service
        .update_binding_config("binding_bot_identity", enabled)
        .await?;

    harness
        .service
        .handle_inbound(group_inbound(
            "conv_group",
            "410025",
            Some("张三"),
            "msg_zhang",
            true,
        ))
        .await?;
    harness
        .service
        .handle_inbound(group_inbound(
            "conv_group",
            "410026",
            Some("李四"),
            "msg_li",
            true,
        ))
        .await?;
    harness
        .service
        .handle_inbound(group_inbound(
            "conv_group",
            "410027",
            None,
            "msg_no_nick",
            true,
        ))
        .await?;
    harness
        .service
        .handle_inbound(group_inbound(
            "conv_group",
            "410028",
            Some("   "),
            "msg_blank_nick",
            true,
        ))
        .await?;

    let sends = harness.message_flow.web_sends.lock().await;
    let zhang = sends[1].channel_sender_identity.as_ref().unwrap();
    assert_eq!(zhang.user_id, "410025");
    assert_eq!(zhang.display_name, "张三");
    let li = sends[2].channel_sender_identity.as_ref().unwrap();
    assert_eq!(li.user_id, "410026");
    assert_eq!(li.display_name, "李四");
    let no_nick = sends[3].channel_sender_identity.as_ref().unwrap();
    assert_eq!(no_nick.user_id, "410027");
    assert_eq!(no_nick.display_name, "410027");
    let blank_nick = sends[4].channel_sender_identity.as_ref().unwrap();
    assert_eq!(blank_nick.user_id, "410028");
    assert_eq!(blank_nick.display_name, "410028");
    Ok(())
}
