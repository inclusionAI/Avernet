use super::*;

#[tokio::test]
async fn async_run_after_last_tool_call_mode_returns_only_followup_text() {
    let (service, run_port, _run_store) = build_run_service(
        vec![
            chat_event("delta", "analysis before tool. "),
            tool_call_event("tool_call_start"),
            chat_event("delta", "tool streaming detail. "),
            tool_call_event("tool_call_end"),
            chat_event("delta", "answer "),
            chat_event("final", "after tool"),
        ],
        false,
    )
    .await;

    service
        .start_async_chat(AsyncA2aChatCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            target_bot_id: "bot-target".to_string(),
            message: "hello".to_string(),
            from_actor_id: Some("api-user".to_string()),
            run_channel_from: Some("api-user".to_string()),
            authenticated_staff_id: Some("owner-1".to_string()),
            tags: Vec::new(),
            run_id: "async-after-tool-run".to_string(),
            session_key: "async-after-tool-session".to_string(),
            timeout_ms: 1_000,
            client: Some("contract-test".to_string()),
            response_mode: ChatResponseMode::AfterLastToolCall,
            caller_wait_mode: None,
            organization_code: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();
    run_port.wait_for_event_unregister("async-after-tool-run").await;

    let status = A2aChatRunService::get_run(
        service.as_ref(),
        ChatRunQueryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            run_id: "async-after-tool-run".to_string(),
            wait_ms: 0,
            since_version: 0,
        },
    )
    .await
    .unwrap();

    assert_eq!(status.status, "completed");
    assert_eq!(
        status
            .response
            .unwrap()
            .get("content")
            .and_then(|v| v.as_str()),
        Some("answer after tool")
    );
}
#[tokio::test]
async fn async_run_after_last_tool_call_mode_uses_agent_tool_boundary() {
    let (service, run_port, _run_store) = build_run_service(
        vec![
            chat_event("delta", "analysis before tool. "),
            agent_tool_event(),
            chat_event("delta", "answer after tool"),
            chat_event("final", "analysis before tool. answer after tool"),
        ],
        false,
    )
    .await;

    service
        .start_async_chat(AsyncA2aChatCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            target_bot_id: "bot-target".to_string(),
            message: "hello".to_string(),
            from_actor_id: Some("api-user".to_string()),
            run_channel_from: Some("api-user".to_string()),
            authenticated_staff_id: Some("owner-1".to_string()),
            tags: Vec::new(),
            run_id: "async-agent-tool-run".to_string(),
            session_key: "async-agent-tool-session".to_string(),
            timeout_ms: 1_000,
            client: Some("contract-test".to_string()),
            response_mode: ChatResponseMode::AfterLastToolCall,
            caller_wait_mode: None,
            organization_code: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();
    run_port.wait_for_event_unregister("async-agent-tool-run").await;

    let status = A2aChatRunService::get_run(
        service.as_ref(),
        ChatRunQueryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            run_id: "async-agent-tool-run".to_string(),
            wait_ms: 0,
            since_version: 0,
        },
    )
    .await
    .unwrap();

    assert_eq!(status.status, "completed");
    assert_eq!(
        status
            .response
            .unwrap()
            .get("content")
            .and_then(|v| v.as_str()),
        Some("answer after tool")
    );
}
#[tokio::test]
async fn async_run_after_last_tool_call_mode_uses_final_when_agent_tool_has_no_followup_text() {
    let (service, run_port, _run_store) = build_run_service(
        vec![
            chat_event("delta", "analysis before tool. "),
            agent_tool_event(),
            chat_event("final", "analysis before tool."),
        ],
        false,
    )
    .await;

    service
        .start_async_chat(AsyncA2aChatCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            target_bot_id: "bot-target".to_string(),
            message: "hello".to_string(),
            from_actor_id: Some("api-user".to_string()),
            run_channel_from: Some("api-user".to_string()),
            authenticated_staff_id: Some("owner-1".to_string()),
            tags: Vec::new(),
            run_id: "async-agent-tool-final-run".to_string(),
            session_key: "async-agent-tool-final-session".to_string(),
            timeout_ms: 1_000,
            client: Some("contract-test".to_string()),
            response_mode: ChatResponseMode::AfterLastToolCall,
            caller_wait_mode: None,
            organization_code: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();
    run_port.wait_for_event_unregister("async-agent-tool-final-run").await;

    let status = A2aChatRunService::get_run(
        service.as_ref(),
        ChatRunQueryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            run_id: "async-agent-tool-final-run".to_string(),
            wait_ms: 0,
            since_version: 0,
        },
    )
    .await
    .unwrap();

    assert_eq!(status.status, "completed");
    assert_eq!(
        status
            .response
            .unwrap()
            .get("content")
            .and_then(|v| v.as_str()),
        Some("analysis before tool.")
    );
}
#[test]
fn a2a_event_parser_matches_existing_chat_event_shapes() {
    let raw = serde_json::json!({
        "type": "event",
        "event": "chat.event",
        "payload": {
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done"}]
            }
        }
    })
    .to_string();

    let mut accumulated = String::new();
    assert_eq!(
        drain_chat_event(&raw, &mut accumulated),
        DrainOutcome::Final
    );
    assert_eq!(accumulated, "done");
}
#[test]
fn a2a_event_parser_treats_final_as_snapshot_for_full_response() {
    let mut accumulated = String::new();
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "hello"),
            &mut accumulated,
            ChatResponseMode::Full,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("final", "hello"),
            &mut accumulated,
            ChatResponseMode::Full,
        ),
        DrainOutcome::Final
    );

    assert_eq!(accumulated, "hello");
}
#[test]
fn a2a_event_parser_treats_block_joined_final_as_snapshot_for_full_response() {
    let mut accumulated = String::new();
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "visible part 1"),
            &mut accumulated,
            ChatResponseMode::Full,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "visible part 2"),
            &mut accumulated,
            ChatResponseMode::Full,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("final", "visible part 1\n\nvisible part 2"),
            &mut accumulated,
            ChatResponseMode::Full,
        ),
        DrainOutcome::Final
    );

    assert_eq!(accumulated, "visible part 1\n\nvisible part 2");
}
#[test]
fn a2a_event_parser_keeps_after_tool_window_when_final_snapshot_is_full_response() {
    let mut accumulated = String::new();
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "analysis before tool. "),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &tool_call_event("tool_call_start"),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "answer after tool"),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("final", "analysis before tool. answer after tool"),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Final
    );

    assert_eq!(accumulated, "answer after tool");
}
#[test]
fn a2a_event_parser_clears_after_agent_tool_event() {
    let mut accumulated = String::new();
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "analysis before tool. "),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &agent_tool_event(),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "answer after tool"),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("final", "analysis before tool. answer after tool"),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Final
    );

    assert_eq!(accumulated, "answer after tool");
}
#[test]
fn a2a_event_parser_deduplicates_repeated_after_tool_delta_before_final_snapshot() {
    let mut accumulated = String::new();
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "analysis before tool. "),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &agent_tool_event(),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "answer after tool"),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "answer after tool"),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("final", "analysis before tool. answer after tool"),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Final
    );

    assert_eq!(accumulated, "answer after tool");
}
