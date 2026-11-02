use async_trait::async_trait;
use crate::{ServiceResult, port::repo::a2a_bot::A2aBotRecord};

#[derive(Clone)]
pub struct ConfigureA2aBotCommand {
    pub provider_id: String,
    pub provider_admin_token: String,
    pub bot_uuid: String,
    pub source: crate::types::a2a_bot::A2aCardSource,
    pub expected_revision: u64,
    pub now_ms: i64,
}

#[async_trait]
pub trait A2aBotManagementService: Send + Sync {
    /// Provider-admin admission: fetch before the atomic Bot/ownership/Card
    /// create. Existing Provider/ref conflicts; this never updates a Bot.
    async fn register_provider_bot(
        &self, _command: crate::RegisterProviderBotCommand, _source: crate::types::a2a_bot::A2aCardSource, _now_ms: i64,
    ) -> ServiceResult<crate::RegisterProviderBotOutcome> {
        Err(crate::ServiceError::InvalidOperation {
            message: "A2A Provider registration is not configured".into(), request_id: None,
        })
    }
    async fn get(&self, provider_id: &str, admin_token: &str, bot_uuid: &str) -> ServiceResult<Option<A2aBotRecord>>;
    async fn configure(&self, command: ConfigureA2aBotCommand) -> ServiceResult<A2aBotRecord>;
    /// Re-read the saved source URL. Inline Cards require explicit configure.
    async fn refresh(&self, provider_id: &str, admin_token: &str, bot_uuid: &str, now_ms: i64)
        -> ServiceResult<A2aBotRecord>;
    /// Trusted background maintenance only; never expose without authorization
    /// as an HTTP endpoint. Reuses saved URLs and cannot configure new routes.
    async fn refresh_due(&self, now_ms: i64) -> ServiceResult<usize>;
}
