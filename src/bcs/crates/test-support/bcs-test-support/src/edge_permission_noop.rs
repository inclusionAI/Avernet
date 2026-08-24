//! No-op implementations of the edge-permission repo/service traits — for DI in
//! tests/dev and as compile-check that the trait surface is object-safe.
use async_trait::async_trait;
use bcs_domain::edge_permission::{
    AdmissionReason, AdmissionResult, AuthzContext, EdgeGrant, FriendListEntry,
    PermissionProfile, PermissionRequest, RequestStatus,
};
use bcs_service_api::application::{
    AdmissionService, ConnectResult, ConnectService, ConnectStatus, RequestDirection, RequestsPage,
};
use bcs_service_api::core::error::ServiceResult;
use bcs_service_api::port::repo::{
    EdgeGrantRepoPort, PermissionProfileRepoPort, PermissionRequestRepoPort,
};
use serde_json::json;

pub struct NoopEdgeGrantRepo;
#[async_trait]
impl EdgeGrantRepoPort for NoopEdgeGrantRepo {
    async fn list_active_grants(&self, _: &str, _: &str, _: &str) -> Vec<EdgeGrant> { vec![] }
    async fn is_authorized(&self, _: &str, _: &str, _: &str) -> bool { false }
    async fn has_friend_edge(&self, _: &str, _: &str, _: &str) -> bool { false }
    async fn list_friends(&self, _: &str, _: &str) -> Vec<String> { vec![] }
    async fn insert_grant(&self, _: EdgeGrant) -> ServiceResult<u64> { Ok(1) }
    async fn revoke_grant(&self, _: u64, _: &str) -> ServiceResult<()> { Ok(()) }
    async fn get_default_profile_id(&self, _: &str, _: &str) -> Option<u64> { None }
}

pub struct NoopPermissionProfileRepo;
#[async_trait]
impl PermissionProfileRepoPort for NoopPermissionProfileRepo {
    async fn ensure_default_profile(&self, _: &str, _: &str) -> ServiceResult<u64> { Ok(1) }
    async fn get_active_default(&self, _: &str, _: &str) -> Option<PermissionProfile> { None }
    async fn upsert_revision(&self, _: PermissionProfile) -> ServiceResult<()> { Ok(()) }
}

pub struct NoopPermissionRequestRepo;
#[async_trait]
impl PermissionRequestRepoPort for NoopPermissionRequestRepo {
    async fn insert(&self, _: PermissionRequest) -> ServiceResult<u64> { Ok(1) }
    async fn get(&self, _: u64, _: &str) -> Option<PermissionRequest> { None }
    async fn list_inbox(&self, _: &str, _: &str, _: Option<RequestStatus>) -> Vec<PermissionRequest> { vec![] }
    async fn list_sent(&self, _: &str, _: &str, _: Option<RequestStatus>) -> Vec<PermissionRequest> { vec![] }
    async fn decide(&self, _: u64, _: &str, _: RequestStatus, _: &str, _: Option<&str>) -> ServiceResult<()> { Ok(()) }
    async fn backfill_edge_id(&self, _: u64, _: &str, _: u64) -> ServiceResult<()> { Ok(()) }
}

pub struct NoopConnectService;
#[async_trait]
impl ConnectService for NoopConnectService {
    async fn create_connect(&self, _: &str, _: &str, _: Option<String>) -> ServiceResult<ConnectResult> {
        Ok(ConnectResult { request_ids: vec![], edge_ids: vec![], status: ConnectStatus::Pending, auto_accepted: false })
    }
    async fn approve(&self, _: u64, _: &str) -> ServiceResult<Vec<u64>> { Ok(vec![]) }
    async fn reject(&self, _: u64, _: &str, _: Option<String>) -> ServiceResult<()> { Ok(()) }
    async fn cancel(&self, _: u64) -> ServiceResult<()> { Ok(()) }
    async fn get_request(&self, _: u64) -> ServiceResult<PermissionRequest> {
        Err(bcs_service_api::ServiceError::FriendRequestNotFound("noop".to_string()))
    }
    async fn revoke_friend(&self, _: &str, _: &str) -> ServiceResult<Vec<u64>> { Ok(vec![]) }
    async fn list_friends(&self, _: &str) -> ServiceResult<Vec<FriendListEntry>> { Ok(vec![]) }
    async fn list_requests(
        &self,
        _actor: &str,
        _direction: RequestDirection,
        _status: Option<RequestStatus>,
        page: u32,
        page_size: u32,
    ) -> ServiceResult<RequestsPage> {
        Ok(RequestsPage { items: vec![], total: 0, page, page_size })
    }
}

pub struct NoopAdmissionService;
#[async_trait]
impl AdmissionService for NoopAdmissionService {
    async fn check_admission(&self, _: &str, _: &str, _: &str, _: &str) -> ServiceResult<AdmissionResult> {
        Ok(AdmissionResult { allowed: false, grants: vec![], reason_code: AdmissionReason::NoEdge, public_default: false })
    }
    async fn build_authz_context(&self, from: &str, to: &str, originator: &str, task_id: &str, run_id: &str, env: &str) -> ServiceResult<AuthzContext> {
        Ok(AuthzContext {
            task_id: task_id.into(), run_id: run_id.into(), from_id: from.into(), to_id: to.into(),
            env: env.into(), originator: originator.into(), context: json!({}), grants: vec![], signature: None,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn traits_are_object_safe_and_noop_compiles() {
        let _repo: Box<dyn EdgeGrantRepoPort> = Box::new(NoopEdgeGrantRepo);
        let _prof: Box<dyn PermissionProfileRepoPort> = Box::new(NoopPermissionProfileRepo);
        let _req: Box<dyn PermissionRequestRepoPort> = Box::new(NoopPermissionRequestRepo);
        let _connect: Box<dyn ConnectService> = Box::new(NoopConnectService);
        let _admission: Box<dyn AdmissionService> = Box::new(NoopAdmissionService);
    }

    #[tokio::test]
    async fn noop_admission_check_returns_no_edge() {
        let svc = NoopAdmissionService;
        let r = svc.check_admission("human_1", "bot_1", "human_1", "prod").await.unwrap();
        assert!(!r.allowed);
        assert_eq!(r.reason_code, AdmissionReason::NoEdge);
    }

    #[tokio::test]
    async fn noop_build_authz_context_threads_params() {
        let svc = NoopAdmissionService;
        let ctx = svc
            .build_authz_context("h_1", "b_1", "h_1", "t_9", "r_7", "prod")
            .await
            .unwrap();
        assert_eq!(ctx.from_id, "h_1");
        assert_eq!(ctx.to_id, "b_1");
        assert_eq!(ctx.task_id, "t_9");
        assert_eq!(ctx.run_id, "r_7");
        assert_eq!(ctx.env, "prod");
        assert_eq!(ctx.originator, "h_1");
        assert!(ctx.grants.is_empty());
        assert!(ctx.signature.is_none());
    }

    #[tokio::test]
    async fn noop_list_requests_returns_empty_page_echoing_pagination() {
        let svc = NoopConnectService;
        let page = svc
            .list_requests("human_1", RequestDirection::Received, None, 2, 10)
            .await
            .unwrap();
        assert!(page.items.is_empty());
        assert_eq!(page.total, 0);
        assert_eq!(page.page, 2);
        assert_eq!(page.page_size, 10);
    }
}
