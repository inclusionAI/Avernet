use bcs_protocol::{BcsFrame, RequestFrame};
use bcs_service_api::{BotDeliveryCommand, BotDeliveryKind, BotDeliveryPort, BotDeliveryTarget, ServiceError};
use bcs_ws::{bot::BotConnectionRegistry, shared::RunChannelManager, web::WorkbenchConnectionRegistry};
use tokio::sync::mpsc;

#[tokio::test]
async fn closed_bot_channel_reports_failed_delivery_with_request_id() {
    let registry = BotConnectionRegistry::new();
    let (tx, rx) = mpsc::channel(1);
    registry.connect("disconnected-bot".into(), tx).await;
    drop(rx);
    let (result, logs) = bcs_test_support::capture_request_logs("failed-bot-delivery", registry.deliver(BotDeliveryCommand {
        target: BotDeliveryTarget::WebSocket { bot_id: "disconnected-bot".into() },
        run_id: "run-delivery".into(),
        frame: BcsFrame::Request(RequestFrame::new("run-delivery", "chat.send", None)),
        delivery_kind: BotDeliveryKind::Send,
        provider_transport: Default::default(),
        provider_bypass_headers: Vec::new(),
    })).await;
    let result = result.unwrap();
    assert!(!result.delivered);
    assert_eq!(result.target_bot_id, "disconnected-bot");
    assert!(matches!(result.error, Some(ServiceError::BotNotConnected(id)) if id == "disconnected-bot"));
    let warning = logs.iter().find(|event| event["fields"]["message"] == "bot delivery failed").unwrap();
    assert_eq!(warning["level"], "WARN");
    assert_eq!(warning["fields"]["request_id"], "failed-bot-delivery");
    assert_eq!(warning["fields"]["bot_id"], "disconnected-bot");
}

#[tokio::test]
async fn expired_run_channel_is_removed_and_no_longer_delivers() {
    let registry = RunChannelManager::new();
    let (tx, mut rx) = mpsc::channel(1);
    registry.register("expired-run".into(), "session-diagnostic".into(), tx, None, None).await;
    assert!(registry.send_event("expired-run", "before-expiry".into()).await);
    assert_eq!(rx.recv().await.as_deref(), Some("before-expiry"));
    let (_, logs) = bcs_test_support::capture_request_logs("expired-run-request", async {
        // Zero TTL deterministically expires an existing channel without sleeping.
        registry.cleanup_expired(0).await;
        assert_eq!(registry.run_count().await, 0);
        assert!(!registry.send_event("expired-run", "after-expiry".into()).await);
        assert!(!registry.send_event_by_session("session-diagnostic", "after-expiry".into()).await);
    }).await;
    assert!(rx.recv().await.is_none());
    let warning = logs.iter().find(|event| event["fields"]["message"] == "Removed expired run channels").unwrap();
    assert_eq!(warning["fields"]["request_id"], "expired-run-request");
    assert_eq!(warning["fields"]["removed"], 1);
}

#[tokio::test]
async fn failed_run_delivery_and_full_frontend_queue_remain_observable_without_false_success() {
    let runs = RunChannelManager::new();
    let (tx, rx) = mpsc::channel(1);
    runs.register("failed-run".into(), "session-diagnostic".into(), tx, None, None).await;
    drop(rx);
    let frontends = WorkbenchConnectionRegistry::new();
    let (tx, mut rx) = mpsc::channel(1);
    let connection_id = frontends
        .subscribe("session-diagnostic".into(), tx, None, None)
        .await
        .unwrap();
    assert_eq!(frontends.broadcast("session-diagnostic", "first-event").await, 1);
    let (_, logs) = bcs_test_support::capture_request_logs("full-queue-request", async {
        assert!(!runs.send_event("failed-run", "event".into()).await);
        let delivered = tokio::time::timeout(std::time::Duration::from_secs(1),
            frontends.broadcast("session-diagnostic", "overflow-event")).await
            .expect("a full queue must reject the event without blocking");
        assert_eq!(delivered, 0);
    }).await;
    assert_eq!(rx.recv().await.as_deref(), Some("first-event"));
    assert!(rx.try_recv().is_err(), "overflow must not overwrite the queued event");
    assert_eq!(frontends.connection_count("session-diagnostic").await, 1, "a full queue is not a disconnection");
    assert_eq!(frontends.broadcast("session-diagnostic", "after-drain").await, 1);
    assert_eq!(rx.recv().await.as_deref(), Some("after-drain"));
    for message in ["Failed to send event to client channel", "frontend channel full"] {
        let warning = logs.iter().find(|event| event["fields"]["message"] == message).expect(message);
        assert_eq!(warning["level"], "WARN");
        assert_eq!(warning["fields"]["request_id"], "full-queue-request");
        if message == "frontend channel full" { assert_eq!(warning["fields"]["conn_id"], connection_id); }
    }
}
