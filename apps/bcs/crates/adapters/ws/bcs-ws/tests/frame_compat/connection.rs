use super::*;

#[tokio::test]
async fn bot_connect_rejects_provider_delivery_before_streaming_registration() {
    let state = new_state();
    *state.bot_runtime.delivery_target.lock().await = Some(BotDeliveryTarget::HttpProvider {
        bot_id: "bot-provider".to_string(),
        provider_id: "provider-1".to_string(),
        provider_bot_ref: "ref-1".to_string(),
        webhook_url: "https://provider.example/webhook".to_string(),
        bcs_to_provider_token: RedactedToken::new("secret"),
        protocol_version: "2025-05-01".to_string(),
    });
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;

    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-provider",
        "bot.connect",
        Some(serde_json::json!({
            "bot_id": "bot-provider",
            "token": "existing-token",
            "protocol_version": 1
        })),
    ));
    let outcome = dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(matches!(
        outcome,
        BotDispatchOutcome::BotConnect {
            registered: false,
            bot_uuid: Some(ref bot_uuid),
        } if bot_uuid == "bot-provider"
    ));

    let rejected = recv_response(&mut rx).await;
    assert!(!rejected.ok);
    assert_eq!(
        rejected.error.as_ref().map(|err| err.code.as_str()),
        Some("bot_delivery_is_provider")
    );
    assert_eq!(registered_bot_id, None);
    assert_eq!(state.bot_runtime.connect_count.load(Ordering::Relaxed), 0);
    assert!(
        !state
            .dispatch_state
            .bot_connections
            .is_connected("bot-provider")
            .await
    );
}

#[tokio::test]
async fn bot_connect_error_preserves_requested_uuid() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = None;

    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-invalid",
        "bot.connect",
        Some(serde_json::json!({
            "bot_id": "bot-invalid",
            "protocol_version": "invalid"
        })),
    ));
    let error = dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap_err();

    assert!(matches!(
        error,
        BotWsDispatchError::BotConnectError {
            bot_uuid: Some(ref bot_uuid),
            ..
        } if bot_uuid == "bot-invalid"
    ));
    assert_eq!(registered_bot_id, None);
}

#[tokio::test]
async fn bot_connect_and_status_frames_are_compatible() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;

    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-1",
        "bot.connect",
        Some(serde_json::json!({"bot_id": "bot-compat:staff", "protocol_version": 1})),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let connected = recv_response(&mut rx).await;
    assert!(connected.ok);
    assert_eq!(registered_bot_id.as_deref(), Some("bot-compat:staff"));
    assert!(
        state
            .dispatch_state
            .bot_connections
            .is_connected("bot-compat:staff")
            .await
    );

    let status = BcsFrame::Request(RequestFrame::new(
        "status-1",
        "bot.status",
        Some(serde_json::json!({
            "status": BotStatus::Busy,
            "dynamic_summary": "working",
            "load": 0.7
        })),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&status).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let updated = recv_response(&mut rx).await;
    assert!(updated.ok);
    let statuses = state.bot_runtime.statuses.lock().await;
    let status = statuses.get("bot-compat:staff").unwrap();
    assert_eq!(status.status, "busy");
    assert_eq!(status.dynamic_summary.as_deref(), Some("working"));
}

#[tokio::test]
async fn bot_connect_without_version_stays_on_v2_compatibility_default() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-default",
        "bot.connect",
        Some(serde_json::json!({"bot_id": "bot-default"})),
    ));

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let connected = recv_response(&mut rx).await;
    assert!(connected.ok);
    assert_eq!(connected.payload.unwrap()["protocol_version"], 2);
    assert_eq!(
        state
            .dispatch_state
            .bot_connections
            .protocol("bot-default")
            .await
            .expect("connected protocol")
            .protocol_version,
        2
    );
}

#[tokio::test]
async fn connected_socket_cannot_handshake_as_another_bot() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered = None;
    for id in ["bot-a", "bot-b"] {
        let connect = BcsFrame::Request(RequestFrame::new(id, "bot.connect",
            Some(serde_json::json!({"bot_id":id,"protocol_version":1}))));
        dispatch_frame(&state.dispatch_state,&serde_json::to_string(&connect).unwrap(),&tx,&mut registered).await.unwrap();
        let response = recv_response(&mut rx).await;
        assert_eq!(response.ok,id=="bot-a");
    }
    assert_eq!(registered.as_deref(),Some("bot-a"));
    assert_eq!(state.bot_runtime.connect_count.load(Ordering::Relaxed),1);
    assert!(state.dispatch_state.bot_connections.is_connected("bot-a").await);
    assert!(!state.dispatch_state.bot_connections.is_connected("bot-b").await);
}
