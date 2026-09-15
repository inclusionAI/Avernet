use std::time::Duration;

use bcs_protocol::{BcsFrame, RequestFrame, ResponseFrame};
use bcs_service_api::{
    BotAbortDeliveryCommand, BotDeliveryCommand, BotDeliveryKind, BotDeliveryPort,
    BotDeliveryTarget, ServiceError,
};
use bcs_ws::bot::BotConnectionRegistry;
use tokio::sync::mpsc;

#[tokio::test]
async fn pinned_operations_never_cross_a_bot_reconnection() {
    let registry = BotConnectionRegistry::new();
    let target = BotDeliveryTarget::WebSocket { bot_id: "pinned-bot".into() };
    let (old_tx, mut old_rx) = mpsc::channel(2);
    registry.connect("pinned-bot".into(), old_tx).await;
    let old_id = registry.connection_identity(&target).await.unwrap();
    let (new_tx, mut new_rx) = mpsc::channel(2);
    registry.connect("pinned-bot".into(), new_tx).await;
    let new_id = registry.connection_identity(&target).await.unwrap();
    assert_ne!(old_id, new_id);
    let command = || BotDeliveryCommand {
        target: target.clone(), run_id: "run-pinned".into(),
        frame: BcsFrame::Request(RequestFrame::new("request-pinned", "chat.send", None)),
        delivery_kind: BotDeliveryKind::Send,
        provider_transport: Default::default(), provider_bypass_headers: Vec::new(),
    };
    assert!(!registry.deliver_on_connection(command(), &old_id).await.unwrap().delivered);
    let aborted = registry.abort_on_connection(BotAbortDeliveryCommand {
        target: target.clone(), command_id: "abort-pinned".into(), group_id: "g".into(),
        session_id: "original-session-key".into(), run_id: Some("run-pinned".into()),
        provider_bypass_headers: Vec::new(), timeout_ms: 100,
    }, &old_id).await;
    assert!(aborted.is_err());
    assert!(new_rx.try_recv().is_err());
    assert!(old_rx.try_recv().is_err());
    assert!(registry.deliver_on_connection(command(), &new_id).await.unwrap().delivered);
    assert!(new_rx.recv().await.unwrap().contains("request-pinned"));
}

#[tokio::test]
async fn bot_registry_delivers_frame_to_connected_bot() {
    let registry = BotConnectionRegistry::new();
    let (tx, mut rx) = mpsc::channel(1);
    registry.connect("bot-1".to_string(), tx).await;

    let frame = BcsFrame::Request(RequestFrame::new("run-1", "chat.send", None));
    let result = registry
        .deliver(BotDeliveryCommand {
            target: BotDeliveryTarget::WebSocket {
                bot_id: "bot-1".to_string(),
            },
            run_id: "run-1".to_string(),
            frame,
            delivery_kind: BotDeliveryKind::Send,
            provider_transport: Default::default(),
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    assert!(result.delivered);
    let delivered = tokio::time::timeout(Duration::from_secs(1), rx.recv())
        .await
        .unwrap()
        .unwrap();
    assert!(delivered.contains("chat.send"));
}

#[tokio::test]
async fn bot_registry_returns_not_delivered_when_bot_disconnected() {
    let registry = BotConnectionRegistry::new();

    let frame = BcsFrame::Request(RequestFrame::new("run-1", "chat.send", None));
    let result = registry
        .deliver(BotDeliveryCommand {
            target: BotDeliveryTarget::WebSocket {
                bot_id: "missing-bot".to_string(),
            },
            run_id: "run-1".to_string(),
            frame,
            delivery_kind: BotDeliveryKind::Send,
            provider_transport: Default::default(),
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    assert!(!result.delivered);
    assert_eq!(result.target_bot_id, "missing-bot");
}

#[tokio::test]
async fn bot_registry_sends_request_and_resolves_response() {
    let registry = std::sync::Arc::new(BotConnectionRegistry::new());
    let (tx, mut rx) = mpsc::channel(1);
    registry.connect("bot-1".to_string(), tx).await;

    let request_registry = registry.clone();
    let response_handle = tokio::spawn(async move {
        request_registry
            .send_request(
                "bot-1",
                "chat.history",
                serde_json::json!({"session_key": "group-1"}),
                1000,
            )
            .await
    });

    let delivered = tokio::time::timeout(Duration::from_secs(1), rx.recv())
        .await
        .unwrap()
        .unwrap();
    let frame: BcsFrame = serde_json::from_str(&delivered).unwrap();
    let request_id = match frame {
        BcsFrame::Request(req) => {
            assert_eq!(req.method, "chat.history");
            req.id
        }
        _ => panic!("expected request frame"),
    };

    let payload = serde_json::json!({"messages": []});
    registry
        .resolve_pending_request(&request_id, payload.clone())
        .await;

    let response = response_handle.await.unwrap().unwrap();
    assert_eq!(response, payload);
}

#[tokio::test]
async fn bot_registry_abort_waits_for_matching_response_and_validates_exact_run() {
    let registry = std::sync::Arc::new(BotConnectionRegistry::new());
    let (tx, mut rx) = mpsc::channel(1);
    registry.connect("bot-1".to_string(), tx).await;

    let abort_registry = registry.clone();
    let abort_handle = tokio::spawn(async move {
        abort_registry
            .abort(BotAbortDeliveryCommand {
                target: BotDeliveryTarget::WebSocket {
                    bot_id: "bot-1".to_string(),
                },
                command_id: "abort-command".to_string(),
                group_id: "group-1".to_string(),
                session_id: "session-1".to_string(),
                run_id: Some("plugin-run-1".to_string()),
                provider_bypass_headers: Vec::new(),
                timeout_ms: 1_000,
            })
            .await
    });

    let delivered = tokio::time::timeout(Duration::from_secs(1), rx.recv())
        .await
        .unwrap()
        .unwrap();
    let frame: BcsFrame = serde_json::from_str(&delivered).unwrap();
    match frame {
        BcsFrame::Request(request) => {
            assert_eq!(request.id, "abort-command");
            assert_eq!(request.method, "chat.abort");
            assert_eq!(request.params.as_ref().unwrap()["session_key"], "session-1");
            assert_eq!(request.params.as_ref().unwrap()["run_id"], "plugin-run-1");
        }
        _ => panic!("expected chat.abort RequestFrame"),
    }
    tokio::task::yield_now().await;
    assert!(
        !abort_handle.is_finished(),
        "mpsc write is not an abort acknowledgement"
    );

    assert!(
        registry
            .resolve_pending_abort_request(
                "abort-command",
                ResponseFrame::ok(
                    "abort-command",
                    serde_json::json!({
                        "aborted": true,
                        "aborted_run_ids": ["plugin-run-1"]
                    }),
                ),
            )
            .await
    );
    let result = abort_handle.await.unwrap().unwrap();
    assert_eq!(result.aborted_run_ids, ["plugin-run-1"]);
}

#[tokio::test]
async fn bot_registry_abort_maps_legacy_unknown_method_to_capability_error() {
    let registry = std::sync::Arc::new(BotConnectionRegistry::new());
    let (tx, mut rx) = mpsc::channel(1);
    registry.connect("legacy-bot".to_string(), tx).await;

    let abort_registry = registry.clone();
    let abort_handle = tokio::spawn(async move {
        abort_registry
            .abort(BotAbortDeliveryCommand {
                target: BotDeliveryTarget::WebSocket {
                    bot_id: "legacy-bot".to_string(),
                },
                command_id: "abort-legacy".to_string(),
                group_id: "group-1".to_string(),
                session_id: "session-1".to_string(),
                run_id: Some("plugin-run-1".to_string()),
                provider_bypass_headers: Vec::new(),
                timeout_ms: 1_000,
            })
            .await
    });

    let _ = tokio::time::timeout(Duration::from_secs(1), rx.recv())
        .await
        .expect("chat.abort request")
        .expect("bot channel");
    assert!(
        registry
            .resolve_pending_abort_request(
                "abort-legacy",
                ResponseFrame::error(
                    "abort-legacy",
                    "NOT_FOUND",
                    "Unknown method: chat.abort",
                ),
            )
            .await
    );

    let error = abort_handle.await.unwrap().unwrap_err();
    assert!(matches!(
        error,
        ServiceError::BotMethodUnsupported { bot_id, method }
            if bot_id == "legacy-bot" && method == "chat.abort"
    ));
}

#[tokio::test]
async fn kick_sends_event_and_closes_connection() {
    use bcs_service_api::{BotConnectionControlPort, KickReason};
    use serde_json::Value;
    use tokio::sync::mpsc;

    let registry = BotConnectionRegistry::new();
    let (tx, mut rx) = mpsc::channel::<String>(8);
    registry.connect("bot-x".to_string(), tx).await;
    assert!(registry.is_connected("bot-x").await);

    let kicked = registry
        .kick("bot-x", KickReason::DeliverySwitchedToProvider)
        .await;
    assert!(kicked, "kick should report that a connection was torn down");

    let frame = rx.recv().await.expect("kick frame must be sent");
    let parsed: Value = serde_json::from_str(&frame).expect("frame is JSON");
    assert_eq!(parsed["type"], "event");
    assert_eq!(parsed["event"], "bot.kicked");
    assert_eq!(parsed["payload"]["reason"], "delivery_switched_to_provider");

    assert!(rx.recv().await.is_none(), "channel should be closed");
    assert!(!registry.is_connected("bot-x").await);
}

#[tokio::test]
async fn kick_returns_false_when_bot_not_connected() {
    use bcs_service_api::{BotConnectionControlPort, KickReason};
    let registry = BotConnectionRegistry::new();
    let kicked = registry
        .kick("nobody", KickReason::DeliverySwitchedToProvider)
        .await;
    assert!(!kicked);
}
