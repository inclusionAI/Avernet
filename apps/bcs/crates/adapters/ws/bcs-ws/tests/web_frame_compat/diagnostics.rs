use super::*;

#[tokio::test]
async fn unexpected_frontend_frames_are_ignored_with_correlated_warnings() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection = WebClientConnectionState::default();
    let _protected_rx = connection.open_protected_queue();
    let frames = [
        (BcsFrame::Response(ResponseFrame::ok("unexpected-response", serde_json::json!({}))), "Unexpected ResponseFrame from frontend client"),
        (BcsFrame::Event(bcs_protocol::EventFrame::new("unexpected.event", None, None)), "Unexpected EventFrame from frontend client"),
    ];
    for (frame, message) in frames {
        let (result, logs) = bcs_test_support::capture_request_logs("unexpected-web-frame", async {
            dispatch_client_frame(&state.dispatch_state, &serde_json::to_string(&frame).unwrap(), &tx,
                &mut connection, &WorkbenchConnectionAuth::UserBound { actor_id: None, caller_ingress: None }).await
        }).await;
        assert!(matches!(result.unwrap(), WebDispatchOutcome::Dispatched));
        assert!(rx.try_recv().is_err(), "unexpected client frames must not produce responses");
        assert!(connection.subscribed_sessions.is_empty());
        let warning = logs.iter().find(|event| event["fields"]["message"] == message).expect(message);
        assert_eq!(warning["level"], "WARN");
        assert_eq!(warning["fields"]["request_id"], "unexpected-web-frame");
    }
}

#[tokio::test]
async fn invalid_session_authorization_context_closes_and_logs_request_id() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection = WebClientConnectionState::default();
    let _protected_rx = connection.open_protected_queue();
    let auth = WorkbenchConnectionAuth::SessionBound {
        tenant: None, actor_id: "bot-not-a-human".into(), group_id: "group-web-1".into(), session_id: "session-bound-1".into(),
        caller_ingress: None,
    };
    let frame = BcsFrame::Request(RequestFrame::new("invalid-binding", "connect", Some(serde_json::json!({
        "group_id":"group-web-1", "session_id":"session-bound-1"
    }))));
    let (result, logs) = bcs_test_support::capture_request_logs("invalid-session-context", async {
        dispatch_client_frame(&state.dispatch_state, &serde_json::to_string(&frame).unwrap(), &tx, &mut connection, &auth).await
    }).await;
    assert!(matches!(result.unwrap(), WebDispatchOutcome::Close));
    let response = recv_response(&mut rx).await;
    assert_eq!(response.id, "invalid-binding");
    assert_eq!(response.error.unwrap().code, "session_access_revoked");
    assert!(connection.subscribed_sessions.is_empty());
    let warning = logs.iter().find(|event| event["fields"]["message"] == "session-bound connect is missing a valid V1 authorization context").unwrap();
    assert_eq!(warning["fields"]["request_id"], "invalid-session-context");
}

#[tokio::test]
async fn interaction_service_failures_preserve_connect_ack_and_retryable_resolution_error() {
    let state = new_state();
    *state.interactions.pending_error.lock().await = Some(bcs_service_api::ServiceError::InternalError("pending store unavailable".into()));
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection = WebClientConnectionState::default();
    let _protected_rx = connection.open_protected_queue();
    let (_, logs) = bcs_test_support::capture_request_logs("interaction-failure-request", async {
        connect_session_bound(&state, &tx, &mut rx, &mut connection).await;
        assert!(rx.try_recv().is_err(), "failed replay must not fabricate an interaction event");
        *state.interactions.next_result.lock().await = Some(Err(InteractionServiceError::Internal("internal resolution store unavailable".into())));
        let frame = BcsFrame::Request(RequestFrame::new("resolve-unavailable", "interaction.resolve", Some(serde_json::json!({
            "bcsRunId":"run-diagnostic", "interactionId":"interaction-diagnostic", "idempotencyKey":"diagnostic-key", "decision":"allow_once"
        }))));
        dispatch_client_frame(&state.dispatch_state, &serde_json::to_string(&frame).unwrap(), &tx, &mut connection, &session_bound_auth()).await.unwrap();
        let response = recv_response(&mut rx).await;
        assert_eq!(response.id, "resolve-unavailable");
        assert!(!response.ok);
        let error = response.error.unwrap();
        assert_eq!(error.code, "interaction_resolve_failed");
        assert_eq!(error.message, "Interaction resolution could not be processed");
        assert!(error.retryable);
        assert_eq!(error.details.unwrap()["interactionStatus"], "pending");
        assert_eq!(state.interactions.resolves.lock().await.len(), 1);
        assert_eq!(connection.subscribed_sessions.len(), 1, "service failure must preserve subscription");
    }).await;
    // The resolution logger explicitly records `%message`; JSON retains that
    // application error under `message`, while the protocol response stays generic.
    for message in ["pending interaction replay failed after connect", "internal resolution store unavailable"] {
        let warning = logs.iter().find(|event| event["fields"]["message"] == message)
            .unwrap_or_else(|| panic!("missing warning {message}: {logs:?}"));
        assert_eq!(warning["level"], "WARN");
        assert_eq!(warning["fields"]["request_id"], "interaction-failure-request");
    }
}

#[tokio::test]
async fn frontend_socket_malformed_binary_and_abrupt_close_logs_keep_request_id() {
    use futures::{SinkExt, StreamExt};
    use tracing::instrument::WithSubscriber;
    use tokio_tungstenite::tungstenite::Message;
    let state = new_state();
    let (_, logs) = bcs_test_support::capture_request_logs("web-socket-diagnostic", async {
        let dispatch = tracing::dispatcher::get_default(Clone::clone);
        let request_id = bcs_observability::current_request_id();
        let (finished_tx, finished_rx) = tokio::sync::oneshot::channel();
        let finished_tx = Arc::new(Mutex::new(Some(finished_tx)));
        let app = axum::Router::new().route("/ws", axum::routing::get(move |ws: axum::extract::ws::WebSocketUpgrade| {
            let state = state.dispatch_state.clone();
            let dispatch = dispatch.clone();
            let request_id = request_id.clone();
            let finished_tx = finished_tx.clone();
            async move {
                ws.on_upgrade(move |socket| bcs_observability::with_request_id(request_id, async move {
                    bcs_ws::web::handle_client_connection(socket, state, WorkbenchConnectionAuth::UserBound { actor_id: None, caller_ingress: None },
                        Arc::new(bcs_test_support::NoopWsLifecycleInstrumentationHook)).await;
                    finished_tx.lock().await.take().unwrap().send(()).unwrap();
                }).with_subscriber(dispatch))
            }
        }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap(); });
        let (mut socket, _) = tokio_tungstenite::connect_async(format!("ws://{addr}/ws")).await.unwrap();
        socket.send(Message::Binary(vec![1, 2, 3].into())).await.unwrap();
        socket.send(Message::Text("{".into())).await.unwrap();
        let response = timeout(Duration::from_secs(2), socket.next()).await.unwrap().unwrap().unwrap();
        let response: serde_json::Value = serde_json::from_str(response.to_text().unwrap()).unwrap();
        assert_eq!(response["ok"], false);
        assert_eq!(response["error"]["code"], "dispatch_error");
        // 不可解析帧没有请求 id 可回显：保留 legacy 占位 "error"。
        assert_eq!(response["id"], "error");
        // 可解析的 req 帧派发失败时：错误响应必须回显请求 id —— 客户端按 id
        // 等待应答，id-less 错误在 UI 侧表现为"毫无响应"（客户端永远等不到）。
        socket
            .send(Message::Text(
                r#"{"type":"req","id":"boom-123","method":"connect","params":"malformed"}"#.into(),
            ))
            .await.unwrap();
        let response = timeout(Duration::from_secs(2), socket.next()).await.unwrap().unwrap().unwrap();
        let response: serde_json::Value = serde_json::from_str(response.to_text().unwrap()).unwrap();
        assert_eq!(response["ok"], false);
        assert_eq!(response["id"], "boom-123");
        assert_eq!(response["error"]["code"], "dispatch_error");
        // Drop TCP without a close frame so the actual read loop observes a protocol error.
        drop(socket);
        timeout(Duration::from_secs(2), finished_rx).await.unwrap().unwrap();
        server.abort();
        let _ = server.await;
    }).await;
    for (message, level) in [("Received unexpected binary frame", "WARN"), ("Frame dispatch error", "WARN"), ("WebSocket error", "ERROR")] {
        let event = logs.iter().find(|event| event["fields"]["message"] == message).expect(message);
        assert_eq!(event["level"], level);
        assert_eq!(event["fields"]["request_id"], "web-socket-diagnostic");
    }
}

#[tokio::test]
async fn user_bound_unknown_method_preserves_protocol_error_response() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    let request = BcsFrame::Request(RequestFrame::new("unknown-1", "future.method", None));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&request).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
            caller_ingress: None,
        },
    )
    .await
    .unwrap();

    let response = recv_response(&mut rx).await;
    assert!(!response.ok);
    assert_eq!(
        response.error.as_ref().map(|error| error.code.as_str()),
        Some("unknown_method")
    );
}
