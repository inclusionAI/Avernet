use super::*;

#[tokio::test]
async fn caller_extensions_reach_callback_and_sse_without_replacing_provider_auth() {
    for sse in [false, true] {
        let captured: CapturedState = Arc::new(Mutex::new(None));
        let app = Router::new().route("/webhook", post(move |State(state): State<CapturedState>, headers: HeaderMap, Json(body): Json<Value>| async move {
            capture(state, headers, body).await;
            if sse { capture_sse().await } else {
                Response::builder().header("content-type", "application/json").body(Body::from(r#"{"ok":true}"#)).unwrap()
            }
        })).with_state(captured.clone());
        let (url, server) = spawn_server(app).await;
        let transport = HttpProviderTransport::allowing_private_networks_for_tests();
        transport.set_ingest(Arc::new(RecordingMessageFlow::default()), Arc::new(RecordingRunContext::default()));
        let extensions = json!({
            "caller_context": {"subject": {"kind": "human", "ref": "staff-1"}},
            "caller_execution_token": {"type": "iam-token", "token": "synthetic-caller-marker"},
            "caller_wait_mode": "detached"
        });
        let result = transport.deliver(BotDeliveryCommand {
            target: provider_target_v2(url), run_id: "caller-run".into(),
            frame: BcsFrame::Request(RequestFrame::new("caller-run", "chat.send", Some(json!({
                "bcs_session_id":"session-1", "bcs_group_id":"group-1", "message":{"text":"hello"},
                "extensions": extensions
            })))),
            delivery_kind: BotDeliveryKind::Send,
            provider_transport: if sse { Default::default() } else { ProviderTransportPreference::Callback },
            provider_bypass_headers: vec![],
        }).await.unwrap();
        assert!(result.delivered);
        let request = captured.lock().await.clone().unwrap();
        assert_eq!(request.authorization.as_deref(), Some("Bearer secret-b2p"));
        assert_eq!(request.body["extensions"], extensions);
        assert_eq!(request.transport.as_deref(), Some(if sse { "sse" } else { "callback" }));
        assert!(!request.body["message"].to_string().contains("synthetic-caller-marker"));
        server.abort();
    }
}
