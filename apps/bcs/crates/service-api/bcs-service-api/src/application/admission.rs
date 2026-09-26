//! `AdmissionService` — inbound use case for workbench/A2A admission.
use async_trait::async_trait;
use bcs_domain::edge_permission::{AdmissionResult, AuthzContext};

use crate::core::error::ServiceResult;

#[async_trait]
pub trait AdmissionService: Send + Sync {
    /// `GET /bots/{id}/admission`: ① status≠hidden ② friend edge ③ public_default ④ deny.
    async fn check_admission(
        &self,
        actor: &str,
        bot: &str,
        originator: &str,
        env: &str,
    ) -> ServiceResult<AdmissionResult>;

    /// Build the slim runtime context injected into A2A messages (path 2 of §4.3).
    async fn build_authz_context(
        &self,
        from: &str,
        to: &str,
        originator: &str,
        task_id: &str,
        run_id: &str,
        env: &str,
    ) -> ServiceResult<AuthzContext>;
}
