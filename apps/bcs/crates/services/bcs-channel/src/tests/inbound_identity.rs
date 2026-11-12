use super::*;

#[tokio::test]
async fn inbound_uses_resolved_human_but_preserves_native_sender() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness.provider.inbound_user_ids.lock().unwrap()
        .insert("000123".into(), "123".into());
    harness.service.create_binding(CreateBindingCommand {
        channel_type: channel_type(), account_ref: "robot_1".into(),
        target: BindingTarget::Group { group_id: "group_1".into() },
        group_chat_scope: Some(GroupChatScope::PerSender),
        outbound_visibility: Visibility::FullTranscript, env: "dev".into(),
        created_by: None, config: dingtalk_config("robot_1"),
    }).await?;
    let mut message = inbound("conv_a", "000123", Some("User"), "msg_a");
    message.conversation_type = "2".into();
    harness.service.handle_inbound(message).await?;
    assert_eq!(*harness.registry.ensured.lock().await,
        vec![("123".to_string(), "User".to_string())]);
    let mapping = harness.participant_repo.get(channel_type(), "robot_1", "000123")
        .await?.expect("native sender mapping");
    assert_eq!(mapping.actor_id, "human_123");
    let conversation = harness.conversation_repo.get("generated_id", "conv_a",
        SessionScope::PerSender, Some("000123")).await?.expect("native conversation");
    let added = harness.session_repo.added_participants.lock().await;
    assert_eq!(added[0].0, conversation.bcs_session_id);
    assert_eq!(added[0].1.bot_uuid, "human_123");
    assert_eq!(harness.message_flow.web_sends.lock().await[0].from_actor_id, "human_123");
    Ok(())
}

#[tokio::test]
async fn empty_resolved_user_never_creates_a_human() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness.provider.inbound_user_ids.lock().unwrap().insert("native".into(), "".into());
    let result = harness.service.ensure_im_human_actor(
        &inbound("conv", "native", None, "msg")).await;
    assert!(result.is_err());
    assert!(harness.registry.ensured.lock().await.is_empty());
    Ok(())
}
