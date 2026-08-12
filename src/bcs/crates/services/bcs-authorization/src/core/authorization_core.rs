use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use serde_json::json;
use uuid::Uuid;

use bcs_service_api::port::repo::{
    AuthzDecisionLogRepoPort, EdgeGrantRepoPort, PermissionProfileRepoPort,
};
use bcs_service_api::{
    AuthzContext, AuthzContextBuilderCoreService, AuthzContextType, AuthzDecisionLog,
    AuthzGrantRef, BuildA2aAuthzContextRequest, Decision, EdgeGrant, GrantKind, GrantSource,
    OriginatorPolicyType, PermissionProfile, RuntimeContext, ServiceError, ServiceResult,
};

#[derive(Debug, Clone, Copy, Default)]
pub struct AuthzContextBuilderServiceConfig {}

pub struct AuthzContextBuilderService {
    edge_grants: Arc<dyn EdgeGrantRepoPort>,
    permission_profiles: Arc<dyn PermissionProfileRepoPort>,
    decision_logs: Arc<dyn AuthzDecisionLogRepoPort>,
}

impl AuthzContextBuilderService {
    pub fn new(
        edge_grants: Arc<dyn EdgeGrantRepoPort>,
        permission_profiles: Arc<dyn PermissionProfileRepoPort>,
        decision_logs: Arc<dyn AuthzDecisionLogRepoPort>,
    ) -> Self {
        Self::with_config(
            edge_grants,
            permission_profiles,
            decision_logs,
            AuthzContextBuilderServiceConfig::default(),
        )
    }

    pub fn with_config(
        edge_grants: Arc<dyn EdgeGrantRepoPort>,
        permission_profiles: Arc<dyn PermissionProfileRepoPort>,
        decision_logs: Arc<dyn AuthzDecisionLogRepoPort>,
        _config: AuthzContextBuilderServiceConfig,
    ) -> Self {
        Self {
            edge_grants,
            permission_profiles,
            decision_logs,
        }
    }

    async fn grant_ref_from_edge(
        &self,
        grant: &EdgeGrant,
    ) -> ServiceResult<Option<AuthzGrantRef>> {
        match grant.grant_kind {
            GrantKind::PermissionProfile => {
                let profile = self
                    .load_current_active_profile(&grant.to_id, &grant.env, &grant.grant_ref_id)
                    .await?
                    .ok_or_else(|| {
                        ServiceError::Forbidden(format!(
                            "permission profile '{}' is not active for target bot '{}'",
                            grant.grant_ref_id, grant.to_id
                        ))
                    })?;
                Ok(Some(profile_ref(&profile, GrantSource::EdgeGrant)))
            }
            GrantKind::Rules => Ok(Some(AuthzGrantRef {
                kind: GrantKind::Rules,
                ref_id: grant.grant_ref_id.clone(),
                revision: None,
                digest: None,
                source: GrantSource::EdgeGrant,
            })),
        }
    }

    async fn load_current_active_profile(
        &self,
        bot_id: &str,
        env: &str,
        permission_profile_id: &str,
    ) -> ServiceResult<Option<PermissionProfile>> {
        let profiles = self
            .permission_profiles
            .list_active_permission_profiles(bot_id, env)
            .await?;
        Ok(profiles
            .into_iter()
            .filter(|profile| profile.permission_profile_id == permission_profile_id)
            .max_by(|left, right| left.revision.cmp(&right.revision)))
    }

    async fn maybe_default_ref(
        &self,
        to_id: &str,
        env: &str,
        source: GrantSource,
    ) -> ServiceResult<Option<AuthzGrantRef>> {
        Ok(self
            .permission_profiles
            .load_active_default_permission_profile(to_id, env)
            .await?
            .map(|profile| profile_ref(&profile, source)))
    }

    async fn append_decision_log(
        &self,
        context: &AuthzContext,
        decision: Decision,
        reason_code: &str,
    ) -> ServiceResult<()> {
        self.decision_logs
            .append_authz_decision_log(AuthzDecisionLog {
                decision_id: Uuid::new_v4().to_string(),
                env: context.env.clone(),
                task_id: context.task_id.clone(),
                run_id: context.run_id.clone(),
                from_id: context.from_id.clone(),
                to_id: context.to_id.clone(),
                originator: context.originator.clone(),
                context_type: context_type(&context.context),
                decision,
                reason_code: reason_code.to_string(),
                grant_refs: context.grants.clone(),
                context_json: Some(json!({
                    "context": context.context,
                })),
                created_at: now_millis(),
            })
            .await
    }
}

#[async_trait]
impl AuthzContextBuilderCoreService for AuthzContextBuilderService {
    async fn build_a2a_authz_context(
        &self,
        request: BuildA2aAuthzContextRequest,
    ) -> ServiceResult<AuthzContext> {
        let mut context = AuthzContext {
            task_id: request.task_id,
            run_id: request.run_id,
            from_id: request.from_id,
            to_id: request.to_id,
            env: request.env,
            originator: request.originator,
            context: request.context,
            grants: Vec::new(),
            signature: None,
        };

        let edge_grants = self
            .edge_grants
            .list_approved_active_edge_grants(&context.from_id, &context.to_id, &context.env)
            .await?;
        for grant in edge_grants {
            if !originator_policy_allows(&grant, context.originator.as_deref(), &context.from_id) {
                continue;
            }
            if let Some(grant_ref) = self.grant_ref_from_edge(&grant).await? {
                push_unique(&mut context.grants, grant_ref);
            }
        }

        match &context.context {
            RuntimeContext::Direct => {}
            RuntimeContext::PublicChat => {
                if let Some(grant_ref) = self
                    .maybe_default_ref(&context.to_id, &context.env, GrantSource::PublicDefault)
                    .await?
                {
                    push_unique(&mut context.grants, grant_ref);
                }
            }
            RuntimeContext::Collaboration { .. } => {
                if let Some(grant_ref) = self
                    .maybe_default_ref(
                        &context.to_id,
                        &context.env,
                        GrantSource::CollaborationDefault,
                    )
                    .await?
                {
                    push_unique(&mut context.grants, grant_ref);
                }
            }
        }

        if context.grants.is_empty() {
            self.append_decision_log(&context, Decision::Deny, "no_active_grants")
                .await?;
            return Err(ServiceError::Forbidden(format!(
                "no active authz grants from '{}' to '{}' in env '{}'",
                context.from_id, context.to_id, context.env
            )));
        }

        self.append_decision_log(&context, Decision::Allow, "active_grants_resolved")
            .await?;
        Ok(context)
    }
}


fn originator_policy_allows(grant: &EdgeGrant, originator: Option<&str>, from_id: &str) -> bool {
    match grant.originator_policy_type {
        OriginatorPolicyType::Any => true,
        OriginatorPolicyType::SameAsFrom => originator == Some(from_id),
        OriginatorPolicyType::Specific => {
            let Some(originator) = originator else {
                return false;
            };
            grant
                .originator_policy_data
                .as_ref()
                .and_then(|data| data.get("allowed_originators"))
                .and_then(|allowed| allowed.as_array())
                .is_some_and(|allowed| allowed.iter().any(|value| value.as_str() == Some(originator)))
        }
        OriginatorPolicyType::Owner => false,
    }
}

fn profile_ref(profile: &PermissionProfile, source: GrantSource) -> AuthzGrantRef {
    AuthzGrantRef {
        kind: GrantKind::PermissionProfile,
        ref_id: profile.permission_profile_id.clone(),
        revision: Some(profile.revision),
        digest: Some(profile.digest.clone()),
        source,
    }
}

fn context_type(context: &RuntimeContext) -> AuthzContextType {
    match context {
        RuntimeContext::Direct => AuthzContextType::Direct,
        RuntimeContext::PublicChat => AuthzContextType::PublicChat,
        RuntimeContext::Collaboration { .. } => AuthzContextType::Collaboration,
    }
}

fn push_unique(grants: &mut Vec<AuthzGrantRef>, grant_ref: AuthzGrantRef) {
    if !grants
        .iter()
        .any(|grant| grant.kind == grant_ref.kind && grant.ref_id == grant_ref.ref_id)
    {
        grants.push(grant_ref);
    }
}

fn now_millis() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as i64)
        .unwrap_or(0)
}
