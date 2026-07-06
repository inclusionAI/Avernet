use async_trait::async_trait;
use bcs_domain::{BotDeliveryTarget, Participant};

/// Outbound port used by group message-history use cases to request bot-local
/// chat history without depending on any HTTP adapter state.
#[async_trait]
pub trait GroupHistoryBotRequestPort: Send + Sync {
    async fn send_history_request(
        &self,
        target: BotDeliveryTarget,
        method: &str,
        params: serde_json::Value,
        timeout_ms: u64,
    ) -> Result<serde_json::Value, String>;
}

/// Outbound port used by delivery adapters to read a group's participant list
/// for routing / coordination decisions without depending on a core service
/// trait directly. Returns `None` when the group does not exist.
#[async_trait]
pub trait GroupDispatchContextPort: Send + Sync {
    async fn participants(&self, group_id: &str) -> Option<Vec<Participant>>;
}
