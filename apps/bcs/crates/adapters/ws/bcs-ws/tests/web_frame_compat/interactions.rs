use super::*;

#[tokio::test]
async fn session_bound_connect_replays_pending_interactions_after_ack() {
    let state = new_state();
    state
        .interactions
        .pending
        .lock()
        .await
        .push(InteractionFrontendEvent {
            bcs_run_id: "bcs-run-1".to_string(),
            bcs_session_id: "session-bound-1".to_string(),
            group_id: "group-web-1".to_string(),
            bot_id: "bot-1".to_string(),
            payload: serde_json::json!({
                "runId":"provider-run-1",
                "phase":"requested",
                "interactionId":"interaction-1",
                "kind":"exec"
            }),
        });
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let mut replay_rx = connection_state.open_protected_queue();

    connect_session_bound(&state, &tx, &mut rx, &mut connection_state).await;
    // Replays are independent finite events: each is authorized
    // once against CURRENT facts at replay time, and flows as an
    // already-authorized Data frame (§8.2/§7.3).
    let frame = timeout(Duration::from_secs(1), replay_rx.recv())
        .await
        .expect("pending interaction replay was not queued")
        .expect("replay frame");
    let bcs_ws::shared::OutboundFrame::Data { payload } = frame else {
        panic!("expected a replay data frame")
    };
    let replay: serde_json::Value = serde_json::from_str(&payload).unwrap();
    assert_eq!(replay["event"], "interaction");
    assert_eq!(replay["bcsRunId"], "bcs-run-1");
    assert_eq!(replay["bcsSessionId"], "session-bound-1");
    assert_eq!(replay["payload"]["interactionId"], "interaction-1");
}

#[tokio::test]
async fn user_bound_connect_replays_pending_interactions_after_ack() {
    let state = new_state();
    state
        .interactions
        .pending
        .lock()
        .await
        .push(InteractionFrontendEvent {
            bcs_run_id: "bcs-run-1".to_string(),
            bcs_session_id: "group-web-1:abcdef12".to_string(),
            group_id: "group-web-1".to_string(),
            bot_id: "bot-1".to_string(),
            payload: serde_json::json!({
                "runId":"provider-run-1",
                "phase":"requested",
                "interactionId":"interaction-1",
                "kind":"ask_user"
            }),
        });
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let mut replay_rx = connection_state.open_protected_queue();
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-user-bound",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "group-web-1:abcdef12"
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
    let frame = timeout(Duration::from_secs(1), replay_rx.recv())
        .await
        .expect("pending interaction replay was not queued")
        .expect("replay frame");
    let bcs_ws::shared::OutboundFrame::Data { payload } = frame else {
        panic!("expected a replay data frame")
    };
    let replay: serde_json::Value =
        serde_json::from_str(&payload).unwrap();
    assert_eq!(replay["event"], "interaction");
    assert_eq!(replay["bcsRunId"], "bcs-run-1");
    assert_eq!(replay["bcsSessionId"], "group-web-1:abcdef12");
    assert_eq!(replay["payload"]["interactionId"], "interaction-1");
}

#[tokio::test]
async fn interaction_resolve_returns_accepted_and_structured_failures() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    connect_session_bound(&state, &tx, &mut rx, &mut connection_state).await;

    let accepted = BcsFrame::Request(RequestFrame::new(
        "resolve-1",
        "interaction.resolve",
        Some(serde_json::json!({
            "bcsRunId":"bcs-run-1",
            "interactionId":"interaction-1",
            "idempotencyKey":"idem-1",
            "decision":"allow_once"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&accepted).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();
    let response = recv_response(&mut rx).await;
    assert!(response.ok);
    let payload = response.payload.unwrap();
    assert_eq!(payload["interactionStatus"], "accepted");
    assert_eq!(payload["idempotencyKey"], "idem-1");
    let commands = state.interactions.resolves.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].resolver_actor_id, "human_100001");
    assert_eq!(
        commands[0].expected_bcs_session_id.as_deref(),
        Some("session-bound-1")
    );
    assert_eq!(
        commands[0].expected_group_id.as_deref(),
        Some("group-web-1")
    );
    assert_eq!(commands[0].resolution["decision"], "allow_once");
    drop(commands);

    *state.interactions.next_result.lock().await =
        Some(Err(InteractionServiceError::ResolveFailed {
            message: "Provider temporarily unavailable".to_string(),
            retryable: true,
            status: InteractionStatus::Pending,
        }));
    let retry = BcsFrame::Request(RequestFrame::new(
        "resolve-2",
        "interaction.resolve",
        Some(serde_json::json!({
            "bcsRunId":"bcs-run-1",
            "interactionId":"interaction-1",
            "idempotencyKey":"idem-1",
            "decision":"allow_once"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&retry).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();
    let response = recv_response(&mut rx).await;
    assert!(!response.ok);
    let error = response.error.unwrap();
    assert_eq!(error.code, "interaction_resolve_failed");
    assert!(error.retryable);
    assert_eq!(error.details.unwrap()["interactionStatus"], "pending");

    *state.interactions.next_result.lock().await =
        Some(Err(InteractionServiceError::ResolveFailed {
            message: "Provider rejected the resolution".to_string(),
            retryable: false,
            status: InteractionStatus::Invalidated,
        }));
    let terminal = BcsFrame::Request(RequestFrame::new(
        "resolve-3",
        "interaction.resolve",
        Some(serde_json::json!({
            "bcsRunId":"bcs-run-1",
            "interactionId":"interaction-1",
            "idempotencyKey":"idem-2",
            "decision":"deny"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&terminal).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();
    let response = recv_response(&mut rx).await;
    assert!(!response.ok);
    let error = response.error.unwrap();
    assert_eq!(error.code, "interaction_resolve_failed");
    assert!(!error.retryable);
    assert_eq!(error.details.unwrap()["interactionStatus"], "invalidated");

    *state.interactions.next_result.lock().await =
        Some(Err(InteractionServiceError::InvalidRequest(
            "ask_user question target does not allow custom answers; select one of the provided options"
                .to_string(),
        )));
    let invalid_custom_answer = BcsFrame::Request(RequestFrame::new(
        "resolve-4",
        "interaction.resolve",
        Some(serde_json::json!({
            "bcsRunId":"bcs-run-1",
            "interactionId":"interaction-1",
            "idempotencyKey":"idem-3",
            "action":"submit",
            "answers":{"target":{"values":["private cloud"]}}
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&invalid_custom_answer).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();
    let response = recv_response(&mut rx).await;
    assert!(!response.ok);
    let error = response.error.unwrap();
    assert_eq!(error.code, "invalid_request");
    assert_eq!(
        error.message,
        "ask_user question target does not allow custom answers; select one of the provided options"
    );
}

#[tokio::test]
async fn interaction_resolve_preserves_empty_array_skip() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    connect_session_bound(&state, &tx, &mut rx, &mut connection_state).await;

    let request = BcsFrame::Request(RequestFrame::new(
        "resolve-ask-skip",
        "interaction.resolve",
        Some(serde_json::json!({
            "bcsRunId":"bcs-run-1",
            "interactionId":"interaction-ask-skip",
            "idempotencyKey":"idem-ask-skip",
            "action":"submit",
            "answers":{
                "question_1":{"values":[]},
                "question_2":{"values":["人确认后才删"]}
            }
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&request).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();

    let response = recv_response(&mut rx).await;
    assert!(response.ok);
    let commands = state.interactions.resolves.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(
        commands[0].resolution,
        serde_json::json!({
            "action":"submit",
            "answers":{
                "question_1":{"values":[]},
                "question_2":{"values":["人确认后才删"]}
            }
        })
    );
}
