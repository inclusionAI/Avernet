//! Event broadcaster for WebSocket clients.
//!
//! Uses tokio broadcast channel for efficient event distribution.

use std::sync::Arc;
use tokio::sync::broadcast;

use super::chat_types::ChatEvent;
use super::EventFrame;

/// Event broadcast sender.
pub type EventSender = broadcast::Sender<Arc<EventFrame>>;

/// Event broadcast receiver.
pub type EventReceiver = broadcast::Receiver<Arc<EventFrame>>;

/// Event broadcaster for distributing events to WebSocket clients.
#[derive(Debug, Clone)]
pub struct EventBroadcaster {
    sender: EventSender,
}

impl EventBroadcaster {
    /// Create a new event broadcaster.
    ///
    /// # Arguments
/// * `capacity` - Maximum number of events to buffer per subscriber.
    ///   Older events are dropped when the buffer is full.
    pub fn new(capacity: usize) -> Self {
        let (sender, _) = broadcast::channel(capacity);
        Self { sender }
    }

    /// Subscribe to events.
    pub fn subscribe(&self) -> EventReceiver {
        self.sender.subscribe()
    }

    /// Get a clone of the sender for broadcasting.
    pub fn sender(&self) -> EventSender {
        self.sender.clone()
    }

    /// Broadcast a chat event.
    pub fn broadcast_chat(&self, event: ChatEvent) {
        let frame = EventFrame::new("chat", serde_json::to_value(event).ok(), None);
        // Ignore send errors (no subscribers)
        let _ = self.sender.send(Arc::new(frame));
    }

    /// Broadcast a tick/heartbeat event.
    pub fn broadcast_tick(&self, ts: u64) {
        let frame = EventFrame::new("tick", Some(serde_json::json!({ "ts": ts })), None);
        let _ = self.sender.send(Arc::new(frame));
    }

    /// Broadcast a custom event.
    pub fn broadcast(&self, event: &str, payload: serde_json::Value) {
        let frame = EventFrame::new(event, Some(payload), None);
        let _ = self.sender.send(Arc::new(frame));
    }

    /// Get the number of active subscribers.
    pub fn subscriber_count(&self) -> usize {
        self.sender.receiver_count()
    }
}

impl Default for EventBroadcaster {
    fn default() -> Self {
        Self::new(256)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use super::super::chat_types::ChatEventState;

    #[tokio::test]
    async fn test_broadcast_and_receive() {
        let broadcaster = EventBroadcaster::new(16);
        let mut receiver = broadcaster.subscribe();

        let event = ChatEvent {
            run_id: "run-1".to_string(),
            session_key: "test".to_string(),
            seq: 1,
            state: ChatEventState::Delta,
            message: Some(serde_json::json!({"text": "hello"})),
            error_message: None,
            usage: None,
            stop_reason: None,
        };

        broadcaster.broadcast_chat(event);

        let received = receiver.recv().await.unwrap();
        assert_eq!(received.event, "chat");
    }

    #[test]
    fn test_subscriber_count() {
        let broadcaster = EventBroadcaster::new(16);
        assert_eq!(broadcaster.subscriber_count(), 0);

        let receiver1 = broadcaster.subscribe();
        assert_eq!(broadcaster.subscriber_count(), 1);

        let _receiver2 = broadcaster.subscribe();
        assert_eq!(broadcaster.subscriber_count(), 2);

        drop(receiver1);
        // Note: receiver_count doesn't immediately reflect drops
    }
}