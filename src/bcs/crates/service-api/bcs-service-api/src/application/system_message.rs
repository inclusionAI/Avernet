//! Application-level system-message service.

use async_trait::async_trait;
use bcs_domain::{Participant, SystemMessageEvent};

use crate::ServiceResult;

#[async_trait]
pub trait SystemMessageService: Send + Sync {
    async fn notify(
        &self,
        group_id: &str,
        event: SystemMessageEvent,
        session_id: &str,
        session_participants: &[Participant],
    ) -> ServiceResult<usize>;
}