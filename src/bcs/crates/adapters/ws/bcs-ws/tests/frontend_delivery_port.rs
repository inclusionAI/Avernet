use std::sync::Arc;
use std::time::Duration;

use bcs_service_api::{
    FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryPort, FrontendDeliveryTarget,
    RunFallbackDelivery,
};
use bcs_ws::shared::RunChannelManager;
use bcs_ws::web::{WorkbenchConnectionRegistry, WorkbenchFrontendDelivery};
use tokio::sync::mpsc;

#[tokio::test]
async fn frontend_delivery_publishes_to_group_connection() {
    let connections = Arc::new(WorkbenchConnectionRegistry::new());
    let run_channels = Arc::new(RunChannelManager::new());
    let delivery = WorkbenchFrontendDelivery::new(connections.clone(), run_channels);

    let (tx, mut rx) = mpsc::channel(1);
    connections
        .subscribe("group-1".to_string(), tx, Some("human_1".to_string()))
        .await;

    let result = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Group {
                group_id: "group-1".to_string(),
            },
            event_json: r#"{"type":"event","event":"chat"}"#.to_string(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
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
        )
        .await;
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
        .subscribe("group-1".to_string(), tx_sender, Some("user_1".to_string()))
        .await;
    let conn_id_other = connections
        .subscribe("group-1".to_string(), tx_other, Some("user_1".to_string()))
        .await;

    assert_ne!(conn_id_sender, conn_id_other, "each subscribe returns a unique conn_id");

    let result = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Group {
                group_id: "group-1".to_string(),
            },
            event_json: r#"{"type":"event","event":"chat"}"#.to_string(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: Some(conn_id_sender),
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
