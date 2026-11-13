use super::*;

#[tokio::test]
async fn user_bound_chat_abort_preserves_existing_response() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-abort",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-web-1"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
            caller_ingress: None,
        },
    )
    .await
    .unwrap();
    assert!(recv_response(&mut rx).await.ok);
    let abort = BcsFrame::Request(RequestFrame::new(
        "abort-1",
        "chat.abort",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-web-1",
            "bot_id": "bot-target"
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&abort).unwrap(),
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
    assert!(response.ok);
    assert_eq!(response.payload.as_ref().expect("payload")["aborted"], true);
    assert_eq!(state.message_flow.aborts.lock().await.len(), 1);
    let authorizations = state.workbench_sessions.abort_authorizations.lock().await;
    assert_eq!(
        authorizations[0].bound_actor_id.as_deref(),
        Some("human_100001")
    );
    assert_eq!(authorizations[0].target_bot_id, "bot-target");
    assert!(state.message_flow.aborts.lock().await[0].run_id.is_none());
    assert_eq!(
        *state.interactions.invalidations.lock().await,
        vec![("run-web-1".to_string(), "chat_abort".to_string())]
    );
}

#[tokio::test]
async fn user_bound_chat_abort_reports_legacy_plugin_restart_resolution() {
    let state = new_state();
    *state.message_flow.abort_outcome.lock().await = Some(ChatAbortOutcome {
        aborted: false,
        aborted_run_ids: vec![],
        bot_deliveries: vec![],
        frontend_deliveries: vec![],
        failures: vec![ChatAbortFailure {
            run_id: "run-legacy".to_string(),
            code: "chat_abort_not_supported".to_string(),
            message: "Bot does not support chat.abort".to_string(),
        }],
    });
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    let auth = WorkbenchConnectionAuth::UserBound {
        actor_id: Some("human_100001".to_string()),
        caller_ingress: None,
    };
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-abort-legacy",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-web-1"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut connection_state,
        &auth,
    )
    .await
    .unwrap();
    assert!(recv_response(&mut rx).await.ok);

    let abort = BcsFrame::Request(RequestFrame::new(
        "abort-legacy-plugin",
        "chat.abort",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-web-1",
            "bot_id": "bot-target"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&abort).unwrap(),
        &tx,
        &mut connection_state,
        &auth,
    )
    .await
    .unwrap();

    let response = recv_response(&mut rx).await;
    assert!(!response.ok);
    let error = response.error.expect("structured abort capability error");
    assert_eq!(error.code, "chat_abort_not_supported");
    assert!(!error.retryable);
    let details = error.details.expect("restart resolution details");
    assert_eq!(details["bot_id"], "bot-target");
    assert_eq!(details["resolution"], "restart_bot");
    assert_eq!(
        details["failures"][0]["code"],
        "chat_abort_not_supported"
    );
}

#[tokio::test]
async fn user_bound_legacy_run_id_resolves_to_bot_session_scope() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    let auth = WorkbenchConnectionAuth::UserBound {
        actor_id: Some("human_100001".to_string()),
        caller_ingress: None,
    };
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-legacy-abort",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-web-1"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut connection_state,
        &auth,
    )
    .await
    .unwrap();
    assert!(recv_response(&mut rx).await.ok);

    let abort = BcsFrame::Request(RequestFrame::new(
        "abort-legacy",
        "chat.abort",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "run_id": "run-legacy"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&abort).unwrap(),
        &tx,
        &mut connection_state,
        &auth,
    )
    .await
    .unwrap();
    assert!(recv_response(&mut rx).await.ok);

    let aborts = state.message_flow.aborts.lock().await;
    assert_eq!(aborts.len(), 1);
    assert_eq!(aborts[0].group_id, "group-web-1");
    assert_eq!(aborts[0].session_id, "session-web-1");
    assert_eq!(aborts[0].bot_id, "bot-target");
    assert!(aborts[0].run_id.is_none());
}

#[tokio::test]
async fn user_bound_chat_abort_requires_connect() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    let abort = BcsFrame::Request(RequestFrame::new(
        "abort-before-connect",
        "chat.abort",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-web-1",
            "bot_id": "bot-target"
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&abort).unwrap(),
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
    assert_eq!(
        response.error.as_ref().map(|error| error.code.as_str()),
        Some("connect_required")
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

#[tokio::test]
async fn anonymous_user_bound_chat_abort_is_unauthorized_after_connect() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    let auth = WorkbenchConnectionAuth::UserBound { actor_id: None, caller_ingress: None };
    let auth = WorkbenchConnectionAuth::UserBound { actor_id: None, caller_ingress: None };
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-anonymous",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-web-1"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut connection_state,
        &auth,
    )
    .await
    .unwrap();
    assert!(recv_response(&mut rx).await.ok);

    let abort = BcsFrame::Request(RequestFrame::new(
        "abort-anonymous",
        "chat.abort",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-web-1",
            "bot_id": "bot-target"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&abort).unwrap(),
        &tx,
        &mut connection_state,
        &auth,
    )
    .await
    .unwrap();

    let response = recv_response(&mut rx).await;
    assert_eq!(
        response.error.as_ref().map(|error| error.code.as_str()),
        Some("unauthorized")
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

#[tokio::test]
async fn user_bound_chat_abort_requires_target_session_subscription() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    let auth = WorkbenchConnectionAuth::UserBound {
        actor_id: Some("human_100001".to_string()),
        caller_ingress: None,
    };
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-one-session",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-web-1"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut connection_state,
        &auth,
    )
    .await
    .unwrap();
    assert!(recv_response(&mut rx).await.ok);

    let abort = BcsFrame::Request(RequestFrame::new(
        "abort-other-session",
        "chat.abort",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-web-2",
            "bot_id": "bot-target"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&abort).unwrap(),
        &tx,
        &mut connection_state,
        &auth,
    )
    .await
    .unwrap();

    let response = recv_response(&mut rx).await;
    assert_eq!(
        response.error.as_ref().map(|error| error.code.as_str()),
        Some("session_not_subscribed")
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
