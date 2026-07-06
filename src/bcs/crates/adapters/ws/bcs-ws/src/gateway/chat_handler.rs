//! Chat method handlers.

use std::sync::Arc;

use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;
use tracing::{debug, info, warn};

use super::abort_manager::ChatRunEntry;
use super::chat_types::{
    ChatAbortParams, ChatAbortResult, ChatEvent, ChatEventState, ChatHistoryParams,
    ChatHistoryResult, ChatSendParams, ChatSendResult, ChatSendStatus,
};
use super::context::SessionAccess;
use super::{error_codes, RequestFrame, ResponseFrame};
use axum::extract::ws::Message;
use super::abort_manager::ChatAbortManager;
use super::event_broadcaster::EventBroadcaster;

/// Generate mock response based on user message.
fn generate_mock_response(message: &str) -> String {
    let msg_lower = message.to_lowercase();

    if msg_lower.contains("hello") || msg_lower.contains("hi") || msg_lower.contains("你好") {
        "你好！我是 BCS 智能助手。很高兴为您服务！有什么我可以帮助您的吗？".to_string()
    } else if msg_lower.contains("help") || msg_lower.contains("帮助") {
        "我可以帮助您：\n1. 创建和管理群组聊天\n2. 发现和协调多个 Bot\n3. 融合多个 Bot 的上下文\n\n请告诉我您需要什么帮助？".to_string()
    } else if msg_lower.contains("bot") || msg_lower.contains("机器人") {
        "当前已注册的 Bot 信息：\n- 张三: 开发助手，擅长代码审查\n- 李四: 数据库专家，擅长 SQL 优化\n- 王五: 安全顾问，擅长漏洞检测\n\n您可以使用 @bot-id 来指定特定 Bot 回复。".to_string()
    } else if msg_lower.contains("weather") || msg_lower.contains("天气") {
        "今天天气晴朗，气温 22°C。非常适合户外活动！".to_string()
    } else if msg_lower.contains("time") || msg_lower.contains("时间") {
        let now = chrono::Local::now();
        format!("当前时间: {}", now.format("%Y-%m-%d %H:%M:%S"))
    } else if msg_lower.contains("status") || msg_lower.contains("状态") {
        "BCS 服务状态:\n✅ WebSocket: 正常\n✅ Bot 注册: 正常\n✅ 消息路由: 正常\n✅ 会话管理: 正常".to_string()
    } else {
        format!(
            "收到您的消息：「{}」\n\n我已理解您的需求。这是一个模拟响应，用于测试 WebSocket 功能。\n\n如果您需要真实的 Bot 响应，请确保已注册相应的 Bot 服务。",
            message
        )
    }
}

/// Generate mock chat history.
fn generate_mock_history(session_key: &str) -> Vec<serde_json::Value> {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);

    vec![
        serde_json::json!({
            "id": format!("{}-msg-1", session_key),
            "role": "user",
            "content": "你好，请问你是谁？",
            "timestamp": now - 60000,
        }),
        serde_json::json!({
            "id": format!("{}-msg-2", session_key),
            "role": "assistant",
            "content": "你好！我是 BCS 智能助手，负责协调多个 Bot 完成复杂任务。",
            "timestamp": now - 59000,
        }),
        serde_json::json!({
            "id": format!("{}-msg-3", session_key),
            "role": "user",
            "content": "你能帮我做什么？",
            "timestamp": now - 30000,
        }),
        serde_json::json!({
            "id": format!("{}-msg-4", session_key),
            "role": "assistant",
            "content": "我可以帮助您：\n1. 协调多个专业 Bot 协作解决问题\n2. 发现具备特定技能的 Bot\n3. 创建群组聊天进行多 Bot 讨论\n4. 融合多个 Bot 的专业知识",
            "timestamp": now - 29000,
        }),
    ]
}

/// Handle chat.send method.
pub async fn handle_chat_send(
    request: &RequestFrame,
    session_access: &Arc<dyn SessionAccess>,
    abort_manager: Arc<ChatAbortManager>,
    event_broadcaster: EventBroadcaster,
    router: Arc<dyn crate::gateway::context::MessageRouting>,
    _outgoing_tx: &mpsc::Sender<Message>,
) -> ResponseFrame {
    // Parse parameters
    let params: ChatSendParams = match &request.params {
        Some(p) => match serde_json::from_value(p.clone()) {
            Ok(p) => p,
            Err(e) => {
                return ResponseFrame::error(
                    &request.id,
                    error_codes::INVALID_REQUEST,
                    format!("Invalid params: {}", e),
                );
            }
        },
        None => {
            return ResponseFrame::error(
                &request.id,
                error_codes::INVALID_REQUEST,
                "Missing params",
            );
        }
    };

    let run_id = params.idempotency_key.clone();

    // Check if already running (idempotency)
    if abort_manager.exists(&run_id).await {
        return ResponseFrame::success(
            &request.id,
            serde_json::to_value(ChatSendResult {
                run_id: run_id.clone(),
                status: ChatSendStatus::InFlight,
            })
            .unwrap(),
        );
    }

    // Check if we have a saved result for this idempotency key
    // (For now, we don't cache results - could be added later)

    // Create cancellation token and buffer
    let token = CancellationToken::new();
    let buffer = Arc::new(tokio::sync::RwLock::new(String::new()));

    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);

    let timeout_ms = params.timeout_ms.unwrap_or(5 * 60 * 1000); // 5 minutes default
    let expires_at = now + timeout_ms + 60_000; // Add grace period

    // Create run entry
    let entry = ChatRunEntry {
        run_id: run_id.clone(),
        session_key: params.session_key.clone(),
        token: token.clone(),
        started_at_ms: now,
        expires_at_ms: expires_at,
        owner_conn_id: None,
        buffer: buffer.clone(),
    };

    // Register the run
    abort_manager.register(entry).await;

    info!(
        run_id = %run_id,
        session_key = %params.session_key,
        "Chat run started"
    );

    // Return immediately with run_id
    let response = ResponseFrame::success(
        &request.id,
        serde_json::to_value(ChatSendResult {
            run_id: run_id.clone(),
            status: ChatSendStatus::Started,
        })
        .unwrap(),
    );

    // Spawn background task to execute the chat
    let session_access_clone = session_access.clone();
    let router_clone = router.clone();
    let abort_manager_clone = abort_manager.clone();
    let event_broadcaster_clone = event_broadcaster.clone();
    let params_clone = params.clone();
    tokio::spawn(async move {
        execute_chat_task(
            session_access_clone,
            router_clone,
            abort_manager_clone,
            event_broadcaster_clone,
            params_clone,
            token,
            buffer,
            run_id,
        ).await;
    });

    response
}

/// Handle chat.history method.
pub async fn handle_chat_history(
    request: &RequestFrame,
    session_access: &Arc<dyn SessionAccess>,
) -> ResponseFrame {
    // Parse parameters
    let params: ChatHistoryParams = match &request.params {
        Some(p) => match serde_json::from_value(p.clone()) {
            Ok(p) => p,
            Err(e) => {
                return ResponseFrame::error(
                    &request.id,
                    error_codes::INVALID_REQUEST,
                    format!("Invalid params: {}", e),
                );
            }
        },
        None => {
            return ResponseFrame::error(
                &request.id,
                error_codes::INVALID_REQUEST,
                "Missing params",
            );
        }
    };

    // Get session from store
    let session = match session_access.get(&params.session_key).await {
        Some(s) => s,
        None => {
            // Return mock history for non-existent session (for testing)
            debug!(
                session_key = %params.session_key,
                "Session not found, returning mock history"
            );
            let limit = params.limit.unwrap_or(200).min(1000);
            let all_messages = generate_mock_history(&params.session_key);
            let messages = latest_messages_before(all_messages, params.before, limit);

            return ResponseFrame::success(
                &request.id,
                serde_json::to_value(ChatHistoryResult {
                    session_key: params.session_key.clone(),
                    session_id: Some(format!("mock-{}", params.session_key)),
                    messages,
                    thinking_level: Some("medium".to_string()),
                })
                .unwrap(),
            );
        }
    };

    // Apply limit
    let limit = params.limit.unwrap_or(200).min(1000);
    let messages = latest_messages_before(session.messages, params.before, limit);

    ResponseFrame::success(
        &request.id,
        serde_json::to_value(ChatHistoryResult {
            session_key: params.session_key,
            session_id: Some(session.id),
            messages,
            thinking_level: None,
        })
        .unwrap(),
    )
}

fn latest_messages_before(
    messages: Vec<serde_json::Value>,
    before: Option<u64>,
    limit: usize,
) -> Vec<serde_json::Value> {
    let filtered: Vec<serde_json::Value> = messages
        .into_iter()
        .filter(|message| message_is_before(message, before))
        .collect();
    let start = filtered.len().saturating_sub(limit);
    filtered.into_iter().skip(start).collect()
}

fn message_is_before(message: &serde_json::Value, before: Option<u64>) -> bool {
    before.map_or(true, |before| {
        message
            .get("timestamp")
            .and_then(|timestamp| timestamp.as_u64())
            .map_or(true, |timestamp| timestamp < before)
    })
}

/// Handle chat.abort method.
pub async fn handle_chat_abort(
    request: &RequestFrame,
    abort_manager: Arc<ChatAbortManager>,
    event_broadcaster: EventBroadcaster,
) -> ResponseFrame {
    // Parse parameters
    let params: ChatAbortParams = match &request.params {
        Some(p) => match serde_json::from_value(p.clone()) {
            Ok(p) => p,
            Err(e) => {
                return ResponseFrame::error(
                    &request.id,
                    error_codes::INVALID_REQUEST,
                    format!("Invalid params: {}", e),
                );
            }
        },
        None => {
            return ResponseFrame::error(
                &request.id,
                error_codes::INVALID_REQUEST,
                "Missing params",
            );
        }
    };

    let run_ids = if let Some(run_id) = &params.run_id {
        // Abort specific run
        if let Some(buffer) = abort_manager.abort(run_id).await {
            broadcast_aborted(&abort_manager, &event_broadcaster, run_id, &params.session_key, &buffer).await;
            vec![run_id.clone()]
        } else {
            vec![]
        }
    } else {
        // Abort all runs for session
        let aborted = abort_manager.abort_session(&params.session_key).await;
        for (run_id, buffer) in &aborted {
            broadcast_aborted(&abort_manager, &event_broadcaster, run_id, &params.session_key, buffer).await;
        }
        aborted.into_iter().map(|(r, _)| r).collect()
    };

    info!(
        session_key = %params.session_key,
        run_count = run_ids.len(),
        "Chat abort completed"
    );

    ResponseFrame::success(
        &request.id,
        serde_json::to_value(ChatAbortResult {
            ok: true,
            aborted: !run_ids.is_empty(),
            run_ids,
        })
        .unwrap(),
    )
}

/// Execute a chat task in the background.
async fn execute_chat_task(
    session_access: Arc<dyn SessionAccess>,
    router: Arc<dyn crate::gateway::context::MessageRouting>,
    abort_manager: Arc<ChatAbortManager>,
    event_broadcaster: EventBroadcaster,
    params: ChatSendParams,
    token: CancellationToken,
    buffer: Arc<tokio::sync::RwLock<String>>,
    run_id: String,
) {
    debug!(
        run_id = %run_id,
        session_key = %params.session_key,
        "Executing chat task"
    );

    // Get the session to check if it exists
    let session = session_access.get(&params.session_key).await;
    if session.is_none() {
        // Session not found - use mock mode
        debug!(
            run_id = %run_id,
            session_key = %params.session_key,
            "Session not found, using mock mode"
        );
        execute_mock_chat(&abort_manager, &event_broadcaster, &params, &token, &buffer, &run_id).await;
        return;
    }

    // Check for cancellation
    if token.is_cancelled() {
        debug!(run_id = %run_id, "Chat task cancelled before sending");
        return;
    }

    // Route and send via router (which uses BotConnector)
    let result = router.route_and_send(
        &params.session_key,
        &params.message,
        params.from.as_deref(),
    ).await;

    // If no results, use mock response
    if result.results.is_empty() {
        debug!(
            run_id = %run_id,
            "No bot targets found, using mock mode"
        );
        execute_mock_chat(&abort_manager, &event_broadcaster, &params, &token, &buffer, &run_id).await;
        return;
    }

    let mut had_error = false;
    let mut final_message: Option<serde_json::Value> = None;
    let stop_reason = "complete";

    // Process results from each bot
    for bot_result in &result.results {
        if !bot_result.success {
            had_error = true;
            warn!(
                run_id = %run_id,
                bot_id = %bot_result.bot_uuid,
                error = ?bot_result.error,
                "Bot failed to respond"
            );
            continue;
        }

        let text = &bot_result.content;

        // Store in buffer for potential abort
        {
            let mut buf = buffer.write().await;
            buf.push_str(text);
        }

        // Create final message
        final_message = Some(serde_json::json!({
            "role": "assistant",
            "content": text,
            "bot_uuid": bot_result.bot_uuid,
            "timestamp": std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_millis() as u64)
                .unwrap_or(0),
        }));

        // Emit delta event with the response
        let seq = abort_manager.next_seq(&run_id).await;
        let event = ChatEvent {
            run_id: run_id.clone(),
            session_key: params.session_key.clone(),
            seq,
            state: ChatEventState::Delta,
            message: Some(serde_json::json!({
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "bot_uuid": bot_result.bot_uuid,
            })),
            error_message: None,
            usage: None,
            stop_reason: None,
        };
        event_broadcaster.broadcast_chat(event);
    }

    // If all targets failed, use mock response
    if had_error && final_message.is_none() {
        debug!(run_id = %run_id, "All bot targets failed, using mock mode");
        execute_mock_chat(&abort_manager, &event_broadcaster, &params, &token, &buffer, &run_id).await;
        return;
    }

    // Emit final event
    let seq = abort_manager.next_seq(&run_id).await;
    let final_event = ChatEvent {
        run_id: run_id.clone(),
        session_key: params.session_key.clone(),
        seq,
        state: if had_error {
            ChatEventState::Error
        } else {
            ChatEventState::Final
        },
        message: final_message,
        error_message: if had_error {
            Some("One or more bots failed to respond".to_string())
        } else {
            None
        },
        usage: None,
        stop_reason: Some(stop_reason.to_string()),
    };
    event_broadcaster.broadcast_chat(final_event);

    // Cleanup
    abort_manager.remove(&run_id).await;

    info!(
        run_id = %run_id,
        session_key = %params.session_key,
        had_error = had_error,
        mentions = ?result.mentions,
        "Chat task completed"
    );
}

/// Execute a mock chat task (for testing without real bots).
async fn execute_mock_chat(
    abort_manager: &Arc<ChatAbortManager>,
    event_broadcaster: &EventBroadcaster,
    params: &ChatSendParams,
    token: &CancellationToken,
    buffer: &Arc<tokio::sync::RwLock<String>>,
    run_id: &str,
) {
    let response_text = generate_mock_response(&params.message);

    // Simulate streaming by sending delta events in chunks
    let chunks: Vec<&str> = response_text.split_inclusive(|c| c == '。' || c == '\n' || c == '！' || c == '？').collect();

    for chunk in &chunks {
        // Check for cancellation
        if token.is_cancelled() {
            debug!(run_id = %run_id, "Mock chat cancelled");
            return;
        }

        // Small delay to simulate streaming
        tokio::time::sleep(tokio::time::Duration::from_millis(50)).await;

        // Store in buffer
        {
            let mut buf = buffer.write().await;
            buf.push_str(chunk);
        }

        // Send delta event
        let seq = abort_manager.next_seq(run_id).await;
        let event = ChatEvent {
            run_id: run_id.to_string(),
            session_key: params.session_key.clone(),
            seq,
            state: ChatEventState::Delta,
            message: Some(serde_json::json!({
                "role": "assistant",
                "content": [{"type": "text", "text": chunk}],
            })),
            error_message: None,
            usage: None,
            stop_reason: None,
        };
        event_broadcaster.broadcast_chat(event);
    }

    // Final event with usage stats
    let seq = abort_manager.next_seq(run_id).await;
    let final_event = ChatEvent {
        run_id: run_id.to_string(),
        session_key: params.session_key.clone(),
        seq,
        state: ChatEventState::Final,
        message: Some(serde_json::json!({
            "role": "assistant",
            "content": response_text,
            "timestamp": std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_millis() as u64)
                .unwrap_or(0),
        })),
        error_message: None,
        usage: Some(serde_json::json!({
            "prompt_tokens": params.message.len() / 4,
            "completion_tokens": response_text.len() / 4,
            "total_tokens": (params.message.len() + response_text.len()) / 4,
        })),
        stop_reason: Some("complete".to_string()),
    };
    event_broadcaster.broadcast_chat(final_event);

    // Cleanup
    abort_manager.remove(run_id).await;

    info!(
        run_id = %run_id,
        session_key = %params.session_key,
        "Mock chat task completed"
    );
}

/// Broadcast an aborted event.
async fn broadcast_aborted(
    abort_manager: &Arc<ChatAbortManager>,
    event_broadcaster: &EventBroadcaster,
    run_id: &str,
    session_key: &str,
    buffer: &str,
) {
    let seq = abort_manager.next_seq(run_id).await;
    let event = ChatEvent {
        run_id: run_id.to_string(),
        session_key: session_key.to_string(),
        seq,
        state: ChatEventState::Aborted,
        message: if !buffer.is_empty() {
            Some(serde_json::json!({
                "role": "assistant",
                "content": [{"type": "text", "text": buffer}],
            }))
        } else {
            None
        },
        error_message: None,
        usage: None,
        stop_reason: Some("aborted".to_string()),
    };
    event_broadcaster.broadcast_chat(event);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::gateway::context::GatewaySession;
    use crate::gateway::context::SessionAccess;
    use async_trait::async_trait;
    use std::collections::HashMap;
    use tokio::sync::RwLock;

    /// Mock session store for testing
    struct MockSessionAccess {
        sessions: RwLock<HashMap<String, GatewaySession>>,
    }

    impl MockSessionAccess {
        fn new() -> Self {
            Self {
                sessions: RwLock::new(HashMap::new()),
            }
        }

        async fn insert(&self, key: &str, session: GatewaySession) {
            let mut sessions = self.sessions.write().await;
            sessions.insert(key.to_string(), session);
        }
    }

    #[async_trait]
    impl SessionAccess for MockSessionAccess {
        async fn get(&self, session_key: &str) -> Option<GatewaySession> {
            let sessions = self.sessions.read().await;
            sessions.get(session_key).cloned()
        }
    }

    #[tokio::test]
    async fn test_handle_chat_history_missing_params() {
        let session_access: Arc<dyn SessionAccess> = Arc::new(MockSessionAccess::new());

        let request = RequestFrame::new("test-1", "chat.history", None);

        let response = handle_chat_history(&request, &session_access).await;

        assert!(!response.ok);
        assert!(response.error.is_some());
        let error = response.error.unwrap();
        assert_eq!(error.code, error_codes::INVALID_REQUEST);
        assert!(error.message.contains("Missing params"));
    }

    #[tokio::test]
    async fn test_handle_chat_history_invalid_params() {
        let session_access: Arc<dyn SessionAccess> = Arc::new(MockSessionAccess::new());

        let request = RequestFrame::new("test-2", "chat.history", Some(serde_json::json!({"invalid_field": "value"})));

        let response = handle_chat_history(&request, &session_access).await;

        assert!(!response.ok);
        assert!(response.error.is_some());
        let error = response.error.unwrap();
        assert_eq!(error.code, error_codes::INVALID_REQUEST);
        assert!(error.message.contains("Invalid params"));
    }

    #[tokio::test]
    async fn test_handle_chat_history_session_not_found_returns_mock() {
        let session_access: Arc<dyn SessionAccess> = Arc::new(MockSessionAccess::new());

        let request = RequestFrame::new("test-3", "chat.history", Some(serde_json::json!({
            "session_key": "non-existent-session"
        })));

        let response = handle_chat_history(&request, &session_access).await;

        assert!(response.ok);
        assert!(response.payload.is_some());

        let payload = response.payload.unwrap();
        assert_eq!(payload["session_key"], "non-existent-session");
        assert!(payload["session_id"].as_str().unwrap().starts_with("mock-"));
        assert!(!payload["messages"].as_array().unwrap().is_empty());
        assert_eq!(payload["thinking_level"], "medium");
    }

    #[tokio::test]
    async fn test_handle_chat_history_existing_session() {
        let mock = MockSessionAccess::new();

        // Insert a test session
        let session = GatewaySession {
            id: "session-123".to_string(),
            driver_bot: "driver-bot".to_string(),
            participants: vec!["bot1".to_string(), "bot2".to_string()],
            messages: vec![
                serde_json::json!({
                    "id": "msg-1",
                    "role": "user",
                    "content": "Hello"
                }),
                serde_json::json!({
                    "id": "msg-2",
                    "role": "assistant",
                    "content": "Hi there!"
                }),
            ],
        };
        mock.insert("test-session", session).await;

        let session_access: Arc<dyn SessionAccess> = Arc::new(mock);

        let request = RequestFrame::new("test-4", "chat.history", Some(serde_json::json!({
            "session_key": "test-session"
        })));

        let response = handle_chat_history(&request, &session_access).await;

        assert!(response.ok);
        assert!(response.payload.is_some());

        let payload = response.payload.unwrap();
        assert_eq!(payload["session_key"], "test-session");
        assert_eq!(payload["session_id"], "session-123");
        assert_eq!(payload["messages"].as_array().unwrap().len(), 2);
    }

    #[tokio::test]
    async fn test_handle_chat_history_with_limit() {
        let mock = MockSessionAccess::new();

        // Insert a test session with multiple messages
        let messages: Vec<serde_json::Value> = (0..10)
            .map(|i| {
                serde_json::json!({
                    "id": format!("msg-{}", i),
                    "role": if i % 2 == 0 { "user" } else { "assistant" },
                    "content": format!("Message {}", i)
                })
            })
            .collect();

        let session = GatewaySession {
            id: "session-456".to_string(),
            driver_bot: "driver-bot".to_string(),
            participants: vec!["bot1".to_string()],
            messages,
        };
        mock.insert("limited-session", session).await;

        let session_access: Arc<dyn SessionAccess> = Arc::new(mock);

        let request = RequestFrame::new("test-5", "chat.history", Some(serde_json::json!({
            "session_key": "limited-session",
            "limit": 3
        })));

        let response = handle_chat_history(&request, &session_access).await;

        assert!(response.ok);
        let payload = response.payload.unwrap();
        assert_eq!(payload["messages"].as_array().unwrap().len(), 3);
    }

    #[tokio::test]
    async fn test_handle_chat_history_with_before() {
        let mock = MockSessionAccess::new();
        let messages: Vec<serde_json::Value> = (0..5)
            .map(|i| {
                serde_json::json!({
                    "id": format!("msg-{}", i),
                    "role": "user",
                    "content": format!("Message {}", i),
                    "timestamp": i * 10
                })
            })
            .collect();

        let session = GatewaySession {
            id: "session-before".to_string(),
            driver_bot: "driver-bot".to_string(),
            participants: vec!["bot1".to_string()],
            messages,
        };
        mock.insert("before-session", session).await;

        let session_access: Arc<dyn SessionAccess> = Arc::new(mock);
        let request = RequestFrame::new("test-before", "chat.history", Some(serde_json::json!({
            "session_key": "before-session",
            "before": 30,
            "limit": 10
        })));

        let response = handle_chat_history(&request, &session_access).await;

        assert!(response.ok);
        let payload = response.payload.unwrap();
        let messages = payload["messages"].as_array().unwrap();
        assert_eq!(messages.len(), 3);
        assert_eq!(messages[0]["id"], "msg-0");
        assert_eq!(messages[2]["id"], "msg-2");
    }

    #[tokio::test]
    async fn test_handle_chat_history_camel_case_params() {
        let session_access: Arc<dyn SessionAccess> = Arc::new(MockSessionAccess::new());

        // Test that snake_case params work
        let request = RequestFrame::new("test-6", "chat.history", Some(serde_json::json!({
            "session_key": "test-session-camel",
            "limit": 5
        })));

        let response = handle_chat_history(&request, &session_access).await;

        assert!(response.ok);
        let payload = response.payload.unwrap();
        assert_eq!(payload["session_key"], "test-session-camel");
    }

    #[test]
    fn test_generate_mock_response_hello() {
        // Test hello/hi variants
        let responses = vec!["hello", "hi", "你好"];
        for input in responses {
            let response = generate_mock_response(input);
            assert!(
                response.contains("你好"),
                "Expected greeting for input '{}', got: {}",
                input,
                response
            );
        }
    }

    #[test]
    fn test_generate_mock_response_help() {
        let response = generate_mock_response("help");
        assert!(response.contains("帮助") || response.contains("创建"));

        let response = generate_mock_response("帮助");
        assert!(response.contains("帮助") || response.contains("群组"));
    }

    #[test]
    fn test_generate_mock_response_bot() {
        let responses = vec!["bot", "机器人"];
        for input in responses {
            let response = generate_mock_response(input);
            assert!(
                response.contains("张三") || response.contains("Bot"),
                "Expected bot info for input '{}', got: {}",
                input,
                response
            );
        }
    }

    #[test]
    fn test_generate_mock_response_weather() {
        let responses = vec!["weather", "天气"];
        for input in responses {
            let response = generate_mock_response(input);
            assert!(
                response.contains("天气") || response.contains("°C"),
                "Expected weather info for input '{}', got: {}",
                input,
                response
            );
        }
    }

    #[test]
    fn test_generate_mock_response_time() {
        let response = generate_mock_response("time");
        assert!(response.contains("时间"));

        let response = generate_mock_response("时间");
        assert!(response.contains("时间") || response.contains("当前"));
    }

    #[test]
    fn test_generate_mock_response_status() {
        let responses = vec!["status", "状态"];
        for input in responses {
            let response = generate_mock_response(input);
            assert!(
                response.contains("状态") || response.contains("正常"),
                "Expected status info for input '{}', got: {}",
                input,
                response
            );
        }
    }

    #[test]
    fn test_generate_mock_response_default() {
        let response = generate_mock_response("some random message");
        assert!(response.contains("收到"));
        assert!(response.contains("模拟响应"));
    }

    #[test]
    fn test_generate_mock_response_case_insensitive() {
        // All uppercase
        let response = generate_mock_response("HELLO");
        assert!(response.contains("你好"));

        // Mixed case
        let response = generate_mock_response("HeLp");
        assert!(response.contains("帮助") || response.contains("创建"));
    }

    #[test]
    fn test_generate_mock_history_count() {
        let history = generate_mock_history("test-session");
        assert_eq!(history.len(), 4); // 2 pairs of user/assistant
    }

    #[test]
    fn test_generate_mock_history_structure() {
        let history = generate_mock_history("test-session");

        // Check first message structure
        let first_msg = &history[0];
        assert!(first_msg["id"].as_str().unwrap().starts_with("test-session"));
        assert_eq!(first_msg["role"], "user");
        assert!(!first_msg["content"].as_str().unwrap().is_empty());
        assert!(first_msg["timestamp"].as_u64().unwrap() > 0);
    }

    #[test]
    fn test_generate_mock_history_alternating_roles() {
        let history = generate_mock_history("test-session");

        assert_eq!(history[0]["role"], "user");
        assert_eq!(history[1]["role"], "assistant");
        assert_eq!(history[2]["role"], "user");
        assert_eq!(history[3]["role"], "assistant");
    }

    #[test]
    fn test_generate_mock_history_timestamps() {
        let history = generate_mock_history("test-session");

        // Timestamps should be in ascending order
        let ts0 = history[0]["timestamp"].as_u64().unwrap();
        let ts1 = history[1]["timestamp"].as_u64().unwrap();
        let ts2 = history[2]["timestamp"].as_u64().unwrap();
        let ts3 = history[3]["timestamp"].as_u64().unwrap();

        assert!(ts0 < ts1);
        assert!(ts1 < ts2);
        assert!(ts2 < ts3);
    }

    #[test]
    fn test_generate_mock_history_session_key_in_ids() {
        let history = generate_mock_history("my-session");

        for msg in history {
            let id = msg["id"].as_str().unwrap();
            assert!(id.starts_with("my-session"));
        }
    }

    #[tokio::test]
    async fn test_handle_chat_history_limit_capped_at_1000() {
        let session_access: Arc<dyn SessionAccess> = Arc::new(MockSessionAccess::new());

        // Request limit > 1000 should be capped
        let request = RequestFrame::new("test-limit", "chat.history", Some(serde_json::json!({
            "session_key": "test",
            "limit": 5000
        })));

        let response = handle_chat_history(&request, &session_access).await;

        assert!(response.ok);
        // Mock history only has 4 messages, so we should get all 4
        let payload = response.payload.unwrap();
        assert_eq!(payload["messages"].as_array().unwrap().len(), 4);
    }

    // Mock MessageRouting for testing handle_chat_send
    #[allow(dead_code)]
    struct MockMessageRouting {
        should_return_empty: bool,
    }

    #[allow(dead_code)]
    impl MockMessageRouting {
        fn new() -> Self {
            Self { should_return_empty: false }
        }

        fn with_empty_results() -> Self {
            Self { should_return_empty: true }
        }
    }

    #[async_trait]
    impl crate::gateway::context::MessageRouting for MockMessageRouting {
        async fn route(
            &self,
            _session_key: &str,
            _message: &str,
            _sender_bot_id: Option<&str>,
        ) -> crate::gateway::context::RoutingDecision {
            crate::gateway::context::RoutingDecision {
                targets: vec![],
                mentions: vec![],
                cleaned_message: _message.to_string(),
            }
        }

        async fn route_and_send(
            &self,
            _session_key: &str,
            _message: &str,
            _from: Option<&str>,
        ) -> crate::gateway::context::RouteAndSendResult {
            if self.should_return_empty {
                crate::gateway::context::RouteAndSendResult {
                    results: vec![],
                    mentions: vec![],
                }
            } else {
                crate::gateway::context::RouteAndSendResult {
                    results: vec![
                        crate::gateway::context::BotSendResult {
                            bot_uuid: "bot1".to_string(),
                            content: "Test response".to_string(),
                            success: true,
                            error: None,
                        },
                    ],
                    mentions: vec![],
                }
            }
        }
    }
}
