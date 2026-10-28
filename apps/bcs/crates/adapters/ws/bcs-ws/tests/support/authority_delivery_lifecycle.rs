use super::*;

/// Independent resource events (成员/元数据变更) use CURRENT facts
/// at publish time: a mid-run revoked viewer stops receiving THOSE
/// events while its admitted run output streams on — and an event
/// failure never closes the stream (§7.3).
#[tokio::test]
async fn independent_events_use_current_facts_without_touching_streams() {
    let stack = new_stack().await;
    let (_conn_a, mut rx_a) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "op-1").await;

    // An independent event flows while everything holds.
    assert_eq!(
        stack
            .delivery
            .publish(event_command(
                &stack,
                "member-note-1",
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("event publish 1")
            .delivered,
        1
    );
    expect_payload(&mut rx_a, "member-note-1").await;

    // Revoke: the NEXT independent event re-resolves (0 delivered);
    // the still-admitted op-1 keeps flowing.
    stack
        .session_repo
        .remove_participant(&stack.session_id, "human_user-a")
        .await
        .expect("remove human");
    assert_eq!(
        stack
            .delivery
            .publish(event_command(
                &stack,
                "member-note-2",
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("event publish 2")
            .delivered,
        0,
        "the independent event re-checks current facts"
    );
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                "stream-continues",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("op publish")
            .delivered,
        1,
        "the admitted operation ignores the independent event's re-check"
    );
    expect_payload(&mut rx_a, "stream-continues").await;
    assert!(
        stack.registry.connection_count(&stack.session_id).await >= 1,
        "the connection was never cancelled by an event denial"
    );
}

/// Frame-count independence (SQL 计数验收): one admitted operation
/// delivers 1, 20 and 1000 frames with ZERO additional
/// authorization reads per frame — the operation count, never the
/// frame count, scales authorization cost.
#[tokio::test]
async fn frame_volume_never_scales_authorization_reads() {
    let stack = new_stack().await;
    let (_conn_a, mut rx_a) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "op-1").await;
    let baseline_reads = stack.group_snapshot_reads.load(Ordering::SeqCst);
    let baseline_hooks = stack.hook.batch_calls.load(Ordering::SeqCst);

    for n in 0..32 {
        assert_eq!(
            stack
                .delivery
                .publish(op_command(
                    &stack,
                    "op-1",
                    &format!("frame-{n}"),
                    false,
                    MessageVisibilityDomain::Chat,
                    None,
                ))
                .await
                .expect("publish n")
                .delivered,
            1
        );
        expect_payload(&mut rx_a, &format!("frame-{n}")).await;
    }
    assert_eq!(
        stack.group_snapshot_reads.load(Ordering::SeqCst),
        baseline_reads,
        "32 frames performed no resource re-reads"
    );
    assert_eq!(stack.hook.batch_calls.load(Ordering::SeqCst), baseline_hooks, "no authority edge re-reads");
}

/// A saturated slow subscriber loses DATA frames with the slow-
/// consumer policy, while the TERMINAL frame takes the bounded
/// backpressure budget and still lands (不是静默丢终态).
#[tokio::test]
async fn slow_subscriber_terminal_uses_bounded_backpressure() {
    let stack = new_stack().await;
    let (_conn_a, rx_a) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "op-1").await;
    let mut rx_a = rx_a;

    // Saturate the queue WITHOUT a consumer.
    for n in 0..bcs_ws::shared::PROTECTED_FRAME_QUEUE_SIZE {
        let filled = stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                &format!("flood-{n}"),
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("flood publish")
            .delivered;
        assert!(filled <= 1);
    }
    // One more data frame overflows the bounded queue (drop policy).
    let overflowed = stack
        .delivery
        .publish(op_command(
            &stack,
            "op-1",
            "overflow-data",
            false,
            MessageVisibilityDomain::Chat,
            None,
        ))
        .await
        .expect("overflow publish")
        .delivered;
    assert_eq!(overflowed, 0, "the saturated data frame is dropped loudly");

    // Start draining so the bounded terminal backpressure can land.
    let drain = tokio::spawn(async move {
        // Simulate the writer catching up after ~200ms.
        tokio::time::sleep(Duration::from_millis(200)).await;
        let mut last = None;
        while let Ok(Some(frame)) =
            tokio::time::timeout(Duration::from_millis(50), rx_a.recv()).await
        {
            last = Some(frame);
        }
        last
    });
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                "run-complete",
                true,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("terminal publish")
            .delivered,
        1,
        "the terminal frame took the bounded backpressure instead of being dropped"
    );
    let last = drain.await.expect("drain joins");
    assert!(
        matches!(last, Some(OutboundFrame::Terminal { .. })),
        "the drained tail IS the typed terminal"
    );
}

/// Conversation-level Bot view: the admitted fanout honors the
/// permit's frozen visibility even when the producer metadata
/// changes per frame (FullOnly 仍被许可谓词过滤).
#[tokio::test]
async fn per_frame_visibility_still_applies_through_the_frozen_permit() {
    let stack = new_stack().await;
    let (conn_a, mut rx_a) = subscribe(&stack, "user-a", "bot-worker").await;
    // Tighten the Session scope for the Human override-free Bot view
    // down to Participant by RESEEDING the participant scope.
    let _ = conn_a;
    stack
        .session_repo
        .remove_participant(&stack.session_id, "bot-worker")
        .await
        .expect("remove worker");
    stack
        .session_repo
        .add_participant(
            &stack.session_id,
            participant("bot-worker", ActorKind::Bot, ParticipantRole::Worker, MessageViewScope::Participant),
        )
        .await
        .expect("reseed scoped worker");
    admit(&stack, "op-1").await;

    // FullOnly is outside the frozen Participant scope → filtered
    // at the enqueue boundary.
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                "full-only",
                false,
                MessageVisibilityDomain::ManagerWorker,
                Some(MessageAudience::FullOnly),
            ))
            .await
            .expect("full-only publish")
            .delivered,
        0
    );
    expect_quiet(&mut rx_a).await;

    // A public frame of the SAME operation flows with the same permit.
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                "public-note",
                false,
                MessageVisibilityDomain::ManagerWorker,
                Some(MessageAudience::Public),
            ))
            .await
            .expect("public publish")
            .delivered,
        1
    );
    expect_payload(&mut rx_a, "public-note").await;
    // Directed to THIS actor also flows.
    let directed = MessageAudience::directed(["bot-worker"]).expect("directed audience");
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                "directed-note",
                false,
                MessageVisibilityDomain::ManagerWorker,
                Some(directed),
            ))
            .await
            .expect("directed publish")
            .delivered,
        1
    );
    expect_payload(&mut rx_a, "directed-note").await;
}

/// Failure doubles sanity: with the Application resolver replaced
/// by an always-failing one, the fanout admits NOTHING at event
/// level and the send-level output admission installs nothing —
/// both fail closed without panics.
#[tokio::test]
async fn failing_admission_fails_everything_closed() {
    let permits = Arc::new(OutputPermitStore::new());
    let registry = Arc::new(
        WorkbenchConnectionRegistry::new().with_output_permits(permits.clone()),
    );
    registry.set_delivery_admission(Arc::new(FailingAdmission));
    let run_channels = Arc::new(RunChannelManager::new().with_output_permits(permits));
    let delivery = Arc::new(WorkbenchFrontendDelivery::new(
        registry.clone(),
        run_channels.clone(),
    ));
    let output_admission = Arc::new(WorkbenchOutputAdmission::new(
        registry.clone(),
        Arc::new(FailingAdmission),
    ));
    let (control_tx, _control_rx) = mpsc::channel(8);
    let (frame_tx, mut frame_rx) = mpsc::channel(4);
    registry
        .subscribe_protected_with_shutdown(
            "session-x".to_string(),
            control_tx,
            Some("human_user-a".to_string()),
            None,
            tokio_util::sync::CancellationToken::new(),
            ProtectedDeliveryBinding {
                tenant: None,
                user_id: "user-a".to_string(),
                group_id: "group-x".to_string(),
                session_id: Some("session-x".to_string()),
                view_actor_id: "human_user-a".to_string(),
                connection_instance_id: 0,
            },
            frame_tx,
        )
        .await
        .expect("subscribe");

    output_admission
        .admit_output_operation(OutputOperationAdmission {
            operation_id: "op-x".to_string(),
            group_id: "group-x".to_string(),
            session_id: Some("session-x".to_string()),
        })
        .await
        .expect("admission failure is absorbed upstream");
    let delivered = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Session {
                session_id: "session-x".to_string(),
            },
            event_json: "evt".to_string(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::Chat,
            audience: None,
            operation_id: Some("op-x".to_string()),
            terminal: false,
            run_id: None,
        })
        .await
        .expect("op publish");
    assert_eq!(delivered.delivered, 0);
    let delivered_event = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Session {
                session_id: "session-x".to_string(),
            },
            event_json: "evt".to_string(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::Chat,
            audience: None,
            operation_id: None,
            terminal: false,
            run_id: None,
        })
        .await
        .expect("event publish");
    assert_eq!(delivered_event.delivered, 0);
    assert!(frame_rx.try_recv().is_err());
    assert!(registry.events_skipped_auth_failure() >= 1);
}

/// The recording double proves the batch shape of boundary calls:
/// one operation admission resolves its whole candidate set in ONE
/// resolver call, and frames never re-enter the resolver.
#[tokio::test]
async fn boundary_calls_are_one_batch_per_operation_and_zero_per_frame() {
    let permits = Arc::new(OutputPermitStore::new());
    let registry = Arc::new(
        WorkbenchConnectionRegistry::new().with_output_permits(permits.clone()),
    );
    let admission = Arc::new(RecordingAdmission::new());
    registry.set_delivery_admission(admission.clone());
    let output_admission = Arc::new(WorkbenchOutputAdmission::new(
        registry.clone(),
        admission.clone(),
    ));
    let run_channels = Arc::new(RunChannelManager::new().with_output_permits(permits));
    let delivery = Arc::new(WorkbenchFrontendDelivery::new(
        registry.clone(),
        run_channels.clone(),
    ));
    for conn in 0..3u64 {
        let (control_tx, _control_rx) = mpsc::channel(8);
        let (frame_tx, _frame_rx) = mpsc::channel(8);
        registry
            .subscribe_protected_with_shutdown(
                "session-x".to_string(),
                control_tx,
                Some("human_view".to_string()),
                None,
                tokio_util::sync::CancellationToken::new(),
                ProtectedDeliveryBinding {
                    tenant: None,
                    user_id: "user-a".to_string(),
                    group_id: "group-x".to_string(),
                    session_id: Some("session-x".to_string()),
                    view_actor_id: "human_view".to_string(),
                    connection_instance_id: 0,
                },
                frame_tx,
            )
            .await
            .unwrap_or_else(|_| panic!("subscribe {conn}"));
    }
    let calls_before = admission.call_count();
    output_admission
        .admit_output_operation(OutputOperationAdmission {
            operation_id: "op-batch".to_string(),
            group_id: "group-x".to_string(),
            session_id: Some("session-x".to_string()),
        })
        .await
        .expect("admission");
    assert_eq!(
        admission.call_count() - calls_before,
        1,
        "ONE resolution per operation regardless of the candidate count"
    );
    for n in 0..16 {
        delivery
            .publish(FrontendDeliveryCommand {
                target: FrontendDeliveryTarget::Session {
                    session_id: "session-x".to_string(),
                },
                event_json: format!("f-{n}"),
                delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
                run_fallback: None,
                exclude_conn_id: None,
                visibility_domain: MessageVisibilityDomain::Chat,
                audience: None,
                operation_id: Some("op-batch".to_string()),
                terminal: false,
                run_id: None,
            })
            .await
            .expect("publish");
    }
    assert_eq!(
        admission.call_count() - calls_before,
        1,
        "16 frames re-entered the resolver"
    );
}

// ---------------------------------------------------------------------------
// Stream lifecycle (Run terminal ≠ operation complete) and the
// slow-consumer terminal floor — the F1 follow-up acceptance matrix.
// ---------------------------------------------------------------------------

/// A run sink's terminal ends THAT stream only: its sibling run of
/// the same admitted operation keeps flowing (one WebSend can drive
/// several bot Runs).
#[tokio::test]
async fn one_run_sink_terminal_never_closes_a_sibling_run() {
    let stack = new_stack().await;
    let (conn, mut fanout) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "multi-run-operation").await;
    let mut run_a = register_run_sink(&stack, "multi-run-operation", "run-a", conn).await;
    let mut run_b = register_run_sink(&stack, "multi-run-operation", "run-b", conn).await;

    assert!(
        stack
            .run_channels
            .send_visible_event(
                "run-a",
                "a-final".to_string(),
                MessageVisibilityDomain::Chat,
                None,
                true,
            )
            .await
    );
    expect_payload(&mut run_a, "a-final").await;
    assert!(
        stack
            .run_channels
            .send_visible_event(
                "run-b",
                "b-delta".to_string(),
                MessageVisibilityDomain::Chat,
                None,
                false,
            )
            .await,
        "run-a's terminal must not retire run-b's admitted output"
    );
    expect_payload(&mut run_b, "b-delta").await;
    let _ = fanout.try_recv();
}

/// Fanout lane: a stream terminal retires only its own stream; the
/// operation closes when EVERY registered stream is terminal.
#[tokio::test]
async fn fanout_stream_terminal_retires_only_its_own_stream() {
    let stack = new_stack().await;
    let (_conn, mut rx) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "fanout-multi-run").await;
    stack
        .output_admission
        .register_operation_runs(
            "fanout-multi-run",
            vec!["run-a".to_string(), "run-b".to_string()],
        )
        .await
        .expect("register streams");

    // Interleaved deltas: everything flows.
    for (run, payload) in [("run-a", "a-1"), ("run-b", "b-1")] {
        assert_eq!(
            stack
                .delivery
                .publish(run_command(
                    &stack,
                    "fanout-multi-run",
                    Some(run),
                    payload,
                    false,
                    MessageVisibilityDomain::Chat,
                    None,
                ))
                .await
                .expect("delta publish")
                .delivered,
            1
        );
        expect_payload(&mut rx, payload).await;
    }

    // run-a's terminal: delivered, and run-b keeps flowing.
    assert_eq!(
        stack
            .delivery
            .publish(run_command(
                &stack,
                "fanout-multi-run",
                Some("run-a"),
                "a-final",
                true,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("run-a terminal")
            .delivered,
        1
    );
    expect_payload(&mut rx, "a-final").await;
    assert_eq!(
        stack
            .delivery
            .publish(run_command(
                &stack,
                "fanout-multi-run",
                Some("run-b"),
                "b-2",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("run-b delta")
            .delivered,
        1,
        "a sibling stream survives another stream's terminal"
    );
    expect_payload(&mut rx, "b-2").await;
    // Post-terminal leakage on run-a is refused and counted.
    assert_eq!(
        stack
            .delivery
            .publish(run_command(
                &stack,
                "fanout-multi-run",
                Some("run-a"),
                "a-late",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("run-a late frame")
            .delivered,
        0,
        "run-a refuses frames after its own terminal"
    );

    // The last registered stream's terminal completes the operation:
    // even a DUPLICATE terminal afterwards is refused and counted.
    assert_eq!(
        stack
            .delivery
            .publish(run_command(
                &stack,
                "fanout-multi-run",
                Some("run-b"),
                "b-final",
                true,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("run-b terminal")
            .delivered,
        1
    );
    expect_payload(&mut rx, "b-final").await;
    assert_eq!(
        stack
            .delivery
            .publish(run_command(
                &stack,
                "fanout-multi-run",
                Some("run-b"),
                "b-final-duplicate",
                true,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("duplicate terminal")
            .delivered,
        0,
        "with every registered stream terminal the operation refuses further frames"
    );
    assert!(stack.permits.frames_dropped_after_terminal() >= 1);
}

/// A terminal observed BEFORE its stream registration (a fast
/// managed callback) still counts: later registration completes the
/// bookkeeping — completion is never inferred from "currently no
/// runs".
#[tokio::test]
async fn fast_terminal_before_registration_completes_on_registration() {
    let stack = new_stack().await;
    let (_conn, mut rx) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "fast-op").await;
    stack
        .permits
        .mark_run_terminal("fast-op", "run-a")
        .await;
    // run-a's terminal arrived before registration; run-b registered
    // afterwards and still flows.
    stack
        .output_admission
        .register_operation_runs("fast-op", vec!["run-a".to_string(), "run-b".to_string()])
        .await
        .expect("register");
    assert_eq!(
        stack
            .delivery
            .publish(run_command(
                &stack,
                "fast-op",
                Some("run-b"),
                "b-delta",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("b delta")
            .delivered,
        1
    );
    expect_payload(&mut rx, "b-delta").await;
    stack
        .permits
        .mark_run_terminal("fast-op", "run-b")
        .await;
    assert_eq!(
        stack
            .delivery
            .publish(run_command(
                &stack,
                "fast-op",
                Some("run-b"),
                "b-late",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("late frame")
            .delivered,
        0,
        "everything is closed once both registered streams are terminal"
    );
}

/// The slow-consumer transport floor: a terminal that cannot land
/// within the bounded backpressure budget RETIRES the saturated
/// connection — an observable transport failure (the client
/// reconnects), never a silent live stream without its final.
#[tokio::test]
async fn terminal_timeout_retires_the_saturated_connection() {
    let stack = new_stack().await;
    let (_conn, mut rx) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "slow-operation").await;
    for n in 0..bcs_ws::shared::PROTECTED_FRAME_QUEUE_SIZE {
        assert_eq!(
            stack
                .delivery
                .publish(op_command(
                    &stack,
                    "slow-operation",
                    &format!("delta-{n}"),
                    false,
                    MessageVisibilityDomain::Chat,
                    None,
                ))
                .await
                .expect("fill")
                .delivered,
            1
        );
    }
    let result = stack
        .delivery
        .publish(op_command(
            &stack,
            "slow-operation",
            "final",
            true,
            MessageVisibilityDomain::Chat,
            None,
        ))
        .await
        .expect("terminal publish");
    assert_eq!(
        result.delivered, 0,
        "the terminal could not land within the bounded budget"
    );
    assert_eq!(
        stack.registry.connection_count(&stack.session_id).await,
        0,
        "the saturated connection was retired — transport failure is observable"
    );
    while let Ok(frame) = rx.try_recv() {
        assert!(
            !matches!(frame, OutboundFrame::Terminal { .. }),
            "no terminal was silently queued"
        );
    }
}

/// The run-sink lane applies the same floor: a lost terminal retires
/// the connection and is counted.
#[tokio::test]
async fn run_sink_terminal_timeout_retires_and_is_counted() {
    let stack = new_stack().await;
    let (conn, _rx) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "sink-slow-op").await;
    let mut sink_rx = register_run_sink(&stack, "sink-slow-op", "run-slow", conn).await;
    for n in 0..bcs_ws::shared::PROTECTED_FRAME_QUEUE_SIZE {
        assert!(
            stack
                .run_channels
                .send_visible_event(
                    "run-slow",
                    format!("delta-{n}"),
                    MessageVisibilityDomain::Chat,
                    None,
                    false,
                )
                .await,
            "delta {n} buffered"
        );
    }
    assert!(
        !stack
            .run_channels
            .send_visible_event(
                "run-slow",
                "final".to_string(),
                MessageVisibilityDomain::Chat,
                None,
                true,
            )
            .await,
        "the terminal was lost past the budget"
    );
    assert_eq!(stack.run_channels.terminal_frames_lost(), 1);
    assert_eq!(
        stack.registry.connection_count(&stack.session_id).await,
        0,
        "the saturated connection was retired through the shared hook"
    );
    assert!(sink_rx.try_recv().is_ok(), "buffered deltas remain drainable");
}
