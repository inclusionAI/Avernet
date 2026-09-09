use std::sync::Arc;
use std::time::Duration;

use bcs_domain::{HumanMessageView, MessageAudience, MessageViewScope, MessageVisibilityDomain};
use bcs_service_api::port::ParticipantViewBindingPort;
use bcs_service_api::{
    FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryPort, FrontendDeliveryTarget,
    RunFallbackDelivery,
};
use bcs_ws::shared::RunChannelManager;
use bcs_ws::web::{WorkbenchConnectionRegistry, WorkbenchFrontendDelivery};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

#[tokio::test]
async fn frontend_delivery_publishes_to_group_connection() {
    let connections = Arc::new(WorkbenchConnectionRegistry::new());
    let run_channels = Arc::new(RunChannelManager::new());
    let delivery = WorkbenchFrontendDelivery::new(connections.clone(), run_channels);

    let (tx, mut rx) = mpsc::channel(1);
    connections
        .subscribe("group-1".to_string(), tx, Some("human_1".to_string()), None)
        .await
        .unwrap();

    let result = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Group {
                group_id: "group-1".to_string(),
            },
            event_json: r#"{"type":"event","event":"chat"}"#.to_string(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::Chat,
            audience: Some(MessageAudience::Public),
        })
        .await
        .unwrap();

    assert_eq!(result.delivered, 1);
    let delivered = tokio::time::timeout(Duration::from_secs(1), rx.recv())
        .await
        .unwrap()
        .unwrap();
    assert!(delivered.contains(r#""event":"chat""#));
}

#[tokio::test]
async fn frontend_delivery_returns_zero_when_group_has_no_connection() {
    let connections = Arc::new(WorkbenchConnectionRegistry::new());
    let run_channels = Arc::new(RunChannelManager::new());
    let delivery = WorkbenchFrontendDelivery::new(connections, run_channels);

    let result = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Group {
                group_id: "missing-group".to_string(),
            },
            event_json: r#"{"type":"event","event":"chat"}"#.to_string(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::Chat,
            audience: Some(MessageAudience::Public),
        })
        .await
        .unwrap();

    assert_eq!(result.delivered, 0);
}

#[tokio::test]
async fn frontend_delivery_falls_back_to_run_channel_when_group_has_no_bound_channel() {
    let connections = Arc::new(WorkbenchConnectionRegistry::new());
    let run_channels = Arc::new(RunChannelManager::new());
    let delivery = WorkbenchFrontendDelivery::new(connections, run_channels.clone());
    let (run_tx, mut run_rx) = mpsc::channel(2);
    run_channels
        .register(
            "run-1".to_string(),
            "chat:session-1".to_string(),
            run_tx,
            Some("http-chat-async".to_string()),
            Some("user-1".to_string()),
        )
        .await;

    let result = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Group {
                group_id: "chat:session-1".to_string(),
            },
            event_json: r#"{"type":"event","event":"chat"}"#.to_string(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: Some(RunFallbackDelivery {
                run_id: "run-1".to_string(),
                session_id: "chat:session-1".to_string(),
                event_json: r#"{"type":"event","event":"chat.event"}"#.to_string(),
            }),
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::Chat,
            audience: Some(MessageAudience::Public),
        })
        .await
        .unwrap();

    assert_eq!(result.delivered, 1);
    let delivered = tokio::time::timeout(Duration::from_secs(1), run_rx.recv())
        .await
        .unwrap()
        .unwrap();
    assert!(delivered.contains(r#""event":"chat.event""#));
    assert!(
        tokio::time::timeout(Duration::from_millis(100), run_rx.recv())
            .await
            .is_err(),
        "fallback should deliver each event to the run channel only once"
    );
}

#[tokio::test]
async fn frontend_delivery_does_not_run_fallback_when_group_channel_is_bound() {
    let connections = Arc::new(WorkbenchConnectionRegistry::new());
    let run_channels = Arc::new(RunChannelManager::new());
    let delivery = WorkbenchFrontendDelivery::new(connections.clone(), run_channels.clone());
    let (frontend_tx, _frontend_rx) = mpsc::channel(1);
    let (run_tx, mut run_rx) = mpsc::channel(1);

    frontend_tx
        .try_send("existing-message".to_string())
        .unwrap();
    connections
        .subscribe(
            "group-1".to_string(),
            frontend_tx,
            Some("human_1".to_string()),
            None,
        )
        .await
        .unwrap();
    run_channels
        .register(
            "run-1".to_string(),
            "group-1".to_string(),
            run_tx,
            Some("http-chat-async".to_string()),
            Some("user-1".to_string()),
        )
        .await;

    let result = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Group {
                group_id: "group-1".to_string(),
            },
            event_json: r#"{"type":"event","event":"chat"}"#.to_string(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: Some(RunFallbackDelivery {
                run_id: "run-1".to_string(),
                session_id: "group-1".to_string(),
                event_json: r#"{"type":"event","event":"chat.event"}"#.to_string(),
            }),
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::Chat,
            audience: Some(MessageAudience::Public),
        })
        .await
        .unwrap();

    assert_eq!(result.delivered, 0);
    assert!(
        tokio::time::timeout(Duration::from_millis(100), run_rx.recv())
            .await
            .is_err()
    );
}

#[tokio::test]
async fn frontend_delivery_excludes_sender_conn_id_from_broadcast() {
    let connections = Arc::new(WorkbenchConnectionRegistry::new());
    let run_channels = Arc::new(RunChannelManager::new());
    let delivery = WorkbenchFrontendDelivery::new(connections.clone(), run_channels);

    let (tx_sender, mut rx_sender) = mpsc::channel(1);
    let (tx_other, mut rx_other) = mpsc::channel(1);

    let conn_id_sender = connections
        .subscribe(
            "group-1".to_string(),
            tx_sender,
            Some("user_1".to_string()),
            None,
        )
        .await
        .unwrap();
    let conn_id_other = connections
        .subscribe(
            "group-1".to_string(),
            tx_other,
            Some("user_1".to_string()),
            None,
        )
        .await
        .unwrap();

    assert_ne!(
        conn_id_sender, conn_id_other,
        "each subscribe returns a unique conn_id"
    );

    let result = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Group {
                group_id: "group-1".to_string(),
            },
            event_json: r#"{"type":"event","event":"chat"}"#.to_string(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: Some(conn_id_sender),
            visibility_domain: MessageVisibilityDomain::Chat,
            audience: Some(MessageAudience::Public),
        })
        .await
        .unwrap();

    assert_eq!(result.delivered, 1);
    assert!(
        tokio::time::timeout(Duration::from_millis(100), rx_sender.recv())
            .await
            .is_err(),
        "sender connection should not receive its own message"
    );
    let other_received = tokio::time::timeout(Duration::from_secs(1), rx_other.recv())
        .await
        .unwrap()
        .unwrap();
    assert!(other_received.contains(r#""event":"chat""#));
}

#[tokio::test]
async fn frontend_delivery_filters_each_human_connection_by_persisted_view() {
    let connections = Arc::new(WorkbenchConnectionRegistry::new());
    let delivery =
        WorkbenchFrontendDelivery::new(connections.clone(), Arc::new(RunChannelManager::new()));
    let (full_tx, mut full_rx) = mpsc::channel(4);
    let (target_tx, mut target_rx) = mpsc::channel(4);
    let (other_tx, mut other_rx) = mpsc::channel(4);

    for (actor_id, scope, tx) in [
        ("human_full", MessageViewScope::Full, full_tx),
        ("human_target", MessageViewScope::Participant, target_tx),
        ("human_other", MessageViewScope::Participant, other_tx),
    ] {
        connections
            .subscribe(
                "session-scoped".to_string(),
                tx,
                Some(actor_id.to_string()),
                Some(HumanMessageView {
                    actor_id: actor_id.to_string(),
                    scope,
                    allow_legacy_unclassified_chat: false,
                }),
            )
            .await
            .unwrap();
    }

    let publish = |audience| FrontendDeliveryCommand {
        target: FrontendDeliveryTarget::Session {
            session_id: "session-scoped".to_string(),
        },
        event_json: r#"{"type":"event","event":"state_machine"}"#.to_string(),
        delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
        run_fallback: None,
        exclude_conn_id: None,
        visibility_domain: MessageVisibilityDomain::StateMachine,
        audience: Some(audience),
    };

    assert_eq!(
        delivery
            .publish(publish(MessageAudience::FullOnly))
            .await
            .unwrap()
            .delivered,
        1,
    );
    assert!(full_rx.recv().await.is_some());
    assert!(
        tokio::time::timeout(Duration::from_millis(50), target_rx.recv())
            .await
            .is_err(),
    );
    assert!(
        tokio::time::timeout(Duration::from_millis(50), other_rx.recv())
            .await
            .is_err(),
    );

    assert_eq!(
        delivery
            .publish(publish(
                MessageAudience::directed(["human_target"]).unwrap(),
            ))
            .await
            .unwrap()
            .delivered,
        2,
    );
    assert!(full_rx.recv().await.is_some());
    assert!(target_rx.recv().await.is_some());
    assert!(
        tokio::time::timeout(Duration::from_millis(50), other_rx.recv())
            .await
            .is_err(),
    );

    assert_eq!(
        delivery
            .publish(publish(MessageAudience::Public))
            .await
            .unwrap()
            .delivered,
        3,
    );
    assert!(full_rx.recv().await.is_some());
    assert!(target_rx.recv().await.is_some());
    assert!(other_rx.recv().await.is_some());
}

#[tokio::test]
async fn frontend_delivery_keeps_unclassified_scoped_events_for_full_only() {
    let connections = Arc::new(WorkbenchConnectionRegistry::new());
    let delivery =
        WorkbenchFrontendDelivery::new(connections.clone(), Arc::new(RunChannelManager::new()));
    let (full_tx, mut full_rx) = mpsc::channel(1);
    let (participant_tx, mut participant_rx) = mpsc::channel(1);
    connections
        .subscribe(
            "session-scoped".to_string(),
            full_tx,
            Some("human_full".to_string()),
            Some(HumanMessageView {
                actor_id: "human_full".to_string(),
                scope: MessageViewScope::Full,
                allow_legacy_unclassified_chat: false,
            }),
        )
        .await
        .unwrap();
    connections
        .subscribe(
            "session-scoped".to_string(),
            participant_tx,
            Some("human_participant".to_string()),
            Some(HumanMessageView {
                actor_id: "human_participant".to_string(),
                scope: MessageViewScope::Participant,
                allow_legacy_unclassified_chat: false,
            }),
        )
        .await
        .unwrap();

    let result = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Session {
                session_id: "session-scoped".to_string(),
            },
            event_json: r#"{"type":"event","event":"state_machine"}"#.to_string(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::StateMachine,
            audience: None,
        })
        .await
        .expect("unclassified legacy event remains compatible");

    assert_eq!(result.delivered, 1);
    assert!(full_rx.recv().await.is_some());
    assert!(
        tokio::time::timeout(Duration::from_millis(50), participant_rx.recv())
            .await
            .is_err()
    );
}

#[tokio::test]
async fn scope_change_closes_existing_views_and_blocks_reconnect_until_finished() {
    let connections = WorkbenchConnectionRegistry::new();
    let (tx, mut rx) = mpsc::channel(2);
    let shutdown = CancellationToken::new();
    connections
        .subscribe_with_shutdown(
            "session-scoped".to_string(),
            tx,
            Some("human_target".to_string()),
            Some(HumanMessageView {
                actor_id: "human_target".to_string(),
                scope: MessageViewScope::Full,
                allow_legacy_unclassified_chat: false,
            }),
            shutdown.clone(),
        )
        .await
        .unwrap();

    let lease = connections
        .begin_scope_change("session-scoped", "human_target")
        .await
        .unwrap();
    let close = rx.recv().await.expect("scope change close event");
    assert!(close.contains("view_scope_changed"));
    assert!(shutdown.is_cancelled());
    assert_eq!(connections.connection_count("session-scoped").await, 0);

    let (blocked_tx, _blocked_rx) = mpsc::channel(1);
    let blocked = connections
        .subscribe(
            "session-scoped".to_string(),
            blocked_tx,
            Some("human_target".to_string()),
            Some(HumanMessageView {
                actor_id: "human_target".to_string(),
                scope: MessageViewScope::Participant,
                allow_legacy_unclassified_chat: false,
            }),
        )
        .await;
    assert!(blocked.is_err());

    connections.finish_scope_change(lease).await.unwrap();
    let (new_tx, _new_rx) = mpsc::channel(1);
    connections
        .subscribe(
            "session-scoped".to_string(),
            new_tx,
            Some("human_target".to_string()),
            Some(HumanMessageView {
                actor_id: "human_target".to_string(),
                scope: MessageViewScope::Participant,
                allow_legacy_unclassified_chat: false,
            }),
        )
        .await
        .expect("reconnect after scope change");
}

#[tokio::test]
async fn scope_change_cancels_socket_when_close_queue_is_full() {
    let connections = WorkbenchConnectionRegistry::new();
    let (tx, _rx) = mpsc::channel(1);
    tx.try_send("queued event".to_string()).unwrap();
    let shutdown = CancellationToken::new();
    connections
        .subscribe_with_shutdown(
            "session-scoped".to_string(),
            tx,
            Some("human_target".to_string()),
            Some(HumanMessageView {
                actor_id: "human_target".to_string(),
                scope: MessageViewScope::Full,
                allow_legacy_unclassified_chat: false,
            }),
            shutdown.clone(),
        )
        .await
        .unwrap();

    connections
        .begin_scope_change("session-scoped", "human_target")
        .await
        .unwrap();

    assert!(shutdown.is_cancelled());
    assert_eq!(connections.connection_count("session-scoped").await, 0);
}

#[tokio::test]
async fn distributed_deployment_allows_instance_local_scope_change_barrier() {
    let connections = WorkbenchConnectionRegistry::new().with_scope_changes_enabled(false);
    assert!(format!("{connections:?}").contains("cluster_scope_changes_enabled: false"));

    let lease = connections
        .begin_scope_change("session-scoped", "human_target")
        .await
        .expect("cluster mode permits instance-local connection invalidation");

    connections
        .finish_scope_change(lease)
        .await
        .expect("finish instance-local scope change");
}
