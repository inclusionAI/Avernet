//! HTTP authentication, protocol validation and gateway event delivery.
use std::{pin::Pin, sync::Arc, time::Duration};
use axum::{body::{Body, Bytes}, extract::State, http::{HeaderMap, HeaderValue, StatusCode}, response::{IntoResponse, Response}, routing::post, Json, Router};
use bcs_protocol::BCN_PROTOCOL_VERSION_HEADER;
use futures::{Stream, StreamExt};
use serde_json::Value;
use crate::{encoder::{Encoder, GatewayEncoder}, error::BridgeError, runtime::{self, RuntimeReply}};

pub use crate::runtime::{AppState, DownstreamRequest, ToBot};

impl IntoResponse for BridgeError {
    fn into_response(self) -> Response {
        let (status, body) = self.into_parts();
        (StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR), Json(body)).into_response()
    }
}

pub fn router(state: Arc<AppState>) -> Router {
    Router::new().route("/webhook", post(handle_webhook)).with_state(state)
}

async fn handle_webhook(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(value): Json<Value>,
) -> Response {
    match dispatch(state, headers, value).await {
        Ok(response) => response,
        Err(error) => error.into_response(),
    }
}

async fn dispatch(state: Arc<AppState>, headers: HeaderMap, value: Value) -> Result<Response, BridgeError> {
    let auth = headers.get(axum::http::header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok()).unwrap_or_default();
    let token = state.config.bcs_to_provider_token.as_deref().ok_or_else(BridgeError::unauthorized)?;
    if auth != format!("Bearer {token}") { return Err(BridgeError::unauthorized()); }
    let req = GatewayEncoder.decode(value)?;
    if req.to_bot.provider_id != state.config.provider_id { return Err(BridgeError::provider_id_mismatch()); }
    if req.method == "chat.send" && headers.get(BCN_PROTOCOL_VERSION_HEADER).and_then(|value| value.to_str().ok()) != Some("2.0") {
        return Err(BridgeError::invalid_request("X-BCN-Protocol-Version 2.0 required"));
    }
    let trace = state.trace.as_ref().and_then(|store| state.config.bot(&req.to_bot.provider_bot_ref).map(|bot| {
        crate::engine::trace::TraceContext::new(store.clone(), bot.engine.clone(), req.id.clone())
    }));
    match runtime::dispatch(state, req).await? {
        RuntimeReply::Json { status, body } => Ok((StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR), Json(body)).into_response()),
        RuntimeReply::Run { stream, handle } => {
            let frames = gateway_stream(stream, handle, trace);
            let mut response = Response::new(Body::from_stream(frames));
            response.headers_mut().insert(axum::http::header::CONTENT_TYPE, HeaderValue::from_static("text/event-stream; charset=utf-8"));
            response.headers_mut().insert(axum::http::header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
            Ok(response)
        }
    }
}

/// Gateway delivery owns keepalive timing and terminal wire-encoding failures.
fn gateway_stream(
    stream: Pin<Box<dyn Stream<Item = crate::run::RunEvent> + Send>>,
    handle: crate::run::RunHandle,
    trace: Option<crate::engine::trace::TraceContext>,
) -> impl Stream<Item = Result<Bytes, std::io::Error>> + Send {
    let mut heartbeat = tokio::time::interval(Duration::from_secs(20));
    heartbeat.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    futures::stream::unfold((stream, handle, trace, heartbeat, false),
        |(mut stream, handle, trace, mut heartbeat, done)| async move {
            if done { return None; }
            loop {
                let event = tokio::select! {
                    biased;
                    event = stream.next() => match event { Some(event) => event, None => return None },
                    _ = heartbeat.tick() => return Some((Ok(Bytes::from_static(crate::sse::HEARTBEAT.as_bytes())),
                        (stream, handle, trace, heartbeat, false))),
                };
                let (frame, done) = match GatewayEncoder.encode(&event) {
                    Ok(Some(frame)) => (Ok(frame), false),
                    Ok(None) => continue,
                    Err(error) => {
                        handle.abort.cancel();
                        let message = match error {
                            crate::sse::FrameError::FrameTooLarge(_) => "frame too large",
                            _ => "gateway event encoding failed",
                        };
                        let terminal = crate::run::RunEvent { run_id: event.run_id.clone(), seq: event.seq, ts: event.ts,
                            event: crate::sse::chat_error(&event.run_id, message, Some("runtime_error")) };
                        let frame = GatewayEncoder.encode(&terminal)
                            .and_then(|frame| frame.ok_or(crate::sse::FrameError::Unsupported))
                            .map_err(std::io::Error::other);
                        (frame, true)
                    }
                };
                if let (Some(trace), Ok(frame)) = (&trace, &frame) { trace.record_sse(event.seq, frame); }
                return Some((frame.map(Bytes::from), (stream, handle, trace, heartbeat, done)));
            }
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn idle_gateway_emits_transport_heartbeat_without_retaining_event() {
        let registry = crate::run::RunRegistry::new();
        let (handle, _) = registry.begin("heartbeat", "fp".into());
        let stream = gateway_stream(Box::pin(crate::run::forward_stream(handle.clone())), handle.clone(), None);
        let mut stream = Box::pin(stream);
        let frame = tokio::time::timeout(Duration::from_millis(200), stream.next()).await.unwrap().unwrap().unwrap();
        assert_eq!(frame.as_ref(), crate::sse::HEARTBEAT.as_bytes());
        assert!(handle.snapshot_after(0).is_empty());
        drop(stream);
        tokio::time::timeout(Duration::from_millis(200), handle.abort.cancelled()).await.unwrap();
    }

    #[tokio::test]
    async fn encoder_failure_emits_error_cancels_and_closes_gateway_stream() {
        let registry = crate::run::RunRegistry::new();
        let (handle, _) = registry.begin("unsupported", "fp".into());
        let records = vec![crate::run::RunEvent { run_id: "unsupported".into(), seq: 1, ts: 123,
            event: bcs_protocol::stream::StreamEvent::Unknown { event: "opaque".into(), raw: Value::Null } },
            crate::run::RunEvent { run_id: "unsupported".into(), seq: 2, ts: 124,
                event: crate::sse::chat_final("unsupported", "must not appear".into()) }];
        let stream = gateway_stream(Box::pin(futures::stream::iter(records)), handle.clone(), None);
        futures::pin_mut!(stream);
        let frame = stream.next().await.unwrap().unwrap();
        let text = std::str::from_utf8(&frame).unwrap();
        assert!(text.contains("\"state\":\"error\""), "{text}");
        assert!(text.contains("runtime_error"));
        assert!(handle.abort.is_cancelled());
        assert!(stream.next().await.is_none());
    }
}
