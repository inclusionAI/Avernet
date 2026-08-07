use std::sync::Arc;

use async_trait::async_trait;
use serde_json::json;
use uuid::Uuid;

use bcs_service_api::port::repo::{
    AuthzDecisionLogRepoPort, EdgeGrantRepoPort, PermissionProfileRepoPort,
};
use bcs_service_api::{
    AuthzContext, AuthzContextBuilderCoreService, AuthzContextType, AuthzDecisionLog,
    AuthzGrantRef, BuildA2aAuthzContextRequest, Decision, EdgeGrant, GrantKind, GrantSource,
    PermissionProfile, RuntimeContext, ServiceError, ServiceResult,
};

#[derive(Debug, Clone, Copy)]
pub struct AuthzContextBuilderServiceConfig {
    pub default_ttl_ms: i64,
}

impl Default for AuthzContextBuilderServiceConfig {
    fn default() -> Self {
        Self {
            default_ttl_ms: 5 * 60 * 1000,
        }
    }
}

pub struct AuthzContextBuilderService {
    edge_grants: Arc<dyn EdgeGrantRepoPort>,
    permission_profiles: Arc<dyn PermissionProfileRepoPort>,
    decision_logs: Arc<dyn AuthzDecisionLogRepoPort>,
    config: AuthzContextBuilderServiceConfig,
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
        config: AuthzContextBuilderServiceConfig,
    ) -> Self {
        Self {
            edge_grants,
            permission_profiles,
            decision_logs,
            config,
        }
    }

    async fn grant_ref_from_edge(
        &self,
        grant: &EdgeGrant,
        issued_at: i64,
    ) -> ServiceResult<Option<AuthzGrantRef>> {
        if grant
            .expires_at
            .is_some_and(|expires_at| expires_at <= issued_at)
        {
            return Ok(None);
        }

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
            GrantKind::Rules => {
                let revision = grant.rules_revision.ok_or_else(|| {
                    ServiceError::Forbidden(format!(
                        "rules grant '{}' is missing revision",
                        grant.edge_id
                    ))
                })?;
                let digest = grant.rules_digest.clone().ok_or_else(|| {
                    ServiceError::Forbidden(format!(
                        "rules grant '{}' is missing digest",
                        grant.edge_id
                    ))
                })?;
                Ok(Some(AuthzGrantRef {
                    kind: GrantKind::Rules,
                    ref_id: grant.grant_ref_id.clone(),
                    revision,
                    digest,
                    source: GrantSource::EdgeGrant,
                }))
            }
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
                    "issued_at": context.issued_at,
                    "expires_at": context.expires_at,
                })),
                created_at: context.issued_at,
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
        let ttl_ms = if request.ttl_ms > 0 {
            request.ttl_ms
        } else {
            self.config.default_ttl_ms
        };
        let mut context = AuthzContext {
            task_id: request.task_id,
            run_id: request.run_id,
            from_id: request.from_id,
            to_id: request.to_id,
            env: request.env,
            originator: request.originator,
            context: request.context,
            grants: Vec::new(),
            issued_at: request.issued_at,
            expires_at: request.issued_at + ttl_ms,
            signature: None,
        };

        let edge_grants = self
            .edge_grants
            .list_approved_active_edge_grants(&context.from_id, &context.to_id, &context.env)
            .await?;
        for grant in edge_grants {
            if let Some(grant_ref) = self.grant_ref_from_edge(&grant, context.issued_at).await? {
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

fn profile_ref(profile: &PermissionProfile, source: GrantSource) -> AuthzGrantRef {
    AuthzGrantRef {
        kind: GrantKind::PermissionProfile,
        ref_id: profile.permission_profile_id.clone(),
        revision: profile.revision,
        digest: profile.digest.clone(),
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
