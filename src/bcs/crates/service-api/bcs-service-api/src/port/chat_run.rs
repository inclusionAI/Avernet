use async_trait::async_trait;

#[derive(Debug, Clone)]
pub struct BotRunContext {
    pub run_id: String,
    pub bot_id: String,
    pub group_id: String,
    pub bcs_session_id: Option<String>,
    pub deadline_ms: u64,
    pub terminal: bool,
}

#[async_trait]
pub trait BotRunContextPort: Send + Sync {
    async fn put_context(&self, context: BotRunContext);
    async fn get_context(&self, run_id: &str) -> Option<BotRunContext>;
    async fn try_begin_terminal(&self, run_id: &str) -> bool;
    async fn mark_terminal(&self, run_id: &str) -> bool;
    async fn release_terminal(&self, run_id: &str);
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
