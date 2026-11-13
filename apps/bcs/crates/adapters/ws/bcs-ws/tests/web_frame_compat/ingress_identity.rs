use super::*;

#[tokio::test]
async fn chat_send_loads_the_live_ws_upgrade_ingress_into_web_send() {
    let state = new_state();
    *state.workbench_sessions.connect_scope.lock().await = Some(MessageViewScope::Participant);

    let now = system_now_ms();
    let ingress = upgrade_ingress("100001", now);
    let (tx, _rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let send = BcsFrame::Request(RequestFrame::new(
        "send-ingress-1",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "bot_uuid": "human_100001",
            "sender_id": "11111111",
            "sessionKey": "group-web-1:abcdef12",
            "message": "hello"
        })),
    ));

    let auth = WorkbenchConnectionAuth::UserBound {
        actor_id: Some("human_100001".to_string()),
        caller_ingress: Some(ingress),
    };
    // The connection snapshot Debug keeps principal material redacted: the
    // display actor id is visible by design, the ingress actor/staff never.
    let redacted = format!("{auth:?}");
    assert!(
        redacted.contains("actor_id: \"<redacted>\""),
        "ingress business actor must stay redacted in Debug: {redacted}"
    );
    assert!(
        redacted.contains("staff_no: \"<redacted>\""),
        "ingress staff principal must stay redacted in Debug: {redacted}"
    );

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &auth,
    )
    .await
    .unwrap();

    let calls = state.message_flow.web_sends.lock().await;
    assert_eq!(calls.len(), 1);
    let sent = calls[0]
        .caller_ingress
        .as_ref()
        .expect("live WS upgrade ingress must ride chat.send into message flow");
    assert_eq!(sent.actor().actor_id, "human_100001");
    assert_eq!(
        sent.auth_source(),
        bcs_service_api::application::caller_identity::HumanCallerAuthSource::WsAuth
    );
}

#[tokio::test]
async fn owned_bot_chat_send_does_not_carry_human_ingress() {
    let state = new_state();
    *state.workbench_sessions.connect_scope.lock().await = Some(MessageViewScope::Participant);

    let now = system_now_ms();
    let ingress = upgrade_ingress("100001", now);
    let (tx, _rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let send = BcsFrame::Request(RequestFrame::new(
        "send-ingress-owned-bot",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "bot_uuid": "bot-owned:100001",
            "sender_id": "11111111",
            "sessionKey": "group-web-1:abcdef12",
            "message": "hello as owned bot"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
            caller_ingress: Some(ingress),
        },
    )
    .await
    .unwrap();

    let calls = state.message_flow.web_sends.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].from_actor_id, "bot-owned:100001");
    assert!(
        calls[0].caller_ingress.is_none(),
        "owned-Bot speech must not carry the authenticated Human ingress"
    );
}

#[tokio::test]
async fn upgraded_login_window_does_not_expire_the_connection_identity() {
    let state = new_state();
    *state.workbench_sessions.connect_scope.lock().await = Some(MessageViewScope::Participant);

    // Authenticated two hours ago under a login that expired one hour ago:
    // identity carries no window (§15.97 + the 2026-10-25 follow-up), so the
    // ingress feeds frames regardless of the login's historical window.
    let now = system_now_ms();
    let ingress = upgrade_ingress("100001", now - 7_200_000);
    assert_eq!(ingress.authenticated_at_ms(), now - 7_200_000);

    let (tx, _rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let send = BcsFrame::Request(RequestFrame::new(
        "send-ingress-2",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "bot_uuid": "human_100001",
            "sender_id": "11111111",
            "sessionKey": "group-web-1:abcdef12",
            "message": "hello"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
            caller_ingress: Some(ingress),
        },
    )
    .await
    .unwrap();
    let calls = state.message_flow.web_sends.lock().await;
    assert_eq!(calls.len(), 1);
    assert!(
        calls[0].caller_ingress.is_some(),
        "the §15.97 connection-lifetime window keeps carrying the identity past the login expiry"
    );
}

#[tokio::test]
async fn the_adapter_sets_no_expiry_and_defense_belong_to_the_seam() {
    let state = new_state();
    *state.workbench_sessions.connect_scope.lock().await = Some(MessageViewScope::Participant);

    // §15.97 deleted the per-frame window re-check AND identity now carries
    // no window at all: the adapter hands on whatever material the upgrade
    // built; the one-shot handoff claim is the structural bound.
    let now = system_now_ms();
    let ingress = bcs_service_api::application::caller_identity::AuthenticatedHumanCallerIngress::new(
        bcs_service_api::application::principal::HumanActor {
            actor_id: "human_100001".into(),
            staff_no: "100001".into(),
        },
        bcs_service_api::application::caller_identity::HumanCallerAuthSource::WsAuth,
        now - 7_200_000,
        "local",
    )
    .expect("hand-crafted windowless ingress");

    let (tx, _rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let send = BcsFrame::Request(RequestFrame::new(
        "send-ingress-2-defense",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "bot_uuid": "human_100001",
            "sender_id": "11111111",
            "sessionKey": "group-web-1:abcdef12",
            "message": "hello"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
            caller_ingress: Some(ingress),
        },
    )
    .await
    .unwrap();
    let calls = state.message_flow.web_sends.lock().await;
    assert_eq!(calls.len(), 1);
    assert!(
        calls[0].caller_ingress.is_some(),
        "the adapter passes material through; the seam (handoff publish) owns expired-material defense"
    );
}

#[tokio::test]
async fn display_only_identity_without_ingress_stays_caller_less() {
    let state = new_state();
    *state.workbench_sessions.connect_scope.lock().await = Some(MessageViewScope::Participant);
    let (tx, _rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let send = BcsFrame::Request(RequestFrame::new(
        "send-ingress-3",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "bot_uuid": "human_100001",
            "sender_id": "11111111",
            "sessionKey": "group-web-1:abcdef12",
            "message": "hello"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
            caller_ingress: None,
        },
    )
    .await
    .unwrap();
    let calls = state.message_flow.web_sends.lock().await;
    assert_eq!(calls.len(), 1);
    assert!(calls[0].caller_ingress.is_none());
}
