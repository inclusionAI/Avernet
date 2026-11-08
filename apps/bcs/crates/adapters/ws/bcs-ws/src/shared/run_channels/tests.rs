use super::*;
use opentelemetry::trace::{SpanContext, SpanId, TraceFlags, TraceId, TraceState};

fn test_span_context() -> SpanContext {
    SpanContext::new(
        TraceId::from_hex("0af7651916cd43dd8448eb211c80319c").unwrap(),
        SpanId::from_hex("b7ad6b7169203331").unwrap(),
        TraceFlags::SAMPLED,
        false,
        TraceState::default(),
    )
}

#[tokio::test]
async fn register_internal_and_send_event() {
    let manager = RunChannelManager::new();
    let (tx, mut rx) = mpsc::channel(16);

    manager
        .register_internal(
            "run-1".to_string(),
            "grp-001".to_string(),
            tx,
            Some("webui".to_string()),
            Some("user-123".to_string()),
        )
        .await;

    assert!(manager.is_registered("run-1").await);

    let sent = manager.send_event("run-1", "test-event".to_string()).await;
    assert!(sent);

    let event = rx.recv().await;
    assert_eq!(event, Some("test-event".to_string()));

    let runs = manager.get_session_runs("grp-001").await;
    assert_eq!(runs, vec!["run-1"]);

    manager.unregister("run-1").await;
    assert!(!manager.is_registered("run-1").await);
}

#[tokio::test]
async fn send_to_unregistered_run_fails() {
    let manager = RunChannelManager::new();

    let sent = manager.send_event("unknown-run", "test".to_string()).await;
    assert!(!sent);
}

#[tokio::test]
async fn alias_forwards_event_and_preserves_session_key() {
    let manager = RunChannelManager::new();
    let (tx, mut rx) = mpsc::channel(16);

    manager
        .register_internal(
            "outer-run".to_string(),
            "grp-001:abcdef12".to_string(),
            tx,
            Some("webui".to_string()),
            Some("user-123".to_string()),
        )
        .await;

    assert!(
        manager
            .register_alias("sub-run".to_string(), "outer-run".to_string())
            .await
    );
    assert!(manager.is_registered("sub-run").await);
    assert_eq!(
        manager.session_for_run("sub-run").await.as_deref(),
        Some("grp-001:abcdef12")
    );

    let sent = manager.send_event("sub-run", "test-event".to_string()).await;
    assert!(sent);
    assert_eq!(rx.recv().await, Some("test-event".to_string()));

    let runs = manager.get_session_runs("grp-001:abcdef12").await;
    assert_eq!(runs, vec!["outer-run", "sub-run"]);

    manager.unregister("sub-run").await;
    assert!(!manager.is_registered("outer-run").await);
    assert!(!manager.is_registered("sub-run").await);
    assert!(manager.get_session_runs("grp-001:abcdef12").await.is_empty());
}

#[tokio::test]
async fn trace_parent_lookup_resolves_alias_and_cleans_up_with_channel() {
    let manager = RunChannelManager::new();
    let (tx, _rx) = mpsc::channel(16);
    let trace_parent = test_span_context();

    manager
        .register_internal_with_trace_parent(
            "outer-run".to_string(),
            "grp-001".to_string(),
            tx,
            Some("http-chat-async".to_string()),
            None,
            Some(trace_parent.clone()),
        )
        .await;
    assert!(
        manager
            .register_alias("inner-run".to_string(), "outer-run".to_string())
            .await
    );

    assert_eq!(manager.trace_parent("inner-run").await, Some(trace_parent));
    manager.unregister("inner-run").await;
    assert_eq!(manager.trace_parent("outer-run").await, None);
    assert_eq!(manager.trace_parent("inner-run").await, None);
}

#[tokio::test]
async fn invalid_trace_parent_is_not_retained() {
    let manager = RunChannelManager::new();
    let (tx, _rx) = mpsc::channel(1);

    manager
        .register_internal_with_trace_parent(
            "run-invalid".to_string(),
            "group-1".to_string(),
            tx,
            Some("http-chat-async".to_string()),
            None,
            Some(SpanContext::empty_context()),
        )
        .await;

    assert_eq!(manager.trace_parent("run-invalid").await, None);
}

async fn install_op(
    manager: &RunChannelManager,
    operation_id: &str,
    conn: u64,
    scope: bcs_domain::MessageViewScope,
) {
    manager
        .output_permits()
        .install_permits(
            &bcs_service_api::port::OutputOperationAdmission {
                operation_id: operation_id.to_string(),
                group_id: "group-a".to_string(),
                session_id: Some("group-a:s1".to_string()),
            },
            vec![bcs_service_api::application::v1::AdmittedRecipient {
                connection_instance_id: conn,
                view_actor_id: "human_user-a".to_string(),
                scope,
                allow_legacy_unclassified_chat: true,
            }],
        )
        .await;
}

#[tokio::test]
async fn workbench_sink_flows_only_through_the_operation_permit() {
    let manager = Arc::new(RunChannelManager::new());
    install_op(&manager, "op-1", 7, bcs_domain::MessageViewScope::Full).await;
    let (frame_tx, mut frame_rx) = mpsc::channel(4);
    manager
        .register_workbench(
            "run-wb".to_string(),
            "group-a:s1".to_string(),
            frame_tx,
            7,
            "op-1",
            Some("workbench-ws".to_string()),
            Some("human_user-a".to_string()),
            None,
        )
        .await;

    assert!(
        manager
            .send_visible_event(
                "run-wb",
                "payload".to_string(),
                MessageVisibilityDomain::StateMachine,
                Some(&MessageAudience::FullOnly),
                false,
            )
            .await,
        "the bound operation holds a conn-7 permit"
    );
    match frame_rx.recv().await {
        Some(OutboundFrame::Data { payload }) => assert_eq!(payload, "payload"),
        other => panic!("expected a data frame, got {other:?}"),
    }
}

#[tokio::test]
async fn workbench_sink_without_a_permit_or_with_a_foreign_instance_is_refused() {
    // Unbound run: no operation binding at all.
    let manager = RunChannelManager::new();
    let (frame_tx, mut frame_rx) = mpsc::channel(4);
    manager
        .register_sink(
            "run-unbound".to_string(),
            "group-a:s1".to_string(),
            RunClientSink::Workbench {
                tx: frame_tx,
                connection_instance_id: 7,
            },
            None,
            None,
            None,
        )
        .await;
    assert!(
        !manager
            .send_visible_event(
                "run-unbound",
                "payload".to_string(),
                MessageVisibilityDomain::Chat,
                None,
                false,
            )
            .await,
        "an unbound run admits no Workbench frame"
    );
    assert!(frame_rx.try_recv().is_err());

    // Permit belongs to connection 7; the sink is instance 8.
    let manager = Arc::new(RunChannelManager::new());
    install_op(&manager, "op-2", 7, bcs_domain::MessageViewScope::Full).await;
    let (frame_tx, mut frame_rx) = mpsc::channel(4);
    manager
        .register_workbench(
            "run-wb".to_string(),
            "group-a:s1".to_string(),
            frame_tx,
            8,
            "op-2",
            None,
            None,
            None,
        )
        .await;
    assert!(
        !manager
            .send_visible_event(
                "run-wb",
                "payload".to_string(),
                MessageVisibilityDomain::Chat,
                None,
                false,
            )
            .await,
        "a replaced connection never matches another instance's permit"
    );
    assert!(frame_rx.try_recv().is_err());
}

#[tokio::test]
async fn unregister_unbinds_the_run_from_its_operation() {
    let manager = Arc::new(RunChannelManager::new());
    install_op(&manager, "op-3", 7, bcs_domain::MessageViewScope::Full).await;
    let (frame_tx, mut frame_rx) = mpsc::channel(4);
    manager
        .register_workbench(
            "run-wb".to_string(),
            "group-a:s1".to_string(),
            frame_tx,
            7,
            "op-3",
            None,
            None,
            None,
        )
        .await;
    manager.unregister("run-wb").await;
    assert!(
        !manager
            .send_visible_event(
                "run-wb",
                "late".to_string(),
                MessageVisibilityDomain::Chat,
                None,
                false,
            )
            .await,
        "frames after unregistration have neither sink nor binding"
    );
    assert!(frame_rx.try_recv().is_err());
}

#[tokio::test]
async fn terminal_frames_are_typed_and_close_the_operation_stream() {
    let manager = Arc::new(RunChannelManager::new());
    install_op(&manager, "op-4", 7, bcs_domain::MessageViewScope::Full).await;
    let (frame_tx, mut frame_rx) = mpsc::channel(4);
    manager
        .register_workbench(
            "run-wb".to_string(),
            "group-a:s1".to_string(),
            frame_tx,
            7,
            "op-4",
            None,
            None,
            None,
        )
        .await;
    assert!(
        manager
            .send_visible_event(
                "run-wb",
                "done".to_string(),
                MessageVisibilityDomain::Chat,
                None,
                true,
            )
            .await
    );
    match frame_rx.recv().await {
        Some(OutboundFrame::Terminal { payload }) => assert_eq!(payload, "done"),
        other => panic!("expected the typed terminal frame, got {other:?}"),
    }
    assert!(
        !manager
            .send_visible_event(
                "run-wb",
                "post-terminal".to_string(),
                MessageVisibilityDomain::Chat,
                None,
                false,
            )
            .await,
        "the operation stopped accepting new frames after its terminal"
    );
    assert!(frame_rx.try_recv().is_err());
}

#[tokio::test]
async fn session_fallback_serves_internal_sinks_but_never_a_workbench_sink() {
    // Workbench sink registered LAST for the session: the fallback
    // must refuse it (spec §7.2 — no last-run permit borrowing).
    let manager = Arc::new(RunChannelManager::new());
    install_op(&manager, "op-5", 7, bcs_domain::MessageViewScope::Full).await;
    let (internal_tx, _internal_rx) = mpsc::channel(4);
    manager
        .register_internal(
            "run-internal".to_string(),
            "group-a:s1".to_string(),
            internal_tx,
            None,
            None,
        )
        .await;
    let (frame_tx, mut frame_rx) = mpsc::channel(4);
    manager
        .register_workbench(
            "run-wb".to_string(),
            "group-a:s1".to_string(),
            frame_tx,
            7,
            "op-5",
            None,
            None,
            None,
        )
        .await;
    assert!(
        !manager
            .send_visible_event_by_session(
                "group-a:s1",
                "event".to_string(),
                MessageVisibilityDomain::Chat,
                None,
                false,
            )
            .await,
        "the Workbench sink is refused by the session fallback"
    );
    assert!(
        frame_rx.try_recv().is_err(),
        "the Workbench sink is unreachable through the session fallback"
    );

    // Reordered session (Internal last): the SAME fallback keeps
    // its legacy Internal contract.
    let manager = RunChannelManager::new();
    let (frame_tx, _frame_rx) = mpsc::channel(4);
    manager
        .register_sink(
            "run-wb".to_string(),
            "group-a:s1".to_string(),
            RunClientSink::Workbench {
                tx: frame_tx,
                connection_instance_id: 7,
            },
            None,
            None,
            None,
        )
        .await;
    let (internal_tx, mut internal_rx) = mpsc::channel(4);
    manager
        .register_internal(
            "run-internal".to_string(),
            "group-a:s1".to_string(),
            internal_tx,
            None,
            None,
        )
        .await;
    assert!(
        manager
            .send_event_by_session("group-a:s1", "legacy".to_string())
            .await
    );
    assert_eq!(internal_rx.recv().await.as_deref(), Some("legacy"));
}

#[tokio::test]
async fn alias_shares_the_source_operations_permit() {
    let manager = Arc::new(RunChannelManager::new());
    install_op(&manager, "op-6", 7, bcs_domain::MessageViewScope::Full).await;
    let (frame_tx, mut frame_rx) = mpsc::channel(4);
    manager
        .register_workbench(
            "outer-run".to_string(),
            "group-a:s1".to_string(),
            frame_tx,
            7,
            "op-6",
            Some("workbench-ws".to_string()),
            None,
            None,
        )
        .await;
    assert!(
        manager
            .register_alias("inner-run".to_string(), "outer-run".to_string())
            .await
    );
    assert!(
        manager
            .send_visible_event(
                "inner-run",
                "via-alias".to_string(),
                MessageVisibilityDomain::Chat,
                None,
                false,
            )
            .await,
        "the alias resolves to the SAME bound operation's permit"
    );
    match frame_rx.recv().await {
        Some(OutboundFrame::Data { payload }) => assert_eq!(payload, "via-alias"),
        other => panic!("expected a data frame, got {other:?}"),
    }
}

#[tokio::test]
async fn control_frames_flow_through_the_workbench_sink_unchanged() {
    let manager = RunChannelManager::new();
    let (frame_tx, mut frame_rx) = mpsc::channel(4);
    manager
        .register_sink(
            "run-wb".to_string(),
            "group-a:s1".to_string(),
            RunClientSink::Workbench {
                tx: frame_tx,
                connection_instance_id: 7,
            },
            None,
            None,
            None,
        )
        .await;

    // The manager never fabricates control frames itself; the
    // sink accepts them from the writer side without any permit.
    let RunClientSink::Workbench { tx, .. } = manager.sink_of("run-wb").await.unwrap()
    else {
        panic!("workbench sink expected");
    };
    tx.send(OutboundFrame::ConnectionControl {
        payload: "{}".to_string(),
    })
    .await
    .unwrap();
    assert!(matches!(
        frame_rx.recv().await,
        Some(OutboundFrame::ConnectionControl { .. })
    ));
}
