use super::*;

#[tokio::test]
async fn session_bound_connect_publishes_the_workbench_speaking_mode_projection() {
    let state = new_state();
    *state.workbench_sessions.speaking_mode_projection.lock().await = Some(
        bcs_domain::speaking_lease::SessionSpeakingMode::LeaseRequired,
    );
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection = WebClientConnectionState::default();
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-speaking",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-bound-1"
        })),
    ));
    let outcome = dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut connection,
        &session_bound_auth(),
    )
    .await
    .unwrap();
    assert!(matches!(outcome, WebDispatchOutcome::ClientConnect { subscribed: true }));
    let response = recv_response(&mut rx).await;
    assert!(response.ok);
    let payload = response.payload.expect("connect payload");
    // The session-bound handshake must publish the SAME advisory projection
    // the UserBound connect resolves — never a hard-coded Plain.
    assert_eq!(payload["speaking_mode"], "lease_required");
    let commands = state.workbench_sessions.speaking_mode_commands.lock().await;
    assert_eq!(commands.len(), 1, "one projection per session-bound connect");
    assert_eq!(commands[0].group_id, "group-web-1");
    assert_eq!(commands[0].session_id.as_deref(), Some("session-bound-1"));
}

#[tokio::test]
async fn session_bound_requires_connect_before_business_frames() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    let send = BcsFrame::Request(RequestFrame::new(
        "send-before-connect",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-bound-1",
            "message": "hello"
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

    assert_eq!(outcome, WebDispatchOutcome::Dispatched);
    let response = recv_response(&mut rx).await;
    assert_eq!(
        response.error.as_ref().map(|error| error.code.as_str()),
        Some("connect_required")
    );
    assert!(state.message_flow.web_sends.lock().await.is_empty());
}

#[tokio::test]
async fn session_bound_scope_mismatch_closes_without_dynamic_authorization() {
    for params in [
        serde_json::json!({
            "group_id": "other-group",
            "session_id": "session-bound-1"
        }),
        serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "other-session"
        }),
    ] {
        let state = new_state();
        let (tx, mut rx) = mpsc::channel(8);
        let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
        let connect = BcsFrame::Request(RequestFrame::new(
            "connect-mismatch",
            "connect",
            Some(params),
        ));

        let outcome = dispatch_client_frame(
            &state.dispatch_state,
            &serde_json::to_string(&connect).unwrap(),
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
        assert!(state.workbench_sessions.connects.lock().await.is_empty());
    }
}

#[tokio::test]
async fn session_bound_connect_projects_v1_participants_into_workbench_shape() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-projection",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-bound-1"
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();

    let response = recv_response(&mut rx).await;
    assert!(response.ok);
    let payload = response.payload.expect("connect payload");
    assert_eq!(payload["group_id"], "group-web-1");
    assert_eq!(
        payload["participants"],
        serde_json::json!([{
            "bot_uuid": "human_100001",
            "role": "observer",
            "type": "bot",
            "mode": "present",
            "message_view_scope": "full"
        }])
    );
}

#[tokio::test]
async fn session_bound_connect_authorizes_once_with_immutable_binding() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();

    assert_eq!(
        connect_session_bound(&state, &tx, &mut rx, &mut connection_state).await,
        WebDispatchOutcome::ClientConnect { subscribed: true }
    );
    assert!(state.workbench_sessions.connects.lock().await.is_empty());
    let authorizations = state.group_session_connections.authorizations.lock().await;
    assert_eq!(authorizations.len(), 1);
    assert_eq!(
        authorizations[0].binding,
        GroupSessionConnectionBinding {
            tenant: Some("tenant-a".to_string()),
            user_id: "100001".to_string(),
            group_id: "group-web-1".to_string(),
            session_id: "session-bound-1".to_string(),
        }
    );
    drop(authorizations);

    let second = BcsFrame::Request(RequestFrame::new(
        "connect-again",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-bound-1"
        })),
    ));
    let outcome = dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&second).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();

    assert_eq!(outcome, WebDispatchOutcome::Dispatched);
    let response = recv_response(&mut rx).await;
    assert_eq!(
        response.error.as_ref().map(|error| error.code.as_str()),
        Some("already_connected")
    );
    assert!(state.workbench_sessions.connects.lock().await.is_empty());
    assert_eq!(
        state
            .group_session_connections
            .authorizations
            .lock()
            .await
            .len(),
        1
    );
}

#[tokio::test]
async fn session_bound_revoked_access_closes_with_stable_error() {
    let state = new_state();
    *state.group_session_connections.reject_connect.lock().await = true;
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-revoked",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-bound-1"
        })),
    ));

    let outcome = dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
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
        Some("session_access_revoked")
    );
    assert!(state.workbench_sessions.connects.lock().await.is_empty());
    assert_eq!(
        state
            .group_session_connections
            .authorizations
            .lock()
            .await
            .len(),
        1
    );
}
