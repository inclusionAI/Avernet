//! Application-level system-message service implementation.
//!
//! Wraps the dispatcher behind the `SystemMessageService` trait so that
//! callers can fire-and-forget notifications without knowing the
//! dispatcher internals.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{Participant, SystemMessageEvent};
use bcs_service_api::{
    GroupCoreService, ServiceError, ServiceResult, SystemMessageDispatcherService, SystemMessageService,
};

/// Concrete `SystemMessageService` backed by a dispatcher.
pub struct SystemMessageServiceImpl {
    dispatcher: Arc<dyn SystemMessageDispatcherService>,
    group_svc: Arc<dyn GroupCoreService>,
}

impl SystemMessageServiceImpl {
    /// Create a new service from its dependencies.
    pub fn new(
        dispatcher: Arc<dyn SystemMessageDispatcherService>,
        group_svc: Arc<dyn GroupCoreService>,
    ) -> Self {
        Self {
            dispatcher,
            group_svc,
        }
    }
}

#[async_trait]
impl SystemMessageService for SystemMessageServiceImpl {
    async fn notify(
        &self,
        group_id: &str,
        event: SystemMessageEvent,
        session_id: &str,
        session_participants: &[Participant],
    ) -> ServiceResult<usize> {
        let group = self
            .group_svc
            .get(group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(group_id.to_string()))?;
        let outcome = self.dispatcher.dispatch(event, &group, session_id, session_participants).await?;
        Ok(outcome.successful_deliveries)
    }
}