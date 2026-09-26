//! Recording double for the `BotDeliveryPort` contract, including abort handling,
//! failure injection, and provider-transport capture.
#![allow(dead_code)]

use tokio::sync::RwLock;

use async_trait::async_trait;
use bcs_protocol::BcsFrame;
use bcs_service_api::{
    BotAbortDeliveryCommand, BotAbortDeliveryResult, BotDeliveryCommand, BotDeliveryKind,
    BotDeliveryPort, BotDeliveryResult, BotDeliveryTarget, ProviderTransportPreference,
    FrontendDeliveryCommand, FrontendDeliveryPort, FrontendDeliveryResult, ServiceError,
    ServiceResult,
};

#[derive(Default)]
pub struct RecordingBotDelivery {
    kinds: RwLock<Vec<BotDeliveryKind>>,
    frames: RwLock<Vec<BcsFrame>>,
    targets: RwLock<Vec<BotDeliveryTarget>>,
    provider_transports: RwLock<Vec<ProviderTransportPreference>>,
    fail_for: RwLock<Vec<String>>,
    not_delivered_for: RwLock<Vec<String>>,
    aborts: RwLock<Vec<BotAbortDeliveryCommand>>,
    provider_abort_run_ids: RwLock<Vec<String>>,
}

impl RecordingBotDelivery {
    pub async fn kinds(&self) -> Vec<BotDeliveryKind> {
        self.kinds.read().await.clone()
    }

    pub async fn frames(&self) -> Vec<BcsFrame> {
        self.frames.read().await.clone()
    }

    pub async fn targets(&self) -> Vec<BotDeliveryTarget> {
        self.targets.read().await.clone()
    }

    pub async fn provider_transports(&self) -> Vec<ProviderTransportPreference> {
        self.provider_transports.read().await.clone()
    }

    pub async fn fail_for(&self, bot_id: &str) {
        self.fail_for.write().await.push(bot_id.to_string());
    }

    pub async fn not_delivered_for(&self, bot_id: &str) {
        self.not_delivered_for
            .write()
            .await
            .push(bot_id.to_string());
    }

    pub async fn aborts(&self) -> Vec<BotAbortDeliveryCommand> {
        self.aborts.read().await.clone()
    }

    pub async fn set_provider_abort_run_ids(&self, run_ids: Vec<String>) {
        *self.provider_abort_run_ids.write().await = run_ids;
    }
}

#[async_trait]
impl BotDeliveryPort for RecordingBotDelivery {
    async fn deliver_on_connection(&self, cmd: BotDeliveryCommand, connection_id: &str) -> ServiceResult<BotDeliveryResult> {
        if self.connection_identity(&cmd.target).await.as_deref() != Some(connection_id) {
            return Err(ServiceError::BotNotConnected(cmd.target_bot_id().to_string()));
        }
        self.deliver(cmd).await
    }
    async fn abort_on_connection(&self, cmd: BotAbortDeliveryCommand, connection_id: &str) -> ServiceResult<BotAbortDeliveryResult> {
        if self.connection_identity(&cmd.target).await.as_deref() != Some(connection_id) {
            return Err(ServiceError::BotNotConnected(cmd.target_bot_id().to_string()));
        }
        self.abort(cmd).await
    }
    async fn connection_identity(&self, target: &BotDeliveryTarget) -> Option<String> {
        matches!(target, BotDeliveryTarget::WebSocket { .. }).then(|| format!("test-connection-{}", target.bot_id()))
    }

    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        true
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let target_bot_id = cmd.target_bot_id().to_string();
        self.targets.write().await.push(cmd.target.clone());
        self.kinds.write().await.push(cmd.delivery_kind);
        self.provider_transports
            .write()
            .await
            .push(cmd.provider_transport);
        self.frames.write().await.push(cmd.frame);
        if self.fail_for.read().await.contains(&target_bot_id) {
            return Err(ServiceError::BotNotConnected(target_bot_id));
        }
        if self.not_delivered_for.read().await.contains(&target_bot_id) {
            return Ok(BotDeliveryResult {
                target_bot_id: target_bot_id.clone(),
                delivered: false,
                error: Some(ServiceError::BotNotConnected(target_bot_id)),
            });
        }
        Ok(BotDeliveryResult {
            target_bot_id,
            delivered: true,
            error: None,
        })
    }

    async fn abort(&self, cmd: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        let target_bot_id = cmd.target_bot_id().to_string();
        self.aborts.write().await.push(cmd.clone());
        if self.fail_for.read().await.contains(&target_bot_id) {
            return Err(ServiceError::BotNotConnected(target_bot_id));
        }
        let aborted_run_ids = match cmd.run_id {
            Some(run_id) => vec![run_id],
            None => self.provider_abort_run_ids.read().await.clone(),
        };
        Ok(BotAbortDeliveryResult {
            target_bot_id,
            aborted_run_ids,
        })
    }
}

#[derive(Default)]
pub struct RecordingFrontendDelivery {
    events: RwLock<Vec<String>>,
    commands: RwLock<Vec<FrontendDeliveryCommand>>,
    fail_publish: RwLock<bool>,
}

impl RecordingFrontendDelivery {
    pub async fn events(&self) -> Vec<String> {
        self.events.read().await.clone()
    }

    pub async fn commands(&self) -> Vec<FrontendDeliveryCommand> {
        self.commands.read().await.clone()
    }

    pub async fn fail_publish(&self) {
        *self.fail_publish.write().await = true;
    }
}

#[async_trait]
impl FrontendDeliveryPort for RecordingFrontendDelivery {
    async fn publish(&self, cmd: FrontendDeliveryCommand) -> ServiceResult<FrontendDeliveryResult> {
        if *self.fail_publish.read().await {
            return Err(ServiceError::InternalError("publish failed".to_string()));
        }
        self.events.write().await.push(cmd.event_json.clone());
        self.commands.write().await.push(cmd.clone());
        Ok(FrontendDeliveryResult {
            target: cmd.target,
            delivered: 1,
        })
    }

    async fn unregister_run(&self, _run_id: &str) -> ServiceResult<()> {
        Ok(())
    }
}
