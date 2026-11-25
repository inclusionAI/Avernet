//! Storage-failure authority hook for consumer error-propagation tests.

use async_trait::async_trait;
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::types::bot_authority::{BotAccessRelation, BotOwnershipView, BotRoleQuery};

/// The failing hook as a `Arc<dyn BotAuthorityPort>`.
pub fn failing_authority_hook() -> std::sync::Arc<dyn BotAuthorityPort> {
    std::sync::Arc::new(FailingAuthorityHook)
}

/// A failing authority hook (Important#3 pin):
/// every read returns a non-Forbidden storage
/// failure, so consumers can be pinned to
/// surface the failure as an internal error —
/// never a deny-as-false masquerade.
pub struct FailingAuthorityHook;

#[async_trait]
impl BotAuthorityPort for FailingAuthorityHook {
    async fn role(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<Option<BotAccessRelation>> {
        Err(bcs_service_api::ServiceError::InternalError(
            "authority storage unavailable".to_string(),
        ))
    }

    async fn roles_for(
        &self,
        _queries: &[BotRoleQuery],
    ) -> bcs_service_api::ServiceResult<
        Vec<Option<BotAccessRelation>>,
    > {
        Err(bcs_service_api::ServiceError::InternalError(
            "authority storage unavailable".to_string(),
        ))
    }

    async fn can_manage(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<bool> {
        Err(bcs_service_api::ServiceError::InternalError(
            "authority storage unavailable".to_string(),
        ))
    }

    async fn require_owner(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<()> {
        Err(bcs_service_api::ServiceError::InternalError(
            "authority storage unavailable".to_string(),
        ))
    }

    async fn require_manage(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<()> {
        Err(bcs_service_api::ServiceError::InternalError(
            "authority storage unavailable".to_string(),
        ))
    }

    async fn ownerships_for(
        &self,
        _bot_ids: &[String],
    ) -> bcs_service_api::ServiceResult<
        Vec<Option<BotOwnershipView>>,
    > {
        Err(bcs_service_api::ServiceError::InternalError(
            "authority storage unavailable".to_string(),
        ))
    }
}
