use super::*;

#[test]
fn channel_meta_uses_inbound_channel_type_as_source() {
    let msg = InboundMessage {
        channel_type: "test-im".to_string(),
        account_ref: "robot_1".to_string(),
        im_conversation_id: "conv_meta".to_string(),
        conversation_type: "2".to_string(),
        im_user_id: "u1".to_string(),
        im_user_nick: Some("张三".to_string()),
        text: "hello".to_string(),
        attachments: None,
        is_at_bot: true,
        msg_id: "msg_meta".to_string(),
    };
    let meta = channel_meta(
        &ResolvedInboundContext {
            binding_id: "binding_1".to_string(),
            group_id: "group_1".to_string(),
            session_scope: SessionScope::Conversation,
            im_user_id: None,
            caller_principal: "test-im:conv_meta".to_string(),
            context_projection: "group",
            state_machine_trigger: false,
            new_session_per_message: false,
            group_context_delivery: None,
        },
        &msg,
    );

    assert_eq!(meta["channel"]["source"], "test-im");
}

#[tokio::test]
async fn try_outbound_filters_visibility_and_sets_render_hint() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    let binding = active_binding(
        "binding_1",
        "robot_1",
        BindingTarget::Group {
            group_id: "group_1".to_string(),
        },
        Visibility::FullTranscript,
    );
    harness.binding_repo.create(binding).await?;
    harness
        .session_repo
        .create(
            "group_1",
            NewSessionParams {
                id: Some("group_1:00000001".to_string()),
                session_kind: SessionKind::Chat,
                ..Default::default()
            },
        )
        .await?;
    harness
        .conversation_repo
        .upsert(bcs_domain::ConversationSessionMap {
            binding_id: "binding_1".to_string(),
            im_conversation_id: "conv_a".to_string(),
            im_conversation_type: "2".to_string(),
            session_scope: SessionScope::Conversation,
            im_user_id: None,
            bcs_session_id: "group_1:00000001".to_string(),
            last_active_at: 1,
        })
        .await?;

    let mut msg = outbound("group_1:00000001", ParticipantRole::Worker, false);
    msg.source_im_message_id = Some("source-msg-1".to_string());
    harness.service.try_outbound(msg).await?;
    let events = harness.delivery.events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].im_conversation_id, "conv_a");
    assert_eq!(
        events[0].source_im_message_id.as_deref(),
        Some("source-msg-1")
    );
    assert!(events[0].render_sender_label);
    drop(events);

    harness
        .service
        .try_outbound(outbound("group_1:00000001", ParticipantRole::Manager, true))
        .await?;
    assert_eq!(harness.delivery.events.lock().await.len(), 1);

    Ok(())
}

#[tokio::test]
async fn try_outbound_system_bypasses_visibility_for_synthetic_sender() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .binding_repo
        .create(active_binding(
            "binding_1",
            "robot_1",
            BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            Visibility::LeadOnly,
        ))
        .await?;
    harness
        .session_repo
        .create(
            "group_1",
            NewSessionParams {
                id: Some("group_1:00000001".to_string()),
                session_kind: SessionKind::Chat,
                ..Default::default()
            },
        )
        .await?;
    harness
        .conversation_repo
        .upsert(bcs_domain::ConversationSessionMap {
            binding_id: "binding_1".to_string(),
            im_conversation_id: "conv_a".to_string(),
            im_conversation_type: "2".to_string(),
            session_scope: SessionScope::Conversation,
            im_user_id: None,
            bcs_session_id: "group_1:00000001".to_string(),
            last_active_at: 1,
        })
        .await?;

    let mut msg = outbound("group_1:00000001", ParticipantRole::Observer, false);
    msg.kind = ChannelOutboundEventKind::System;
    harness.service.try_outbound(msg).await?;

    let events = harness.delivery.events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].im_conversation_id, "conv_a");
    assert_eq!(events[0].kind, ChannelOutboundEventKind::System);

    Ok(())
}

#[tokio::test]
async fn try_outbound_free_chat_lead_only_delivers_driver_only() -> TestResult {
    let harness = TestHarness::new(chat_group("group_chat")).await?;
    harness
        .binding_repo
        .create(active_binding(
            "binding_1",
            "robot_1",
            BindingTarget::Group {
                group_id: "group_chat".to_string(),
            },
            Visibility::LeadOnly,
        ))
        .await?;
    harness
        .session_repo
        .create(
            "group_chat",
            NewSessionParams {
                id: Some("group_chat:00000001".to_string()),
                session_kind: SessionKind::Chat,
                ..Default::default()
            },
        )
        .await?;
    harness
        .conversation_repo
        .upsert(bcs_domain::ConversationSessionMap {
            binding_id: "binding_1".to_string(),
            im_conversation_id: "conv_chat".to_string(),
            im_conversation_type: "2".to_string(),
            session_scope: SessionScope::Conversation,
            im_user_id: None,
            bcs_session_id: "group_chat:00000001".to_string(),
            last_active_at: 1,
        })
        .await?;

    let mut consultant_msg =
        outbound("group_chat:00000001", ParticipantRole::Consultant, false);
    consultant_msg.group_id = "group_chat".to_string();
    harness.service.try_outbound(consultant_msg).await?;
    assert!(harness.delivery.events.lock().await.is_empty());

    let mut driver_msg = outbound("group_chat:00000001", ParticipantRole::Driver, false);
    driver_msg.group_id = "group_chat".to_string();
    harness.service.try_outbound(driver_msg).await?;
    let events = harness.delivery.events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].sender_role, ParticipantRole::Driver);

    Ok(())
}

#[tokio::test(flavor = "current_thread")]
async fn try_outbound_logs_selected_binding_and_delivery_result() -> TestResult {
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
    harness
        .session_repo
        .create(
            "group_1",
            NewSessionParams {
                id: Some("group_1:00000001".to_string()),
                session_kind: SessionKind::Chat,
                ..Default::default()
            },
        )
        .await?;
    harness
        .conversation_repo
        .upsert(bcs_domain::ConversationSessionMap {
            binding_id: "binding_1".to_string(),
            im_conversation_id: "conv_a".to_string(),
            im_conversation_type: "2".to_string(),
            session_scope: SessionScope::Conversation,
            im_user_id: None,
            bcs_session_id: "group_1:00000001".to_string(),
            last_active_at: 1,
        })
        .await?;

    let (result, logs) = capture_tracing_logs(async {
        harness
            .service
            .try_outbound(outbound("group_1:00000001", ParticipantRole::Worker, false))
            .await
    })
    .await;
    result?;

    for expected in [
        "channel outbound: selected",
        "channel outbound: delivered",
        "binding_id=binding_1",
        "bcs_session_id=group_1:00000001",
        "run_id=run_1",
        "im_conversation_id=conv_a",
    ] {
        assert!(
            logs.contains(expected),
            "expected log fragment {expected:?}, got:\n{logs}"
        );
    }

    Ok(())
}

#[tokio::test]
async fn try_outbound_uses_session_mapping_instead_of_listing_bindings() -> TestResult {
    let harness = TestHarness::new_without_binding_list(manager_group("group_1")).await?;
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
    harness
        .session_repo
        .create(
            "group_1",
            NewSessionParams {
                id: Some("group_1:00000001".to_string()),
                session_kind: SessionKind::Chat,
                ..Default::default()
            },
        )
        .await?;
    harness
        .conversation_repo
        .upsert(bcs_domain::ConversationSessionMap {
            binding_id: "binding_1".to_string(),
            im_conversation_id: "conv_a".to_string(),
            im_conversation_type: "2".to_string(),
            session_scope: SessionScope::Conversation,
            im_user_id: None,
            bcs_session_id: "group_1:00000001".to_string(),
            last_active_at: 1,
        })
        .await?;

    harness
        .service
        .try_outbound(outbound("group_1:00000001", ParticipantRole::Worker, false))
        .await?;

    let events = harness.delivery.events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].im_conversation_id, "conv_a");

    Ok(())
}

#[tokio::test(flavor = "current_thread")]
async fn try_outbound_logs_delivery_error_detail_when_not_confirmed() -> TestResult {
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
    harness
        .session_repo
        .create(
            "group_1",
            NewSessionParams {
                id: Some("group_1:00000001".to_string()),
                session_kind: SessionKind::Chat,
                ..Default::default()
            },
        )
        .await?;
    harness
        .conversation_repo
        .upsert(bcs_domain::ConversationSessionMap {
            binding_id: "binding_1".to_string(),
            im_conversation_id: "conv_a".to_string(),
            im_conversation_type: "2".to_string(),
            session_scope: SessionScope::Conversation,
            im_user_id: None,
            bcs_session_id: "group_1:00000001".to_string(),
            last_active_at: 1,
        })
        .await?;
    *harness.delivery.fail_error.lock().await = Some(
        "dingtalk normal message send returned status 400 code=invalidConversation".to_string(),
    );

    let (result, logs) = capture_tracing_logs(async {
        harness
            .service
            .try_outbound(outbound("group_1:00000001", ParticipantRole::Worker, false))
            .await
    })
    .await;
    result?;

    assert!(
        logs.contains("channel outbound: delivery not confirmed"),
        "expected delivery failure log, got:\n{logs}"
    );
    assert!(
        logs.contains("invalidConversation"),
        "expected delivery error detail in log, got:\n{logs}"
    );

    Ok(())
}

#[tokio::test]
async fn try_outbound_continues_after_delivery_call_error() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .binding_repo
        .create(active_binding(
            "binding_failed",
            "robot_failed",
            BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            Visibility::FullTranscript,
        ))
        .await?;
    harness
        .binding_repo
        .create(active_binding(
            "binding_healthy",
            "robot_healthy",
            BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            Visibility::FullTranscript,
        ))
        .await?;
    harness
        .session_repo
        .create(
            "group_1",
            NewSessionParams {
                id: Some("group_1:00000001".to_string()),
                session_kind: SessionKind::Chat,
                ..Default::default()
            },
        )
        .await?;
    for (binding_id, conversation_id) in [
        ("binding_failed", "conv_failed"),
        ("binding_healthy", "conv_healthy"),
    ] {
        harness
            .conversation_repo
            .upsert(bcs_domain::ConversationSessionMap {
                binding_id: binding_id.to_string(),
                im_conversation_id: conversation_id.to_string(),
                im_conversation_type: "2".to_string(),
                session_scope: SessionScope::Conversation,
                im_user_id: None,
                bcs_session_id: "group_1:00000001".to_string(),
                last_active_at: 1,
            })
            .await?;
    }
    *harness.delivery.call_error_account_ref.lock().await = Some("robot_failed".to_string());

    harness
        .service
        .try_outbound(outbound("group_1:00000001", ParticipantRole::Worker, false))
        .await?;

    let events = harness.delivery.events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].binding_ref.account_ref, "robot_healthy");

    Ok(())
}

#[tokio::test]
async fn try_outbound_skips_when_session_does_not_belong_to_group() -> TestResult {
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
    harness
        .session_repo
        .create(
            "other_group",
            NewSessionParams {
                id: Some("other_group:00000001".to_string()),
                session_kind: SessionKind::Chat,
                ..Default::default()
            },
        )
        .await?;
    harness
        .conversation_repo
        .upsert(bcs_domain::ConversationSessionMap {
            binding_id: "binding_1".to_string(),
            im_conversation_id: "conv_a".to_string(),
            im_conversation_type: "2".to_string(),
            session_scope: SessionScope::Conversation,
            im_user_id: None,
            bcs_session_id: "other_group:00000001".to_string(),
            last_active_at: 1,
        })
        .await?;

    harness
        .service
        .try_outbound(outbound(
            "other_group:00000001",
            ParticipantRole::Worker,
            false,
        ))
        .await?;

    assert!(harness.delivery.events.lock().await.is_empty());

    Ok(())
}
