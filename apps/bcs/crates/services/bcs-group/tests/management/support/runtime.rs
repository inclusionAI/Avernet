use super::*;

pub(super) struct FakeBotRuntimeConnectionService {
    pub(super) provider_downlink_bots: Arc<RwLock<HashSet<String>>>,
}

#[async_trait]
impl BotRuntimeConnectionService for FakeBotRuntimeConnectionService {
    async fn connect_streaming(
        &self,
        _command: BotRuntimeConnectCommand,
    ) -> Result<BotRuntimeConnectOutcome, BotUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_runtime_status(
        &self,
        _command: BotRuntimeStatusCommand,
    ) -> Result<BotRuntimeStatusOutcome, BotUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn disconnect_streaming(
        &self,
        _command: BotRuntimeDisconnectCommand,
    ) -> Result<(), BotUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn is_provider_downlink_bot(&self, bot_id: &str) -> ServiceResult<bool> {
        Ok(self
            .provider_downlink_bots
            .read()
            .unwrap()
            .contains(bot_id))
    }

    async fn resolve_delivery_target(&self, bot_id: &str) -> ServiceResult<BotDeliveryTarget> {
        Ok(BotDeliveryTarget::WebSocket {
            bot_id: bot_id.to_string(),
        })
    }
}
