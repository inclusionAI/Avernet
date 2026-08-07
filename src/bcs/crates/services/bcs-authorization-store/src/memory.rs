use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use tokio::sync::RwLock;

use bcs_service_api::port::repo::{
    AuthzDecisionLogRepoPort, CapabilityCatalogRepoPort, EdgeGrantRepoPort,
    PermissionProfileRepoPort, PermissionRequestRepoPort,
};
use bcs_service_api::{
    AuthzContext, AuthzContextType, AuthzDecisionLog, Capability, EdgeGrant, GrantStatus,
    PermissionProfile, PermissionProfileStatus, PermissionRequest, PermissionRequestStatus,
    ServiceResult,
};

#[derive(Debug, Default)]
pub struct MemoryAuthorizationStore {
    capabilities: RwLock<HashMap<String, Capability>>,
    permission_profiles: RwLock<HashMap<(String, i64), PermissionProfile>>,
    edge_grants: RwLock<HashMap<String, EdgeGrant>>,
    permission_requests: RwLock<HashMap<String, PermissionRequest>>,
    decision_logs: RwLock<HashMap<String, AuthzDecisionLog>>,
}

impl MemoryAuthorizationStore {
    pub fn new() -> Self {
        Self::default()
    }

    fn profile_key(profile_id: &str, revision: i64) -> (String, i64) {
        (profile_id.to_string(), revision)
    }

    fn log_matches_context(log: &AuthzDecisionLog, context: &AuthzContext) -> bool {
        log.env == context.env
            && log.from_id == context.from_id
            && log.to_id == context.to_id
            && log.task_id == context.task_id.clone()
            && log.run_id == context.run_id.clone()
            && log.originator == context.originator.clone()
            && log.grant_refs == context.grants
            && log.context_type == Self::context_type(&context.context)
            && log.created_at >= context.issued_at
            && log.created_at <= context.expires_at
    }

    fn context_type(context: &bcs_service_api::RuntimeContext) -> AuthzContextType {
        match context {
            bcs_service_api::RuntimeContext::Direct => AuthzContextType::Direct,
            bcs_service_api::RuntimeContext::PublicChat => AuthzContextType::PublicChat,
            bcs_service_api::RuntimeContext::Collaboration { .. } => {
                AuthzContextType::Collaboration
            }
        }
    }

    fn now_millis() -> i64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_millis() as i64)
            .unwrap_or(0)
    }
}

#[async_trait]
impl CapabilityCatalogRepoPort for MemoryAuthorizationStore {
    async fn list_active_capabilities(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Vec<Capability>> {
        let mut capabilities = self
            .capabilities
            .read()
            .await
            .values()
            .filter(|capability| {
                capability.bot_id == bot_id
                    && capability.env == env
                    && capability.status == bcs_service_api::CapabilityStatus::Active
            })
            .cloned()
            .collect::<Vec<_>>();
        capabilities.sort_by(|left, right| left.capability_id.cmp(&right.capability_id));
        Ok(capabilities)
    }

    async fn upsert_capabilities(&self, capabilities: Vec<Capability>) -> ServiceResult<()> {
        let mut store = self.capabilities.write().await;
        for capability in capabilities {
            store.insert(capability.capability_id.clone(), capability);
        }
        Ok(())
    }
}

#[async_trait]
impl PermissionProfileRepoPort for MemoryAuthorizationStore {
    async fn list_active_permission_profiles(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Vec<PermissionProfile>> {
        let mut profiles = self
            .permission_profiles
            .read()
            .await
            .values()
            .filter(|profile| {
                profile.bot_id == bot_id
                    && profile.env == env
                    && profile.status == PermissionProfileStatus::Active
            })
            .cloned()
            .collect::<Vec<_>>();
        profiles.sort_by(|left, right| {
            right
                .is_default
                .cmp(&left.is_default)
                .then(right.revision.cmp(&left.revision))
                .then(left.permission_profile_id.cmp(&right.permission_profile_id))
        });
        Ok(profiles)
    }

    async fn get_permission_profile_by_revision(
        &self,
        permission_profile_id: &str,
        revision: i64,
    ) -> ServiceResult<Option<PermissionProfile>> {
        Ok(self
            .permission_profiles
            .read()
            .await
            .get(&Self::profile_key(permission_profile_id, revision))
            .cloned())
    }

    async fn load_active_default_permission_profile(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<PermissionProfile>> {
        let mut profiles = self
            .permission_profiles
            .read()
            .await
            .values()
            .filter(|profile| {
                profile.bot_id == bot_id
                    && profile.env == env
                    && profile.status == PermissionProfileStatus::Active
                    && profile.is_default
            })
            .cloned()
            .collect::<Vec<_>>();
        profiles.sort_by(|left, right| {
            right
                .revision
                .cmp(&left.revision)
                .then(left.permission_profile_id.cmp(&right.permission_profile_id))
        });
        Ok(profiles.into_iter().next())
    }

    async fn upsert_permission_profile(
        &self,
        profile: PermissionProfile,
    ) -> ServiceResult<PermissionProfile> {
        self.permission_profiles.write().await.insert(
            Self::profile_key(&profile.permission_profile_id, profile.revision),
            profile.clone(),
        );
        Ok(profile)
    }
}

#[async_trait]
impl EdgeGrantRepoPort for MemoryAuthorizationStore {
    async fn list_approved_active_edge_grants(
        &self,
        from_id: &str,
        to_id: &str,
        env: &str,
    ) -> ServiceResult<Vec<EdgeGrant>> {
        let mut grants = self
            .edge_grants
            .read()
            .await
            .values()
            .filter(|grant| {
                grant.from_id == from_id
                    && grant.to_id == to_id
                    && grant.env == env
                    && grant.status == GrantStatus::Approved
            })
            .cloned()
            .collect::<Vec<_>>();
        grants.sort_by(|left, right| {
            left.created_at
                .cmp(&right.created_at)
                .then(left.edge_id.cmp(&right.edge_id))
        });
        Ok(grants)
    }

    async fn insert_edge_grant(&self, edge_grant: EdgeGrant) -> ServiceResult<EdgeGrant> {
        self.edge_grants
            .write()
            .await
            .insert(edge_grant.edge_id.clone(), edge_grant.clone());
        Ok(edge_grant)
    }
}

#[async_trait]
impl PermissionRequestRepoPort for MemoryAuthorizationStore {
    async fn insert_permission_request(
        &self,
        request: PermissionRequest,
    ) -> ServiceResult<PermissionRequest> {
        self.permission_requests
            .write()
            .await
            .insert(request.request_id.clone(), request.clone());
        Ok(request)
    }

    async fn get_permission_request(
        &self,
        request_id: &str,
    ) -> ServiceResult<Option<PermissionRequest>> {
        Ok(self
            .permission_requests
            .read()
            .await
            .get(request_id)
            .cloned())
    }

    async fn list_permission_requests_by_to_id(
        &self,
        to_id: &str,
        status: Option<PermissionRequestStatus>,
    ) -> ServiceResult<Vec<PermissionRequest>> {
        let mut requests = self
            .permission_requests
            .read()
            .await
            .values()
            .filter(|request| request.to_id == to_id)
            .filter(|request| status.is_none_or(|expected| request.status == expected))
            .cloned()
            .collect::<Vec<_>>();
        requests.sort_by(|left, right| {
            left.created_at
                .cmp(&right.created_at)
                .then(left.request_id.cmp(&right.request_id))
        });
        Ok(requests)
    }

    async fn update_permission_request_status(
        &self,
        request_id: &str,
        status: PermissionRequestStatus,
    ) -> ServiceResult<()> {
        if let Some(request) = self.permission_requests.write().await.get_mut(request_id) {
            request.status = status;
            request.updated_at = Self::now_millis();
        }
        Ok(())
    }
}

#[async_trait]
impl AuthzDecisionLogRepoPort for MemoryAuthorizationStore {
    async fn append_authz_decision_log(&self, log: AuthzDecisionLog) -> ServiceResult<()> {
        self.decision_logs
            .write()
            .await
            .insert(log.decision_id.clone(), log);
        Ok(())
    }

    async fn list_recent_authz_decision_logs(
        &self,
        authz_context: &AuthzContext,
    ) -> ServiceResult<Vec<AuthzDecisionLog>> {
        let mut logs = self
            .decision_logs
            .read()
            .await
            .values()
            .filter(|log| Self::log_matches_context(log, authz_context))
            .cloned()
            .collect::<Vec<_>>();
        logs.sort_by(|left, right| {
            right
                .created_at
                .cmp(&left.created_at)
                .then(right.decision_id.cmp(&left.decision_id))
        });
        Ok(logs)
    }
}
