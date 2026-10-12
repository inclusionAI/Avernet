//! Signed identity is fixed by Gateway; current persisted state remains authoritative.
use std::sync::Arc;
use async_trait::async_trait;
use axum::http::HeaderMap;
use bcs_api_http::{PrincipalVerifier, PrincipalVerificationError};
use bcs_service_api::{core::BotRegistryCoreService, ServiceError};
use bcs_service_api::application::v1::AuthenticatedCaller;

pub(crate) struct StatefulPrincipalVerifier {
    pub inner: Arc<dyn PrincipalVerifier>,
    pub registry: Arc<dyn BotRegistryCoreService>,
}

#[async_trait]
impl PrincipalVerifier for StatefulPrincipalVerifier {
    async fn verify(&self, headers: &HeaderMap) -> Result<AuthenticatedCaller, PrincipalVerificationError> {
        let caller = self.inner.verify(headers).await?;
        if let Some(bot) = &caller.bot {
            self.registry.validate_signed_bot(&bot.bot_uuid).await.map_err(|error| match error {
                ServiceError::Unauthorized(_) | ServiceError::Forbidden(_) | ServiceError::BotNotFound(_) =>
                    PrincipalVerificationError::Invalid("Bot is unavailable".into()),
                _ => PrincipalVerificationError::Unavailable,
            })?;
        }
        Ok(caller)
    }
}
