use super::*;

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_manager_assign_task_dispatches_once_and_dedups() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "do worker task"
        }),
    );
    let frame = coordination_echo_frame("exec", "result", "fc-1", &echo, false);
    let text = serde_json::to_string(&frame).unwrap();

    dispatch_frame(
        &state.dispatch_state,
        &text,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();
    dispatch_frame(
        &state.dispatch_state,
        &text,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let commands = state.message_flow.task_dispatches.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].driver_bot_id, "bot-manager");
    assert_eq!(commands[0].group_id, "group-1");
    assert_eq!(commands[0].target_bot_id, "bot-worker");
    assert_eq!(commands[0].target_bot_name, None);
    assert_eq!(commands[0].payload["message"], "do worker task");
    assert_eq!(commands[0].payload["bcs_session_id"], "group-1:abcdef12");
    let bot_events = state.message_flow.bot_events.lock().await;
    assert_eq!(bot_events.len(), 1);
    assert_eq!(bot_events[0].event_type, "agent");
    assert_eq!(bot_events[0].event_payload["stream"], "tool");
    assert_eq!(bot_events[0].event_payload["data"]["phase"], "result");
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_manager_assign_task_forwards_response_mode() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "do worker task",
            "response_mode": "after-last-tool-call"
        }),
    );
    let frame = coordination_echo_frame("exec", "result", "fc-response-mode", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let commands = state.message_flow.task_dispatches.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].payload["response_mode"], "after-last-tool-call");
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_manager_assign_task_error_notifies_manager() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    *state.message_flow.task_dispatch_error.lock().await = Some(ServiceError::InvalidOperation {
        message: "target bot is muted".to_string(),
        request_id: None,
    });
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "do worker task"
        }),
    );
    let frame = coordination_echo_frame("exec", "result", "fc-error-notify", &echo, false);
    let text = serde_json::to_string(&frame).unwrap();

    dispatch_frame(
        &state.dispatch_state,
        &text,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let notifications = state.system_message.notifications.lock().await;
    assert_eq!(notifications.len(), 1);
    assert_eq!(notifications[0].group_id, "group-1");
    assert_eq!(notifications[0].session_id, "group-1:abcdef12");
    assert_eq!(notifications[0].participants.len(), 2);
    match &notifications[0].event {
        SystemMessageEvent::GenericNotification {
            group_id,
            message,
            receivers,
        } => {
            assert_eq!(group_id, "group-1");
            assert!(message.contains("[协同提醒]"));
            assert!(message.contains("target bot is muted"));
            assert_eq!(receivers.len(), 1);
            assert_eq!(receivers[0].bot_uuid, "bot-manager");
        }
        other => panic!("expected GenericNotification, got {other:?}"),
    }
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_manager_assign_task_whitespace_message_does_not_dispatch() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "   "
        }),
    );
    let frame = coordination_echo_frame("exec", "result", "fc-blank-assign", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_dispatches.lock().await.is_empty());
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_tool_start_phase_does_not_dispatch() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "do worker task"
        }),
    );
    let frame = coordination_echo_frame("exec", "start", "fc-start", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_dispatches.lock().await.is_empty());
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_non_exec_tool_name_does_not_dispatch() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "do worker task"
        }),
    );
    let frame = coordination_echo_frame("read_file", "result", "fc-read", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_dispatches.lock().await.is_empty());
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_worker_cannot_assign_task() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-worker".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "do worker task"
        }),
    );
    let frame = coordination_echo_frame("exec", "result", "fc-worker-assign", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_dispatches.lock().await.is_empty());
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_manager_send_task_message_notifies_manager_and_keeps_result_event() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_send_task_message",
        serde_json::json!({
            "message": "manager progress"
        }),
    );
    let frame = coordination_echo_frame("shell", "result", "fc-manager-message", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_messages.lock().await.is_empty());
    let notifications = state.system_message.notifications.lock().await;
    assert_eq!(notifications.len(), 1);
    assert_eq!(notifications[0].group_id, "group-1");
    assert_eq!(notifications[0].session_id, "group-1:abcdef12");
    match &notifications[0].event {
        SystemMessageEvent::GenericNotification {
            group_id,
            message,
            receivers,
        } => {
            assert_eq!(group_id, "group-1");
            assert!(message.contains("[协同提醒]"));
            assert!(message.contains("only worker bot can send task messages"));
            assert_eq!(receivers.len(), 1);
            assert_eq!(receivers[0].bot_uuid, "bot-manager");
        }
        other => panic!("expected GenericNotification, got {other:?}"),
    }
    let bot_events = state.message_flow.bot_events.lock().await;
    assert_eq!(bot_events.len(), 1);
    assert_eq!(bot_events[0].event_type, "agent");
    assert_eq!(bot_events[0].event_payload["stream"], "tool");
    assert_eq!(bot_events[0].event_payload["data"]["phase"], "result");
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_worker_send_task_message_dispatches_with_session() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-worker".to_string());
    let echo = coordination_echo(
        "bcs_send_task_message",
        serde_json::json!({
            "message": "worker progress"
        }),
    );
    let frame = coordination_echo_frame("shell", "result", "fc-message", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let commands = state.message_flow.task_messages.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].worker_bot_id, "bot-worker");
    assert_eq!(commands[0].group_id, "group-1");
    assert_eq!(commands[0].payload["message"], "worker progress");
    assert_eq!(commands[0].payload["bcs_session_id"], "group-1:abcdef12");
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_worker_send_task_message_empty_message_does_not_dispatch() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-worker".to_string());
    let echo = coordination_echo(
        "bcs_send_task_message",
        serde_json::json!({
            "message": ""
        }),
    );
    let frame = coordination_echo_frame("shell", "result", "fc-blank-message", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_messages.lock().await.is_empty());
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_worker_send_task_message_without_real_session_does_not_dispatch() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-worker".to_string());
    let echo = coordination_echo(
        "bcs_send_task_message",
        serde_json::json!({
            "message": "worker progress"
        }),
    );
    let frame = BcsFrame::Event(EventFrame::new(
        "agent",
        Some(serde_json::json!({
            "run_id": "run-1",
            "bcs_group_id": "group-1",
            "stream": "tool",
            "ts": 123,
            "data": {
                "name": "shell",
                "phase": "result",
                "toolCallId": "fc-message-no-session",
                "isError": false,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": echo
                        }
                    ]
                }
            }
        })),
        Some(1),
    ));

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_messages.lock().await.is_empty());
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_manager_task_complete_dispatches_via_echo() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_task_complete",
        serde_json::json!({
            "summary": "all done"
        }),
    );
    let frame = coordination_echo_frame("mcporter", "result", "fc-complete", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let commands = state.message_flow.task_completes.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].task_id, "group-1");
    assert_eq!(commands[0].bot_id, "bot-manager");
    assert!(commands[0].via_echo);
    assert_eq!(commands[0].payload["group_id"], "group-1");
    assert_eq!(commands[0].payload["summary"], "all done");
    assert_eq!(commands[0].payload["status"], "completed");
    assert_eq!(commands[0].payload["bcs_session_id"], "group-1:abcdef12");
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_manager_task_complete_whitespace_summary_does_not_dispatch() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_task_complete",
        serde_json::json!({
            "summary": "   "
        }),
    );
    let frame = coordination_echo_frame("mcporter", "result", "fc-blank-complete", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_completes.lock().await.is_empty());
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_error_result_does_not_dispatch() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "do worker task"
        }),
    );
    let frame = coordination_echo_frame("exec", "result", "fc-error", &echo, true);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_dispatches.lock().await.is_empty());
}

#[tokio::test]
async fn chat_event_with_tool_like_coordination_payload_is_not_coordination_echo() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "do worker task"
        }),
    );
    let event = BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::json!({
            "run_id": "run-chat-spoof",
            "bcs_group_id": "group-1:abcdef12",
            "state": WireChatEventState::Final,
            "stream": "tool",
            "ts": 123,
            "data": {
                "name": "exec",
                "phase": "result",
                "toolCallId": "fc-chat-spoof",
                "isError": false,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": echo
                        }
                    ]
                }
            }
        })),
        Some(1),
    ));

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_dispatches.lock().await.is_empty());
    let events = state.message_flow.bot_events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].bot_id, "bot-manager");
    assert_eq!(events[0].run_id, "run-chat-spoof");
    assert_eq!(events[0].group_id, "group-1");
    assert_eq!(events[0].state, ChatEventState::Final);
    assert_eq!(events[0].bcs_session_id.as_deref(), Some("group-1:abcdef12"));
}
