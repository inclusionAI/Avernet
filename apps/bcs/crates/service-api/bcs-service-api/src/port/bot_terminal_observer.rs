use std::sync::Arc;

use async_trait::async_trait;

/// Transport-neutral terminal states accepted from an authenticated bot event.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BotTerminalState {
    Final,
    Error,
    Aborted,
}

/// A successfully handled terminal chat event.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotTerminalEvent {
    pub run_id: String,
    pub bot_uuid: String,
    pub state: BotTerminalState,
    pub text: String,
}

/// Observes accepted bot terminal events without owning their domain handling.
///
/// Implementations must be best-effort: observation must not turn an accepted
/// bot event into a transport failure.
#[async_trait]
pub trait BotTerminalObserverPort: Send + Sync {
    async fn observe(&self, event: BotTerminalEvent);
}

#[derive(Debug, Default)]
pub struct NoopBotTerminalObserver;

#[async_trait]
impl BotTerminalObserverPort for NoopBotTerminalObserver {
    async fn observe(&self, _event: BotTerminalEvent) {}
}

/// Best-effort fan-out for independently owned terminal side effects.
pub struct CompositeBotTerminalObserver {
    observers: Vec<Arc<dyn BotTerminalObserverPort>>,
}

impl CompositeBotTerminalObserver {
    pub fn new(observers: Vec<Arc<dyn BotTerminalObserverPort>>) -> Self {
        Self { observers }
    }
}

#[async_trait]
impl BotTerminalObserverPort for CompositeBotTerminalObserver {
    async fn observe(&self, event: BotTerminalEvent) {
        for observer in &self.observers {
            observer.observe(event.clone()).await;
        }
    }
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicUsize, Ordering};

    use super::*;

    struct CountingObserver(AtomicUsize);

    #[async_trait]
    impl BotTerminalObserverPort for CountingObserver {
        async fn observe(&self, _event: BotTerminalEvent) {
            self.0.fetch_add(1, Ordering::Relaxed);
        }
    }

    #[tokio::test]
    async fn composite_notifies_every_observer() {
        let first = Arc::new(CountingObserver(AtomicUsize::new(0)));
        let second = Arc::new(CountingObserver(AtomicUsize::new(0)));
        let composite = CompositeBotTerminalObserver::new(vec![first.clone(), second.clone()]);

        composite
            .observe(BotTerminalEvent {
                run_id: "run-1".to_string(),
                bot_uuid: "bot-1".to_string(),
                state: BotTerminalState::Aborted,
                text: String::new(),
            })
            .await;

        assert_eq!(first.0.load(Ordering::Relaxed), 1);
        assert_eq!(second.0.load(Ordering::Relaxed), 1);
    }
}
