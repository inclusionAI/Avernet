use super::*;

pub(super) struct ControllableHistoryBotRequest {
    delays_ms: HashMap<String, u64>,
    responses: HashMap<String, Result<serde_json::Value, String>>,
}

impl ControllableHistoryBotRequest {
    pub(super) fn with_delays(
        delays: HashMap<String, u64>,
        responses: HashMap<String, Result<serde_json::Value, String>>,
    ) -> Self {
        Self {
            delays_ms: delays,
            responses,
        }
    }
}

#[async_trait::async_trait]
impl GroupHistoryBotRequestPort for ControllableHistoryBotRequest {
    async fn send_history_request(
        &self,
        target: BotDeliveryTarget,
        _method: &str,
        _params: serde_json::Value,
        _timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        let bot_uuid = target.bot_id();
        if let Some(&delay) = self.delays_ms.get(bot_uuid) {
            tokio::time::sleep(std::time::Duration::from_millis(delay)).await;
        }
        self.responses
            .get(bot_uuid)
            .cloned()
            .unwrap_or_else(|| Err("not configured".to_string()))
    }
}
