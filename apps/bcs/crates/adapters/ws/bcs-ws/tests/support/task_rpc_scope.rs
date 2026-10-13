use super::*;
use serde_json::Value;

async fn captured_scope(state: &TestState, method: &str) -> (String, Value) {
    match method {
        "task.dispatch" => {
            let commands = state.message_flow.task_dispatches.lock().await;
            assert_eq!(commands.len(), 1);
            (commands[0].group_id.clone(), commands[0].payload.clone())
        }
        "task.message" => {
            let commands = state.message_flow.task_messages.lock().await;
            assert_eq!(commands.len(), 1);
            (commands[0].group_id.clone(), commands[0].payload.clone())
        }
        _ => {
            let commands = state.message_flow.task_completes.lock().await;
            assert_eq!(commands.len(), 1);
            (commands[0].task_id.clone(), commands[0].payload.clone())
        }
    }
}

#[tokio::test]
async fn task_rpcs_preserve_explicit_and_legacy_session_scope_on_v2_and_v3() {
    for version in [2, 3] {
        for method in ["task.dispatch", "task.message", "task.complete"] {
            let cases = [
                ("group-1", "bcs_session_id", "group-1:explicit", Some("group-1:explicit")),
                ("group-1", "session_id", "group-1:explicit", Some("group-1:explicit")),
                ("group-1", "sessionId", "group-1:explicit", Some("group-1:explicit")),
                ("group-1", "bcs_session_id", "opaque-session", Some("opaque-session")),
                ("group-1:legacy", "", "", Some("group-1:legacy")),
                ("group-1:legacy", "session_id", "group-1", Some("group-1:legacy")),
                ("group-1:legacy", "bcs_session_id", "group-1:explicit", Some("group-1:explicit")),
                ("group-1", "", "", None),
            ];
            for (group, field, session, expected) in cases {
                let state = new_state();
                let (tx, mut rx) = mpsc::channel(8);
                state.dispatch_state.bot_connections
                    .connect_with_protocol("bot-driver".into(), tx.clone(), version, None).await;
                let mut bot = Some("bot-driver".to_string());
                let mut params = serde_json::json!({
                    "group_id": group, "target_bot": "bot-worker", "message": "work", "summary": "done"
                });
                if !field.is_empty() { params[field] = Value::String(session.into()); }
                let frame = BcsFrame::Request(RequestFrame::new("task-scope", method, Some(params)));
                dispatch_frame(&state.dispatch_state, &serde_json::to_string(&frame).unwrap(), &tx, &mut bot)
                    .await.unwrap();
                assert!(recv_response(&mut rx).await.ok, "{method} v{version}");
                let (group, payload) = captured_scope(&state, method).await;
                assert_eq!(group, "group-1");
                assert_eq!(payload.get("bcs_session_id").and_then(Value::as_str), expected,
                    "{method} v{version}, field={field}");
            }
        }
    }
}

#[tokio::test]
async fn task_rpcs_keep_null_session_legacy_fallback_and_reject_duplicate_aliases() {
    for version in [2, 3] {
        for method in ["task.dispatch", "task.message", "task.complete"] {
            for duplicate in [false, true] {
                let state = new_state();
                let (tx, mut rx) = mpsc::channel(8);
                state.dispatch_state.bot_connections
                    .connect_with_protocol("bot-driver".into(), tx.clone(), version, None).await;
                let mut bot = Some("bot-driver".to_string());
                let mut params = serde_json::json!({
                    "group_id": "group-1:legacy", "bcs_session_id": null,
                    "target_bot": "bot-worker", "message": "work", "summary": "done"
                });
                if duplicate { params["session_id"] = Value::String("group-1:explicit".into()); }
                let frame = BcsFrame::Request(RequestFrame::new("task-scope-alias", method, Some(params)));
                let result = dispatch_frame(&state.dispatch_state, &serde_json::to_string(&frame).unwrap(), &tx, &mut bot).await;
                if duplicate {
                    assert!(matches!(result, Err(BotWsDispatchError::InvalidFrameFormat(_))));
                    assert!(state.message_flow.task_dispatches.lock().await.is_empty());
                    assert!(state.message_flow.task_messages.lock().await.is_empty());
                    assert!(state.message_flow.task_completes.lock().await.is_empty());
                } else {
                    result.unwrap();
                    assert!(recv_response(&mut rx).await.ok);
                    let (group, payload) = captured_scope(&state, method).await;
                    assert_eq!(group, "group-1");
                    assert_eq!(payload["bcs_session_id"], "group-1:legacy");
                }
            }
        }
    }
}

#[tokio::test]
async fn task_rpcs_reject_cross_group_and_empty_explicit_session_before_application() {
    for version in [2, 3] {
        for method in ["task.dispatch", "task.message", "task.complete"] {
            for session in ["other-group:session", ""] {
                let state = new_state();
                let (tx, _rx) = mpsc::channel(8);
                state.dispatch_state.bot_connections
                    .connect_with_protocol("bot-driver".into(), tx.clone(), version, None).await;
                let mut bot = Some("bot-driver".to_string());
                let frame = BcsFrame::Request(RequestFrame::new("invalid-task-scope", method,
                    Some(serde_json::json!({
                        "group_id": "group-1", "bcs_session_id": session,
                        "target_bot": "bot-worker", "message": "work", "summary": "done"
                    }))));
                let error = dispatch_frame(&state.dispatch_state, &serde_json::to_string(&frame).unwrap(), &tx, &mut bot)
                    .await.expect_err("invalid task scope must be rejected");
                assert!(matches!(error, BotWsDispatchError::InvalidFrameFormat(_)));
                assert!(state.message_flow.task_dispatches.lock().await.is_empty());
                assert!(state.message_flow.task_messages.lock().await.is_empty());
                assert!(state.message_flow.task_completes.lock().await.is_empty());
            }
        }
    }
}
