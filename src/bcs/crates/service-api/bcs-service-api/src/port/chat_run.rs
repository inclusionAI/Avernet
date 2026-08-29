use async_trait::async_trait;

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct BotRunContext {
    pub run_id: String,
    pub bot_id: String,
    pub group_id: String,
    pub bcs_session_id: Option<String>,
    pub deadline_ms: u64,
    pub terminal: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProviderRunTransport {
    Negotiating,
    Sse,
    Callback,
    Terminal,
}

#[async_trait]
pub trait BotRunContextPort: Send + Sync {
    async fn put_context(&self, context: BotRunContext);
    async fn get_context(&self, run_id: &str) -> Option<BotRunContext>;
    async fn try_begin_terminal(&self, run_id: &str) -> bool;
    async fn mark_terminal(&self, run_id: &str) -> bool;
    async fn release_terminal(&self, run_id: &str);

    /// Register a Provider 2.0 run in `Negotiating` state before its network
    /// request starts. Returns `false` when the run id is already registered.
    /// Implementations must not report success without retaining this state.
    async fn begin_provider_transport(&self, run_id: &str, deadline_ms: u64) -> bool;

    /// Bind a negotiating run to exactly one event source. `Sse` and
    /// `Callback` are the only valid binding targets. Rebinding to the same
    /// source may succeed idempotently; a missing run, an invalid target, or a
    /// conflicting existing binding must return `false`.
    async fn bind_provider_transport(&self, run_id: &str, transport: ProviderRunTransport) -> bool;

    /// Return the current transport state, or `None` only when the run has no
    /// transport registration. `None` does not imply callback transport.
    async fn get_provider_transport(&self, run_id: &str) -> Option<ProviderRunTransport>;

    /// Mark a registered run terminal so late events can be rejected.
    async fn mark_provider_transport_terminal(&self, run_id: &str);

    /// Remove transport state after a failed request or completed retention.
    async fn clear_provider_transport(&self, run_id: &str);
    async fn cleanup_expired(&self, _now_ms: u64, _retention_ms: u64) -> usize {
        0
    }
}

#[async_trait]
pub trait ChatRunCleanupPort: Send + Sync {
    async fn unregister(&self, run_id: &str);
}

#[async_trait]
pub trait ChatRunEventPort: Send + Sync {
    async fn register(
        &self,
        run_id: String,
        session_key: String,
        sender: tokio::sync::mpsc::Sender<String>,
        source: Option<String>,
        from: Option<String>,
    );

    async fn unregister(&self, run_id: &str);
}
