use super::*;
use bcs_service_api::application::caller_execution::ExecutionToken;

fn binding_command(target: BindingTarget, caller: bool) -> CreateBindingCommand {
    let mut config = dingtalk_config("robot_1");
    if caller { config["execution_mode"] = serde_json::json!("caller"); }
    CreateBindingCommand {
        channel_type: channel_type(), account_ref: "robot_1".into(), target,
        group_chat_scope: None, outbound_visibility: Visibility::FullTranscript,
        env: "dev".into(), created_by: Some("creator".into()), config,
    }
}

#[tokio::test]
async fn caller_binding_create_update_and_runtime_reject_group() -> TestResult {
    let harness = TestHarness::new(chat_group("group_1")).await?;
    let group = BindingTarget::Group { group_id: "group_1".into() };
    assert!(harness.service.create_binding(binding_command(group.clone(), true)).await.is_err());
    let binding = harness.service.create_binding(binding_command(group, false)).await?;
    let mut config = binding.config.clone();
    config["execution_mode"] = serde_json::json!("caller");
    assert!(harness.service.update_binding_config(&binding.id, config.clone()).await.is_err());
    // Bypass the management API to simulate malformed existing configuration.
    harness.binding_repo.set_config(&binding.id, config).await?;
    let mut message = inbound("conv", "u1", None, "msg");
    message.execution_token = Some(ExecutionToken::new("test-token".into(), "iam-token".into()));
    let error = harness.service.handle_inbound(message).await.unwrap_err();
    assert_eq!(error.kind, ChannelInboundFailureKind::CallerExecutionUnavailable);
    assert!(harness.message_flow.web_sends.lock().await.is_empty());
    Ok(())
}

#[tokio::test]
async fn caller_bot_binding_supports_dm_and_at_group_and_does_not_change_default() -> TestResult {
    for group_chat in [false, true] {
        for caller in [false, true] {
            let harness = TestHarness::new(chat_group("group_1")).await?;
            let binding = harness.service.create_binding(binding_command(
                BindingTarget::Bot { bot_id: "target_bot".into() }, caller)).await?;
            let mut message = if group_chat { group_inbound("conv", "u1", None, "msg", true) }
                else { inbound("conv", "u1", None, "msg") };
            message.is_at_bot = group_chat;
            message.execution_token = Some(ExecutionToken::new("test-token".into(), "iam-token".into()));
            harness.service.handle_inbound(message).await?;
            let sends = harness.message_flow.web_sends.lock().await;
            assert_eq!(sends.len(), 1);
            assert_eq!(sends[0].caller_execution.is_some(), caller);
            if let Some(context) = &sends[0].caller_execution {
                assert_eq!(context.token.expose(), "test-token");
                assert_eq!(context.user_id, "u1");
                assert_eq!(context.binding_id, binding.id);
                assert_eq!(context.target_bot_id, "target_bot");
                assert_eq!(context.source_message_id, "msg");
            }
            assert!(!format!("{:?}", sends[0]).contains("test-token"));
        }
    }
    Ok(())
}

#[tokio::test]
async fn caller_missing_token_rejects_send_but_not_local_new_command() -> TestResult {
    let harness = TestHarness::new(chat_group("group_1")).await?;
    harness.service.create_binding(binding_command(BindingTarget::Bot { bot_id: "target_bot".into() }, true)).await?;
    for (id, value) in [("missing", None), ("empty", Some("   "))] {
        let mut message = inbound("conv", "u1", None, id);
        message.execution_token = value.map(|v| ExecutionToken::new(v.into(), "iam-token".into()));
        assert!(harness.service.handle_inbound(message).await.is_err());
    }
    harness.service.handle_inbound(new_command("conv", "u1", "new")).await?;
    assert!(harness.message_flow.web_sends.lock().await.is_empty());
    Ok(())
}
