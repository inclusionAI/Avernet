//! Retryable transport close, deliberately not a permanent `bot.kicked` event.
use axum::extract::ws::{CloseFrame, Message, WebSocket};
use futures::{SinkExt, stream::SplitSink};
use std::time::Duration;

pub async fn close_for_leadership_change(sink: &mut SplitSink<WebSocket, Message>) {
    // A stalled peer must not keep the old owner's socket alive indefinitely.
    let _ = tokio::time::timeout(Duration::from_secs(1), sink.send(Message::Close(Some(
        CloseFrame { code: 1012, reason: "leadership_lost".into() },
    )))).await;
}
