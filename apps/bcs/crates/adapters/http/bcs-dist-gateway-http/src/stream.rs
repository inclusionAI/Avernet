//! Task subscriptions as Server-Sent Events.
//!
//! Every frame's `data:` is a JSON-RPC response whose result is one A2A
//! stream response; its `id:` is the encoded cursor of that event, which the
//! client sends back as `Last-Event-ID` to resume. A snapshot is sent as the
//! A2A `Task`; a resume acknowledgement sends nothing. An error ends the
//! stream with one JSON-RPC error frame.

use crate::error::rpc_error;
use crate::mapping::{self, TaskShape};
use crate::{GatewayHttpConfig, cursor};
use a2a::{JsonRpcId, JsonRpcResponse, StreamResponse};
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::{IntoResponse, Response};
use bcs_dist_model::TaskCursor;
use bcs_service_api::application::dist_gateway::{GatewayError, TaskStreamItem, TaskSubscription};
use futures::StreamExt;
use std::convert::Infallible;

type Item = Result<TaskStreamItem, GatewayError>;

fn frame(id: &JsonRpcId, at: Option<&TaskCursor>, result: &StreamResponse) -> Option<Event> {
    let value = serde_json::to_value(result).ok()?;
    let body = serde_json::to_string(&JsonRpcResponse::success(id.clone(), value)).ok()?;
    let event = Event::default().data(body);
    Some(match at.and_then(cursor::encode) {
        Some(encoded) => event.id(encoded),
        None => event,
    })
}

fn frames(id: &JsonRpcId, item: Item) -> Vec<Event> {
    match item {
        Ok(TaskStreamItem::Snapshot { view, cursor }) => {
            let task = StreamResponse::Task(mapping::task(&view, TaskShape::FULL));
            frame(id, cursor.as_ref(), &task).into_iter().collect()
        }
        Ok(TaskStreamItem::ResumeAccepted { .. }) => Vec::new(),
        Ok(TaskStreamItem::Events { events, cursor }) => {
            // The application yields one event per item, so the item's cursor is
            // that event's. Should a batch ever hold several, only the last frame
            // carries the cursor: a resume then never skips an event.
            let last = events.len().saturating_sub(1);
            events
                .iter()
                .enumerate()
                .filter_map(|(n, event)| {
                    let result = mapping::event(&cursor.sid, event)?;
                    frame(id, (n == last).then_some(&cursor), &result)
                })
                .collect()
        }
        Err(error) => {
            let body = JsonRpcResponse::error(id.clone(), rpc_error(&error));
            serde_json::to_string(&body)
                .ok()
                .map(|body| Event::default().data(body))
                .into_iter()
                .collect()
        }
    }
}

/// The SSE response of a subscription. `first` holds items already taken from
/// `rest` (to inspect them before answering).
pub(crate) fn response(
    id: JsonRpcId,
    first: Vec<Item>,
    rest: TaskSubscription,
    config: &GatewayHttpConfig,
) -> Response {
    let events = futures::stream::iter(first)
        .chain(rest)
        .flat_map(move |item| futures::stream::iter(frames(&id, item)))
        .map(Ok::<_, Infallible>);
    Sse::new(events)
        .keep_alive(KeepAlive::new().interval(config.keep_alive))
        .into_response()
}
