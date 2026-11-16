use super::*;

#[tokio::test]
async fn session_bound_chat_send_cannot_escape_bound_scope() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    connect_session_bound(&state, &tx, &mut rx, &mut connection_state).await;
    let send = BcsFrame::Request(RequestFrame::new(
        "send-mismatch",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "other-session",
            "bot_uuid": "other-actor",
            "message": "escape"
        })),
    ));

    let outcome = dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();

    assert_eq!(outcome, WebDispatchOutcome::Close);
    let response = recv_response(&mut rx).await;
    assert_eq!(
        response.error.as_ref().map(|error| error.code.as_str()),
        Some("token_scope_mismatch")
    );
    assert!(
        state
            .workbench_sessions
            .authorizations
            .lock()
            .await
            .is_empty()
    );
    assert!(state.message_flow.web_sends.lock().await.is_empty());
}

#[tokio::test]
async fn session_bound_chat_send_uses_bound_human_identity() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    connect_session_bound(&state, &tx, &mut rx, &mut connection_state).await;
    let send = BcsFrame::Request(RequestFrame::new(
        "send-bound",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-bound-1",
            "bot_uuid": "payload-controlled-actor",
            "message": "hello"
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();
    assert!(recv_response(&mut rx).await.ok);

    let authorizations = state.workbench_sessions.authorizations.lock().await;
    assert_eq!(authorizations.len(), 1);
    assert_eq!(authorizations[0].from_actor_id, "human_100001");
    drop(authorizations);
    let sends = state.message_flow.web_sends.lock().await;
    assert_eq!(sends.len(), 1);
    assert_eq!(sends[0].from_actor_id, "human_100001");
    assert_eq!(sends[0].session_id.as_deref(), Some("session-bound-1"));
}

#[tokio::test]
async fn session_bound_chat_abort_passes_only_the_bound_session() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    connect_session_bound(&state, &tx, &mut rx, &mut connection_state).await;
    let abort = BcsFrame::Request(RequestFrame::new(
        "abort-bound",
        "chat.abort",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "bot_id": "bot-target"
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&abort).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();
    assert!(recv_response(&mut rx).await.ok);

    let aborts = state.message_flow.aborts.lock().await;
    assert_eq!(aborts.len(), 1);
    assert_eq!(aborts[0].group_id, "group-web-1");
    assert!(aborts[0].run_id.is_none());
    assert_eq!(aborts[0].session_id, "session-bound-1");
    assert_eq!(aborts[0].bot_id, "bot-target");
}

#[tokio::test]
async fn session_bound_chat_abort_scope_mismatch_closes_connection() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    connect_session_bound(&state, &tx, &mut rx, &mut connection_state).await;
    let abort = BcsFrame::Request(RequestFrame::new(
        "abort-scope-mismatch",
        "chat.abort",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "other-session",
            "bot_id": "bot-target"
        })),
    ));

    let outcome = dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&abort).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();

    assert_eq!(outcome, WebDispatchOutcome::Close);
    let response = recv_response(&mut rx).await;
    assert_eq!(
        response.error.as_ref().map(|error| error.code.as_str()),
        Some("token_scope_mismatch")
    );
    assert!(
        state
            .workbench_sessions
            .abort_authorizations
            .lock()
            .await
            .is_empty()
    );
}
