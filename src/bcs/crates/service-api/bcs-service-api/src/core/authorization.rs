use async_trait::async_trait;

use crate::application::principal::CallerContext;
use crate::types::{AuthzContext, RuntimeContext, ServiceResult};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuildA2aAuthzContextRequest {
    pub from_id: String,
    pub to_id: String,
    pub env: String,
    pub caller: CallerContext,
    pub originator: Option<String>,
    pub context: RuntimeContext,
    pub task_id: Option<String>,
    pub run_id: Option<String>,
    pub issued_at: i64,
    pub ttl_ms: i64,
}

#[async_trait]
pub trait AuthzContextBuilderCoreService: Send + Sync {
    async fn build_a2a_authz_context(
        &self,
        request: BuildA2aAuthzContextRequest,
    ) -> ServiceResult<AuthzContext>;
}
