use super::*;

#[tokio::test]
async fn bot_v3_interaction_requested_forwards_to_interaction_service() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;

    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-v3-interaction",
        "bot.connect",
        Some(serde_json::json!({
            "bot_id": "bot-interaction",
            "protocol_version": 3,
            "client_kind": "bcs-bridge"
        })),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();
    recv_response(&mut rx).await;

    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "run-interaction".to_string(),
            bot_id: "bot-interaction".to_string(),
            group_id: "group-2".to_string(),
            bcs_session_id: Some("group-2:11223344".to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;

    let payload = serde_json::json!({
        "runId": "run-interaction",
        "sessionId": "group-2:11223344",
        "seq": 1,
        "ts": 100,
        "interactionId": "int-1",
        "phase": "requested",
        "kind": "exec",
        "command": "rm -rf /tmp/x"
    });
    let event = BcsFrame::Event(EventFrame::new("interaction", Some(payload), Some(1)));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let requested = state.interactions.requested.lock().await;
    assert_eq!(requested.len(), 1);
    assert_eq!(requested[0].bcs_run_id, "run-interaction");
    assert_eq!(requested[0].interaction_id, "int-1");
    assert_eq!(requested[0].bcs_session_id, "group-2:11223344");
    assert_eq!(requested[0].group_id, "group-2");
    assert_eq!(requested[0].bot_id, "bot-interaction");
    assert!(matches!(
        requested[0].provider_target,
        BotDeliveryTarget::WebSocket { ref bot_id } if bot_id == "bot-interaction"
    ));
    assert!(matches!(requested[0].kind, bcs_service_api::InteractionKind::Exec));
}

#[tokio::test]
async fn bot_v3_interaction_resolved_forwards_to_interaction_service() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;

    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-v3-interaction-resolved",
        "bot.connect",
        Some(serde_json::json!({
            "bot_id": "bot-interaction-2",
            "protocol_version": 3,
            "client_kind": "bcs-bridge"
        })),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();
    recv_response(&mut rx).await;

    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "run-interaction-2".to_string(),
            bot_id: "bot-interaction-2".to_string(),
            group_id: "group-3".to_string(),
            bcs_session_id: Some("group-3:aabbccdd".to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;

    let payload = serde_json::json!({
        "runId": "run-interaction-2",
        "sessionId": "group-3:aabbccdd",
        "seq": 1,
        "ts": 100,
        "interactionId": "int-2",
        "phase": "resolved",
        "kind": "ask_user",
        "resolution": {"answer": "yes"}
    });
    let event = BcsFrame::Event(EventFrame::new("interaction", Some(payload), Some(1)));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let resolved = state.interactions.resolved.lock().await;
    assert_eq!(resolved.len(), 1);
    assert_eq!(resolved[0].interaction_id, "int-2");
    assert!(matches!(resolved[0].kind, bcs_service_api::InteractionKind::AskUser));
}

#[tokio::test]
async fn bot_v3_interaction_rejects_terminal_run() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;

    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-v3-interaction-terminal",
        "bot.connect",
        Some(serde_json::json!({
            "bot_id": "bot-interaction-3",
            "protocol_version": 3,
            "client_kind": "bcs-bridge"
        })),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();
    recv_response(&mut rx).await;

    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "run-interaction-3".to_string(),
            bot_id: "bot-interaction-3".to_string(),
            group_id: "group-4".to_string(),
            bcs_session_id: Some("group-4:99887766".to_string()),
            deadline_ms: u64::MAX,
            terminal: true,
        })
        .await;

    let payload = serde_json::json!({
        "runId": "run-interaction-3",
        "sessionId": "group-4:99887766",
        "seq": 1,
        "ts": 100,
        "interactionId": "int-3",
        "phase": "requested",
        "kind": "exec"
    });
    let event = BcsFrame::Event(EventFrame::new("interaction", Some(payload), Some(1)));
    let error = dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .expect_err("terminal run must reject new interaction requests");
    assert!(error.to_string().contains("terminal or expired"));

    assert_eq!(state.interactions.requested.lock().await.len(), 0);
    // Terminal cleanup belongs to the bot-terminal observer path that already
    // ran when the terminal event was processed — the interaction rejection
    // branch must not double-invalidate.
    assert!(state.interactions.invalidated.lock().await.is_empty());
}

#[tokio::test]
async fn bot_v3_interaction_rejects_frame_seq_mismatch() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;
    connect_v3_interaction_bot(&state, &tx, &mut rx, &mut registered_bot_id, "bot-seq-mismatch").await;

    // Payload seq (1) disagrees with the outer frame seq (2): fail-closed.
    let payload = serde_json::json!({
        "runId": "run-seq-mismatch",
        "sessionId": "group-6:00000001",
        "seq": 1,
        "ts": 100,
        "interactionId": "int-seq",
        "phase": "requested",
        "kind": "exec"
    });
    let event = BcsFrame::Event(EventFrame::new("interaction", Some(payload), Some(2)));
    let error = dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .expect_err("outer frame seq must match payload seq");
    assert!(error.to_string().contains("seq does not match"));
    assert_eq!(state.interactions.requested.lock().await.len(), 0);
}

#[tokio::test]
async fn bot_v3_interaction_rejects_unknown_run() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;
    connect_v3_interaction_bot(&state, &tx, &mut rx, &mut registered_bot_id, "bot-unknown-run").await;

    // No run context was ever put for this run id.
    let payload = serde_json::json!({
        "runId": "run-never-registered",
        "sessionId": "group-6:00000002",
        "seq": 1,
        "ts": 100,
        "interactionId": "int-unknown",
        "phase": "requested",
        "kind": "exec"
    });
    let event = BcsFrame::Event(EventFrame::new("interaction", Some(payload), Some(1)));
    let error = dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .expect_err("events for unknown runs must be rejected");
    assert!(error.to_string().contains("runId is unknown"));
    assert_eq!(state.interactions.requested.lock().await.len(), 0);
}

#[tokio::test]
async fn bot_v3_interaction_rejects_foreign_bot_run_identity() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;
    connect_v3_interaction_bot(&state, &tx, &mut rx, &mut registered_bot_id, "bot-legit").await;

    // The run context belongs to a different bot: a connected bot must not be
    // able to inject interactions into someone else's run.
    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "run-foreign".to_string(),
            bot_id: "bot-someone-else".to_string(),
            group_id: "group-7".to_string(),
            bcs_session_id: Some("group-7:00000003".to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;

    let payload = serde_json::json!({
        "runId": "run-foreign",
        "sessionId": "group-7:00000003",
        "seq": 1,
        "ts": 100,
        "interactionId": "int-foreign",
        "phase": "requested",
        "kind": "exec"
    });
    let event = BcsFrame::Event(EventFrame::new("interaction", Some(payload), Some(1)));
    let error = dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .expect_err("a bot must not interact on another bot's run");
    assert!(error.to_string().contains("identity mismatch"));
    assert_eq!(state.interactions.requested.lock().await.len(), 0);
}

#[tokio::test]
async fn bot_v3_interaction_rejects_session_identity_mismatch() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;
    connect_v3_interaction_bot(&state, &tx, &mut rx, &mut registered_bot_id, "bot-session-mismatch").await;

    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "run-session".to_string(),
            bot_id: "bot-session-mismatch".to_string(),
            group_id: "group-8".to_string(),
            bcs_session_id: Some("group-8:real-session".to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;

    // Correct bot and run, but the frame claims a session the run never had.
    let payload = serde_json::json!({
        "runId": "run-session",
        "sessionId": "group-8:spoofed-session",
        "seq": 1,
        "ts": 100,
        "interactionId": "int-session",
        "phase": "requested",
        "kind": "exec"
    });
    let event = BcsFrame::Event(EventFrame::new("interaction", Some(payload), Some(1)));
    let error = dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .expect_err("a frame with a session id the run never had must be rejected");
    assert!(error.to_string().contains("identity mismatch"));
    assert_eq!(state.interactions.requested.lock().await.len(), 0);
}

#[tokio::test]
async fn bot_v3_interaction_invalidates_pending_on_expired_run() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;
    connect_v3_interaction_bot(&state, &tx, &mut rx, &mut registered_bot_id, "bot-expired-run").await;

    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "run-expired".to_string(),
            bot_id: "bot-expired-run".to_string(),
            group_id: "group-9".to_string(),
            bcs_session_id: Some("group-9:00000004".to_string()),
            deadline_ms: 1,
            terminal: false,
        })
        .await;

    let payload = serde_json::json!({
        "runId": "run-expired",
        "sessionId": "group-9:00000004",
        "seq": 1,
        "ts": 100,
        "interactionId": "int-expired",
        "phase": "requested",
        "kind": "exec"
    });
    let event = BcsFrame::Event(EventFrame::new("interaction", Some(payload), Some(1)));
    let error = dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .expect_err("expired runs must reject new interaction requests");
    assert!(error.to_string().contains("terminal or expired"));

    // Mirrors the HTTP SSE ingest: an interaction frame arriving after the
    // run deadline sweeps the run's still-pending interactions so the
    // workbench stops offering an unresolvable approval card.
    let invalidated = state.interactions.invalidated.lock().await;
    assert_eq!(*invalidated, vec![("run-expired".to_string(), "run_deadline".to_string())]);
    drop(invalidated);
    assert_eq!(state.interactions.requested.lock().await.len(), 0);
}

#[tokio::test]
async fn bot_v3_interaction_from_unregistered_bot_is_dropped_safely() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id: Option<String> = None;

    let payload = serde_json::json!({
        "runId": "run-orphan",
        "sessionId": "group-5:00000000",
        "seq": 1,
        "ts": 100,
        "interactionId": "int-orphan",
        "phase": "requested",
        "kind": "exec"
    });
    let event = BcsFrame::Event(EventFrame::new("interaction", Some(payload), Some(1)));
    let outcome = dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await;
    assert!(outcome.is_ok(), "unregistered-bot events must be dropped without error, matching agent/chat behavior");
    assert_eq!(state.interactions.requested.lock().await.len(), 0);
}

#[tokio::test]
async fn bot_v3_interaction_duplicate_seq_is_rejected() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;
    connect_v3_interaction_bot(&state, &tx, &mut rx, &mut registered_bot_id, "bot-seq-dup").await;

    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "run-seq-dup".to_string(),
            bot_id: "bot-seq-dup".to_string(),
            group_id: "group-10".to_string(),
            bcs_session_id: Some("group-10:00000005".to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;

    let requested_at = |interaction_id: &str, seq: u64| {
        BcsFrame::Event(EventFrame::new(
            "interaction",
            Some(serde_json::json!({
                "runId": "run-seq-dup",
                "sessionId": "group-10:00000005",
                "seq": seq,
                "ts": 100,
                "interactionId": interaction_id,
                "phase": "requested",
                "kind": "exec"
            })),
            Some(seq),
        ))
    };

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&requested_at("int-first", 1)).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    // A different interaction id at an already-consumed sequence must not
    // reach the service: this is how a stale approval prompt is minted.
    let error = dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&requested_at("int-stale", 1)).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .expect_err("interaction seq must join the shared per-run ledger");
    assert!(error.to_string().contains("duplicate or regressed"));
    assert_eq!(state.interactions.requested.lock().await.len(), 1);
}

#[tokio::test]
async fn bot_v3_interaction_reserves_seq_shared_with_agent_frames() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;
    connect_v3_interaction_bot(&state, &tx, &mut rx, &mut registered_bot_id, "bot-seq-mix").await;

    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "run-seq-mix".to_string(),
            bot_id: "bot-seq-mix".to_string(),
            group_id: "group-11".to_string(),
            bcs_session_id: Some("group-11:00000006".to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;

    let chat_at = |seq: u64, content: &str| {
        BcsFrame::Event(EventFrame::new(
            "chat",
            Some(serde_json::json!({
                "runId": "run-seq-mix",
                "sessionId": "group-11:00000006",
                "seq": seq,
                "ts": 100,
                "state": "delta",
                "content": content
            })),
            Some(seq),
        ))
    };
    let interaction_at = |interaction_id: &str, seq: u64| {
        BcsFrame::Event(EventFrame::new(
            "interaction",
            Some(serde_json::json!({
                "runId": "run-seq-mix",
                "sessionId": "group-11:00000006",
                "seq": seq,
                "ts": 100,
                "interactionId": interaction_id,
                "phase": "requested",
                "kind": "exec"
            })),
            Some(seq),
        ))
    };

    // agent 1 → interaction 2 → agent 2 rejected → interaction 2 rejected →
    // agent 3 accepted: one ledger across event kinds, strictly increasing.
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&chat_at(1, "first")).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&interaction_at("int-mid", 2)).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let agent_error = dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&chat_at(2, "reuse")).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .expect_err("agent frames must not reuse an interaction's seq");
    assert!(agent_error.to_string().contains("duplicate or regressed"));

    let interaction_error = dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&interaction_at("int-reuse", 2)).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .expect_err("interaction frames must not reuse a consumed seq either");
    assert!(interaction_error.to_string().contains("duplicate or regressed"));

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&chat_at(3, "after")).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .expect("the ledger must keep advancing past the interaction");

    assert_eq!(state.message_flow.bot_events.lock().await.len(), 2);
    assert_eq!(state.interactions.requested.lock().await.len(), 1);
}

#[tokio::test]
async fn bot_v3_interaction_releases_seq_when_service_fails() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;
    connect_v3_interaction_bot(&state, &tx, &mut rx, &mut registered_bot_id, "bot-seq-fail").await;

    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "run-seq-fail".to_string(),
            bot_id: "bot-seq-fail".to_string(),
            group_id: "group-12".to_string(),
            bcs_session_id: Some("group-12:00000007".to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;

    let event = BcsFrame::Event(EventFrame::new(
        "interaction",
        Some(serde_json::json!({
            "runId": "run-seq-fail",
            "sessionId": "group-12:00000007",
            "seq": 1,
            "ts": 100,
            "interactionId": "int-retry",
            "phase": "requested",
            "kind": "exec"
        })),
        Some(1),
    ));
    let encoded = serde_json::to_string(&event).unwrap();
    state
        .interactions
        .fail_requested_once
        .store(true, Ordering::Relaxed);

    let first = dispatch_frame(
        &state.dispatch_state,
        &encoded,
        &tx,
        &mut registered_bot_id,
    )
    .await;
    assert!(first.is_err());
    dispatch_frame(
        &state.dispatch_state,
        &encoded,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .expect("same interaction seq should be retryable after a service failure");
    assert_eq!(state.interactions.requested.lock().await.len(), 1);
}
