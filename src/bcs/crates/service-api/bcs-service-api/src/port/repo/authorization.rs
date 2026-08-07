use async_trait::async_trait;

use crate::types::{
    AuthzContext, AuthzDecisionLog, Capability, EdgeGrant, PermissionProfile, PermissionRequest,
    PermissionRequestStatus, ServiceResult,
};

#[async_trait]
pub trait CapabilityCatalogRepoPort: Send + Sync {
    async fn list_active_capabilities(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Vec<Capability>>;

    async fn upsert_capabilities(&self, capabilities: Vec<Capability>) -> ServiceResult<()>;
}

#[async_trait]
pub trait PermissionProfileRepoPort: Send + Sync {
    async fn list_active_permission_profiles(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Vec<PermissionProfile>>;

    async fn get_permission_profile_by_revision(
        &self,
        permission_profile_id: &str,
        revision: i64,
    ) -> ServiceResult<Option<PermissionProfile>>;

    async fn load_active_default_permission_profile(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<PermissionProfile>>;

    async fn upsert_permission_profile(
        &self,
        profile: PermissionProfile,
    ) -> ServiceResult<PermissionProfile>;
}

#[async_trait]
pub trait EdgeGrantRepoPort: Send + Sync {
    async fn list_approved_active_edge_grants(
        &self,
        from_id: &str,
        to_id: &str,
        env: &str,
    ) -> ServiceResult<Vec<EdgeGrant>>;

    async fn insert_edge_grant(&self, edge_grant: EdgeGrant) -> ServiceResult<EdgeGrant>;
}

#[async_trait]
pub trait PermissionRequestRepoPort: Send + Sync {
    async fn insert_permission_request(
        &self,
        request: PermissionRequest,
    ) -> ServiceResult<PermissionRequest>;

    async fn get_permission_request(
        &self,
        request_id: &str,
    ) -> ServiceResult<Option<PermissionRequest>>;

    async fn list_permission_requests_by_to_id(
        &self,
        to_id: &str,
        status: Option<PermissionRequestStatus>,
    ) -> ServiceResult<Vec<PermissionRequest>>;

    async fn update_permission_request_status(
        &self,
        request_id: &str,
        status: PermissionRequestStatus,
    ) -> ServiceResult<()>;
}

#[async_trait]
pub trait AuthzDecisionLogRepoPort: Send + Sync {
    async fn append_authz_decision_log(&self, log: AuthzDecisionLog) -> ServiceResult<()>;

    async fn list_recent_authz_decision_logs(
        &self,
        authz_context: &AuthzContext,
    ) -> ServiceResult<Vec<AuthzDecisionLog>>;
}
