use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::extract::ws::{Message, WebSocket};
use bcs_service_api::{
    WsCloseReason, WsErrorKind, WsLifecycleInstrumentationHook, WsPeer,
};
use futures::{SinkExt, StreamExt};
use serde::Deserialize;
use tokio::sync::mpsc;
use tracing::{debug, error, info, warn};

use crate::web::{
    dispatch_client_frame, WebClientConnectionState, WebDispatchOutcome, WebDispatchState,
    WebWsDispatchError,
};

static CLIENT_COUNTER: AtomicU64 = AtomicU64::new(0);

const CLIENT_IDLE_TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Debug, Deserialize)]
struct PingFrame {
    #[allow(dead_code)]
    r#type: String,
}

pub async fn handle_client_connection(
    socket: WebSocket,
    state: Arc<WebDispatchState>,
    bound_actor_id: Option<String>,
    metrics_hook: Arc<dyn WsLifecycleInstrumentationHook>,
) {
    let (mut ws_tx, mut ws_rx) = socket.split();
    let (client_tx, mut client_rx) = mpsc::channel::<String>(256);

    let connected_at = Instant::now();
    let client_id = CLIENT_COUNTER.fetch_add(1, Ordering::Relaxed);
    let send_error_seen = Arc::new(AtomicBool::new(false));
    let mut close_reason = WsCloseReason::Unknown;
    metrics_hook
        .accepted(WsPeer::Frontend, crate::web::FRONTEND_WS_ENDPOINT)
        .await;

    let write_send_error = send_error_seen.clone();
    let write_handle = tokio::spawn(async move {
        while let Some(msg) = client_rx.recv().await {
            if ws_tx.send(Message::Text(msg.into())).await.is_err() {
                debug!("WebSocket send error, connection likely closed");
                write_send_error.store(true, Ordering::Relaxed);
                break;
            }
        }
        let _ = ws_tx.send(Message::Close(None)).await;
    });

    let mut connection_state = WebClientConnectionState::default();

    info!(
        client_id = client_id,
        bound_actor_id = ?bound_actor_id,
        "New frontend WebSocket connection established"
    );

    loop {
        match tokio::time::timeout(CLIENT_IDLE_TIMEOUT, ws_rx.next()).await {
            Ok(Some(msg_result)) => {
                match msg_result {
                    Ok(Message::Text(text)) => {
                        debug!(client_id = client_id, len = text.len(), "Received text frame");

                        if is_ping_frame(&text) {
                            send_pong(&client_tx).await;
                            debug!(client_id = client_id, "Ping received, pong sent");
                            continue;
                        }

                        match dispatch_client_frame(
                            &state,
                            &text,
                            &client_tx,
                            &mut connection_state,
                            bound_actor_id.as_deref(),
                        ).await {
                            Ok(outcome) => {
                                debug!(client_id = client_id, "Frame dispatched successfully");
                                if let WebDispatchOutcome::ClientConnect { subscribed } = outcome {
                                    if subscribed {
                                        metrics_hook
                                            .registered(
                                                WsPeer::Frontend,
                                                crate::web::FRONTEND_WS_ENDPOINT,
                                            )
                                            .await;
                                    } else {
                                        metrics_hook
                                            .error(
                                                WsPeer::Frontend,
                                                crate::web::FRONTEND_WS_ENDPOINT,
                                                WsErrorKind::RegisterRejected,
                                            )
                                            .await;
                                    }
                                }
                            }
                            Err(e) => {
                                warn!(client_id = client_id, error = %e, "Frame dispatch error");
                                let error_kind = if matches!(&e, WebWsDispatchError::ClientConnectError(_)) {
                                    WsErrorKind::RegisterRejected
                                } else {
                                    WsErrorKind::DispatchError
                                };
                                metrics_hook
                                    .error(
                                        WsPeer::Frontend,
                                        crate::web::FRONTEND_WS_ENDPOINT,
                                        error_kind,
                                    )
                                    .await;
                                let _ = client_tx.send(
                                    serde_json::json!({
                                        "type": "res",
                                        "id": "error",
                                        "ok": false,
                                        "error": {
                                            "code": "dispatch_error",
                                            "message": e.to_string()
                                        }
                                    }).to_string()
                                ).await;
                            }
                        }
                    }
                    Ok(Message::Binary(data)) => {
                        warn!(client_id = client_id, len = data.len(), "Received unexpected binary frame");
                    }
                    Ok(Message::Ping(_data)) => {
                        debug!(client_id = client_id, "Received WebSocket ping");
                    }
                    Ok(Message::Pong(_)) => {
                        debug!(client_id = client_id, "Received WebSocket pong");
                    }
                    Ok(Message::Close(close_frame)) => {
                        close_reason = WsCloseReason::ClientClose;
                        info!(
                            client_id = client_id,
                            close_code = ?close_frame.as_ref().map(|f| f.code),
                            close_reason = ?close_frame.as_ref().map(|f| f.reason.to_string()),
                            "WebSocket close frame received"
                        );
                        break;
                    }
                    Err(e) => {
                        close_reason = WsCloseReason::ProtocolError;
                        error!(client_id = client_id, error = %e, "WebSocket error");
                        metrics_hook
                            .error(
                                WsPeer::Frontend,
                                crate::web::FRONTEND_WS_ENDPOINT,
                                WsErrorKind::ProtocolError,
                            )
                            .await;
                        break;
                    }
                }
            }
            Ok(None) => {
                info!(client_id = client_id, "WebSocket stream ended");
                break;
            }
            Err(_timeout) => {
                close_reason = WsCloseReason::IdleTimeout;
                info!(
                    client_id = client_id,
                    timeout_secs = CLIENT_IDLE_TIMEOUT.as_secs(),
                    "Client idle timeout, closing connection"
                );
                let _ = client_tx.send(
                    serde_json::json!({
                        "type": "event",
                        "event": "close",
                        "payload": {
                            "reason": "idle_timeout",
                            "message": format!("No activity for {} seconds", CLIENT_IDLE_TIMEOUT.as_secs())
                        }
                    }).to_string()
                ).await;
                break;
            }
        }
    }

    if send_error_seen.load(Ordering::Relaxed) {
        close_reason = WsCloseReason::SendError;
        metrics_hook
            .error(
                WsPeer::Frontend,
                crate::web::FRONTEND_WS_ENDPOINT,
                WsErrorKind::SendError,
            )
            .await;
    }

    for run_id in &connection_state.active_run_ids {
        state.run_channels.unregister(run_id).await;
        debug!(client_id = client_id, run_id = %run_id, "Unregistered run channel on disconnect");
    }
    for (session_id, conn_id) in &connection_state.subscribed_sessions {
        state.frontend_connections.unsubscribe(session_id, *conn_id).await;
        debug!(
            client_id = client_id,
            session_id = %session_id,
            conn_id = conn_id,
            "Unsubscribed frontend connection on disconnect"
        );
    }

    info!(
        client_id = client_id,
        duration_ms = connected_at.elapsed().as_millis() as u64,
        active_runs = connection_state.active_run_ids.len(),
        subscribed_sessions = connection_state.subscribed_sessions.len(),
        "Frontend client disconnected"
    );

    write_handle.abort();
    metrics_hook
        .closed(
            WsPeer::Frontend,
            crate::web::FRONTEND_WS_ENDPOINT,
            close_reason,
            connected_at.elapsed(),
        )
        .await;
}

fn is_ping_frame(text: &str) -> bool {
    if text.contains(r#""type":"ping""#) || text.contains(r#""type": "ping""#) {
        serde_json::from_str::<PingFrame>(text).is_ok()
    } else {
        false
    }
}

async fn send_pong(tx: &mpsc::Sender<String>) {
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);

    let pong = serde_json::json!({
        "type": "event",
        "event": "pong",
        "payload": {
            "ts": ts
        }
    });

    let _ = tx.send(pong.to_string()).await;
}
