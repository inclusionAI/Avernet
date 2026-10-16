use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::extract::ws::{Message, WebSocket};
use bcs_service_api::application::v1::DeliveryDecision;
use bcs_service_api::{
    WsCloseReason, WsErrorKind, WsLifecycleInstrumentationHook, WsPeer,
};
use futures::{SinkExt, StreamExt};
use serde::Deserialize;
use serde_json::Value;
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;
use tracing::{debug, error, info, warn};

use crate::shared::OutboundFrame;
use crate::web::{
    dispatch_client_frame, WebClientConnectionState, WebDispatchOutcome, WebDispatchState,
    WebWsDispatchError, WorkbenchConnectionAuth,
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
    auth: WorkbenchConnectionAuth,
    metrics_hook: Arc<dyn WsLifecycleInstrumentationHook>,
) {
    let leadership_shutdown = state.frontend_connections.connection_epoch.subscribe();
    let (mut ws_tx, mut ws_rx) = socket.split();
    let (client_tx, mut client_rx) = mpsc::channel::<String>(256);
    let mut connection_state = WebClientConnectionState::default();
    let mut protected_rx = connection_state.open_protected_queue();
    let connection_shutdown = connection_state.shutdown.clone();

    let connected_at = Instant::now();
    let client_id = CLIENT_COUNTER.fetch_add(1, Ordering::Relaxed);
    let send_error_seen = Arc::new(AtomicBool::new(false));
    let mut close_reason = WsCloseReason::Unknown;
    let mut flush_server_close = false;
    metrics_hook
        .accepted(WsPeer::Frontend, crate::web::FRONTEND_WS_ENDPOINT)
        .await;

    let write_send_error = send_error_seen.clone();
    let write_shutdown = leadership_shutdown.clone();
    let write_state = state.clone();
    let write_app_shutdown = connection_shutdown.clone();
    let write_client_tx = client_tx.clone();
    let mut write_handle = tokio::spawn(async move {
        // Set once the final check invalidated the binding: the close
        // notification is still drained and sent, but no further
        // protected frame is authorized (解绑后查询0 — the queue is
        // dropped without any further consult).
        let mut revoked = false;
        loop {
            let msg = tokio::select! {
                biased;
                _ = write_shutdown.cancelled() => break,
                msg = client_rx.recv() => match msg {
                    Some(msg) => msg,
                    None => break,
                },
                frame = protected_rx.recv() => {
                    // The FINAL writer: protected frames are
                    // re-authorized against current authorization facts
                    // once more right before the socket send. There is
                    // no connection-time scope and no enqueue-time
                    // decision; every frame's decision is independent.
                    let frame = match frame {
                        Some(frame) if !revoked => frame,
                        _ => break,
                    };
                    match finalize_protected_frame(
                        &write_state,
                        client_id,
                        &write_client_tx,
                        frame,
                        &write_app_shutdown,
                    )
                    .await
                    {
                        ProtectedOutcome::Send(payload) => payload,
                        ProtectedOutcome::Skip => {
                            debug!(client_id = client_id, "protected frame skipped for current view");
                            continue;
                        }
                        ProtectedOutcome::Revoked => {
                            // The close notification was enqueued
                            // best-effort; drain the queue and end.
                            revoked = true;
                            continue;
                        }
                    }
                }
            };
            let close_after_send = is_view_scope_changed_event(&msg);
            let sent = tokio::select! {
                biased;
                _ = write_shutdown.cancelled() => break,
                sent = ws_tx.send(Message::Text(msg.into())) => sent,
            };
            if sent.is_err() {
                debug!("WebSocket send error, connection likely closed");
                write_send_error.store(true, Ordering::Relaxed);
                break;
            }
            if close_after_send {
                break;
            }
        }
        if write_shutdown.is_cancelled() {
            // Drop the receiver first so concurrent senders immediately observe a
            // closed channel instead of enqueuing into a socket that is already
            // closing for leadership loss.
            drop(client_rx);
            crate::shared::leadership_close::close_for_leadership_change(&mut ws_tx).await;
        } else {
            let _ = ws_tx.send(Message::Close(None)).await;
        }
    });

    info!(
        client_id = client_id,
        bound_actor_id = ?auth.actor_id(),
        "New frontend WebSocket connection established"
    );

    loop {
        let next_message = tokio::select! {
            biased;
            _ = leadership_shutdown.cancelled() => {
                close_reason = WsCloseReason::ServerClose;
                info!(reason = "leadership_lost", "Closing WebSocket on non-leader");
                break;
            }
            _ = connection_shutdown.cancelled() => {
                close_reason = WsCloseReason::ServerClose;
                flush_server_close = true;
                break;
            }
            next_message = tokio::time::timeout(CLIENT_IDLE_TIMEOUT, ws_rx.next()) => next_message,
        };
        match next_message {
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
                            &auth,
                        ).await {
                            Ok(outcome) => {
                                debug!(client_id = client_id, "Frame dispatched successfully");
                                match outcome {
                                    WebDispatchOutcome::ClientConnect { subscribed } => {
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
                                    WebDispatchOutcome::Close => {
                                        close_reason = WsCloseReason::ProtocolError;
                                        flush_server_close = true;
                                        break;
                                    }
                                    WebDispatchOutcome::Dispatched => {}
                                }
                            }
                            Err(e) => {
                                warn!(request_id = %bcs_observability::CurrentRequestId, client_id = client_id, error = %e, "Frame dispatch error");
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
                        warn!(request_id = %bcs_observability::CurrentRequestId, client_id = client_id, len = data.len(), "Received unexpected binary frame");
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
                        error!(request_id = %bcs_observability::CurrentRequestId, client_id = client_id, error = %e, "WebSocket error");
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
    for (session_id, conn_id, _) in &connection_state.subscribed_sessions {
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

    if flush_server_close || leadership_shutdown.is_cancelled() {
        drop(client_tx);
        let _ = tokio::time::timeout(Duration::from_secs(2), &mut write_handle).await;
    }
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

/// Outcome of the per-frame final authorization used by the socket
/// writer.
enum ProtectedOutcome {
    /// Authorized: send this payload.
    Send(String),
    /// Invisible to the current view only — drop this frame, other
    /// frames and subscriptions are untouched.
    Skip,
    /// The binding lost its authority or resource qualification: the
    /// connection is shutting down (cancel + unbind always); the
    /// close notification is delivered best-effort through the
    /// writer's own control queue.
    Revoked,
}

/// The FINAL per-frame check (bot-authority spec §8.2):
/// `Deliver` → payload, `SkipMessage` → drop this frame only,
/// `InvalidateBinding` → revoke the whole binding (drop queued
/// frames, unregister this connection's runs, remove its
/// subscriptions, notify with the existing close protocol). Every
/// call is an independent check; no registry lock is held across
/// the authorization await.
///
/// The application error is already application-level and
/// desensitized: it is treated exactly like an invalidated binding,
/// never guessed into a delivery.
async fn finalize_protected_frame(
    state: &Arc<WebDispatchState>,
    client_id: u64,
    client_tx: &mpsc::Sender<String>,
    frame: OutboundFrame,
    connection_shutdown: &CancellationToken,
) -> ProtectedOutcome {
    match frame {
        OutboundFrame::ConnectionControl { payload } => {
            // The public socket carries connection-control frames
            // WITHOUT a passing delivery authorization; protected
            // data never flows through them.
            ProtectedOutcome::Send(payload)
        }
        OutboundFrame::Protected {
            binding,
            visibility,
            payload,
        } => {
            match state
                .delivery_authorization
                .authorize_delivery(&binding, &visibility)
                .await
            {
                Ok(DeliveryDecision::Deliver) => ProtectedOutcome::Send(payload),
                Ok(DeliveryDecision::SkipMessage) => ProtectedOutcome::Skip,
                Ok(DeliveryDecision::InvalidateBinding) | Err(_) => {
                    warn!(
                        request_id = %bcs_observability::CurrentRequestId,
                        client_id = client_id,
                        view_actor_id = %binding.view_actor_id,
                        session_id = ?binding.session_id,
                        "protected delivery binding invalidated; revoking connection"
                    );
                    // Cancel FIRST: the revocation (unregister the
                    // runs, remove the subscriptions, close the
                    // socket) must never depend on enqueueing into
                    // the writer's OWN control queue — this call
                    // runs ON that writer, and the sole consumer of
                    // `client_tx` is the writer itself, so an
                    // awaited send into a full queue would deadlock
                    // the writer against its own queue.
                    connection_shutdown.cancel();
                    // Notify with the existing close protocol on a
                    // best-effort basis: only when the control
                    // queue has space. When it is full the writer
                    // still drains its queued traffic, and the
                    // socket closes through the normal revoked
                    // teardown — a dropped notification leaks no
                    // unauthorized frame and loses no unbinding.
                    let close = serde_json::json!({
                        "type": "event",
                        "event": "close",
                        "payload": {
                            "reason": "view_scope_changed",
                            "message": "Session access is no longer authorized; reconnect required"
                        }
                    })
                    .to_string();
                    match client_tx.try_send(close) {
                        Ok(()) => {}
                        Err(mpsc::error::TrySendError::Full(_)) => {
                            warn!(
                                request_id = %bcs_observability::CurrentRequestId,
                                client_id = client_id,
                                "revocation close notification dropped: writer control queue full"
                            );
                        }
                        Err(mpsc::error::TrySendError::Closed(_)) => {
                            debug!(
                                request_id = %bcs_observability::CurrentRequestId,
                                client_id = client_id,
                                "revocation close notification skipped: writer already gone"
                            );
                        }
                    }
                    ProtectedOutcome::Revoked
                }
            }
        }
    }
}

fn is_view_scope_changed_event(message: &str) -> bool {
    serde_json::from_str::<Value>(message).is_ok_and(|frame| {
        frame.get("type").and_then(Value::as_str) == Some("event")
            && frame.get("event").and_then(Value::as_str) == Some("close")
            && frame.pointer("/payload/reason").and_then(Value::as_str)
                == Some("view_scope_changed")
    })
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
