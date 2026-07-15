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
