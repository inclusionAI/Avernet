use async_trait::async_trait;

use crate::{ServiceError, ServiceResult};

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct BotRunContext {
    pub run_id: String,
    pub bot_id: String,
    pub group_id: String,
    pub bcs_session_id: Option<String>,
    pub deadline_ms: u64,
    pub terminal: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
pub struct BotRunScope {
    pub group_id: String,
    pub session_id: String,
    pub bot_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum BotRunTransportOwner {
    WebSocket,
    HttpProvider {
        provider_id: String,
        provider_bot_ref: String,
    },
}

/// Abort-routing snapshot captured before `chat.send` delivery begins.
/// Secrets and callback URLs deliberately remain owned by delivery adapters.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ActiveBotRunContext {
    pub canonical_run_id: String,
    pub downstream_run_id: String,
    /// Exact session key carried by the original downstream `chat.send`.
    /// Older persisted contexts may not contain it and fall back to Scope.
    #[serde(default)]
    pub downstream_session_key: Option<String>,
    pub scope: BotRunScope,
    pub transport_owner: BotRunTransportOwner,
    pub deadline_ms: u64,
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

    async fn register_active_run(&self, _context: ActiveBotRunContext) -> ServiceResult<()> {
        Err(ServiceError::InvalidOperation {
            message: "active Bot run index is not configured".to_string(),
            request_id: None,
        })
    }

    async fn list_active_runs(
        &self,
        _scope: &BotRunScope,
    ) -> ServiceResult<Vec<ActiveBotRunContext>> {
        Ok(Vec::new())
    }

    async fn find_active_run(&self, _run_id: &str) -> ServiceResult<Option<ActiveBotRunContext>> {
        Ok(None)
    }

    async fn bind_downstream_run_id(
        &self,
        _canonical_run_id: &str,
        _downstream_run_id: &str,
    ) -> ServiceResult<bool> {
        Ok(false)
    }

    async fn remove_active_run(
        &self,
        _scope: &BotRunScope,
        _canonical_run_id: &str,
    ) -> ServiceResult<bool> {
        Ok(false)
    }

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
