use super::*;

#[tokio::test]
async fn invalid_bot_events_are_dropped_with_request_correlated_warnings() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let cases = [
        (None, "agent", "Received EventFrame from unregistered bot"),
        (Some("bot-diagnostic".to_string()), "agent", "Failed to parse agent event payload"),
        (Some("bot-diagnostic".to_string()), "chat.event", "Failed to parse chat event payload"),
        (Some("bot-diagnostic".to_string()), "future.event", "Unknown event type"),
    ];
    for (mut registered, event, message) in cases {
        let frame = BcsFrame::Event(EventFrame::new(event, Some(serde_json::json!({})), None));
        let (outcome, logs) = bcs_test_support::capture_request_logs("invalid-bot-event", async {
            dispatch_frame(&state.dispatch_state, &serde_json::to_string(&frame).unwrap(), &tx, &mut registered).await
        }).await;
        assert!(matches!(outcome.unwrap(), BotDispatchOutcome::Dispatched));
        assert!(rx.try_recv().is_err(), "ignored events must not produce a protocol response");
        assert!(state.message_flow.bot_events.lock().await.is_empty(), "invalid events must not reach message flow");
        let warning = logs.iter().find(|event| event["fields"]["message"] == message).expect(message);
        assert_eq!(warning["level"], "WARN");
        assert_eq!(warning["fields"]["request_id"], "invalid-bot-event");
    }
}

#[tokio::test]
async fn status_for_removed_bot_returns_not_registered_and_correlates_warning() {
    let state = new_state();
    state.bot_runtime.reject_status_update.store(true, Ordering::Relaxed);
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered = Some("removed-bot".to_string());
    let frame = BcsFrame::Request(RequestFrame::new("status-removed", "bot.status", Some(serde_json::json!({"status":"idle"}))));
    let (_, logs) = bcs_test_support::capture_request_logs("removed-bot-request", async {
        dispatch_frame(&state.dispatch_state, &serde_json::to_string(&frame).unwrap(), &tx, &mut registered).await.unwrap();
    }).await;
    let response = recv_response(&mut rx).await;
    assert_eq!(response.id, "status-removed");
    assert!(!response.ok);
    assert_eq!(response.error.unwrap().code, "not_registered");
    assert!(state.bot_runtime.statuses.lock().await.is_empty());
    let warning = logs.iter().find(|event| event["fields"]["message"] == "Failed to update status - bot not found in registry").unwrap();
    assert_eq!(warning["fields"]["request_id"], "removed-bot-request");
    assert_eq!(warning["fields"]["bot_id"], "removed-bot");
}

#[tokio::test]
async fn bot_socket_errors_keep_request_identity_and_cleanup_after_disconnect_failure() {
    use futures::{SinkExt, StreamExt};
    use tokio_tungstenite::tungstenite::Message;
    let state = new_state();
    state.bot_runtime.fail_disconnect.store(true, Ordering::Relaxed);
    let registry = state.dispatch_state.bot_connections.clone();
    let (_, logs) = bcs_test_support::capture_request_logs("bot-socket-diagnostic", async {
        let dispatch = ::tracing::dispatcher::get_default(Clone::clone);
        let request_id = bcs_observability::current_request_id();
        let (finished_tx, finished_rx) = tokio::sync::oneshot::channel();
        let finished_tx = Arc::new(Mutex::new(Some(finished_tx)));
        let app = axum::Router::new().route("/ws/bot", axum::routing::get(move |ws: axum::extract::ws::WebSocketUpgrade| {
            let state = state.dispatch_state.clone();
            let dispatch = dispatch.clone();
            let request_id = request_id.clone();
            let finished_tx = finished_tx.clone();
            async move {
                ws.on_upgrade(move |socket| bcs_observability::with_request_id(request_id, async move {
                    bcs_ws::bot::handle_connection(socket, state, Arc::new(bcs_test_support::NoopWsLifecycleInstrumentationHook), None, None).await;
                    finished_tx.lock().await.take().unwrap().send(()).unwrap();
                }).with_subscriber(dispatch))
            }
        }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap(); });
        let (mut socket, _) = tokio_tungstenite::connect_async(format!("ws://{addr}/ws/bot")).await.unwrap();
        socket.send(Message::Binary(vec![1, 2, 3].into())).await.unwrap();
        socket.send(Message::Text("{".into())).await.unwrap();
        let response = tokio::time::timeout(std::time::Duration::from_secs(2), socket.next()).await.unwrap().unwrap().unwrap();
        let response: serde_json::Value = serde_json::from_str(response.to_text().unwrap()).unwrap();
        assert_eq!(response["ok"], false);
        assert_eq!(response["error"]["code"], "dispatch_error");
        let connect = BcsFrame::Request(RequestFrame::new("connect-diagnostic", "bot.connect", Some(serde_json::json!({"bot_id":"bot-diagnostic","protocol_version":1}))));
        socket.send(Message::Text(serde_json::to_string(&connect).unwrap().into())).await.unwrap();
        let response = tokio::time::timeout(std::time::Duration::from_secs(2), socket.next()).await.unwrap().unwrap().unwrap();
        let response: serde_json::Value = serde_json::from_str(response.to_text().unwrap()).unwrap();
        assert_eq!(response["ok"], true);
        assert!(registry.is_connected("bot-diagnostic").await);
        socket.close(None).await.unwrap();
        tokio::time::timeout(std::time::Duration::from_secs(2), finished_rx).await.unwrap().unwrap();
        assert!(!registry.is_connected("bot-diagnostic").await, "adapter cleanup must still run when persistence fails");
        server.abort();
        let _ = server.await;
    }).await;
    for message in ["Received unexpected binary frame", "Frame dispatch error", "Failed to record bot streaming disconnect"] {
        let warning = logs.iter().find(|event| event["fields"]["message"] == message).expect(message);
        assert_eq!(warning["level"], "WARN");
        assert_eq!(warning["fields"]["request_id"], "bot-socket-diagnostic");
    }
}
