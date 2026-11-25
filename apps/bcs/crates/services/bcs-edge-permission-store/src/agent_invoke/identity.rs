//! Byte-exact credential -> physical Bot handoff. One bounded read, no auto-
//! claim, code-only lookup, public/default fallback, cache or database writes.
use super::*;
use async_trait::async_trait;
use bcs_service_api::port::agent_invoke::AgentInvokeIdentityResolutionPort;

#[async_trait]
impl AgentInvokeIdentityResolutionPort for DbAgentInvokeStore {
    async fn resolve_invocation_source(
        &self,
        scope: &AgentInvokeScope,
        identity: &AgentInvokeVerifiedBot,
    ) -> AgentInvokeResult<AgentInvokeAuthenticatedSource> {
        self.resolve_verified_source(scope, identity).await
    }
}

impl DbAgentInvokeStore {
    // Keep the implementation outside async_trait's macro span so coverage
    // measures actual policy/SQL/error paths, not only the generated braces.
    async fn resolve_verified_source(
        &self,
        scope: &AgentInvokeScope,
        identity: &AgentInvokeVerifiedBot,
    ) -> AgentInvokeResult<AgentInvokeAuthenticatedSource> {
        self.require_scope(scope)?;
        Self::validate_bot_id(&identity.bot_id)?;
        if identity.agent_code.trim().is_empty() || identity.agent_code.chars().count() > 256 {
            return Err(AgentInvokeError::Unauthenticated);
        }
        let sql = format!(
            "SELECT s.*, {} AS db_now_ms, b.id AS registration_id, b.bot_uuid, b.agent_code, \
             b.actor_kind, b.is_deleted, b.status, b.visibility, \
             b.agent_invoke_identity_revision AS identity_revision, \
             f.bot_id AS fact_bot_id, f.binding_generation, f.eligibility_generation, \
             f.eligible, f.active, f.hidden, f.valid_until_ms, f.revision AS fact_revision, \
             f.registration_id AS bound_registration_id, f.registration_revision AS bound_identity_revision \
             FROM agent_invoke_scopes s \
             LEFT JOIN bcs_bots b ON b.env = s.env AND {} = {} AND b.bot_uuid = ? AND {} = ? \
             LEFT JOIN agent_invoke_facts f ON f.scope_id = s.id AND f.bot_id = b.bot_uuid AND {} = {} \
             WHERE s.namespace = ? AND s.env = ? AND {} = ? AND {} = ? LIMIT 2",
            self.now_ms(),
            self.exact("b.env"),
            self.exact("s.env"),
            self.exact("b.bot_uuid"),
            self.exact("f.bot_id"),
            self.exact("b.bot_uuid"),
            self.exact("s.namespace"),
            self.exact("s.env"),
        );
        let rows = self
            .query(DbStatement::with_params(
                sql,
                vec![
                    identity.bot_id.as_str().into(),
                    identity.bot_id.as_str().into(),
                    scope.namespace.as_str().into(),
                    scope.env.as_str().into(),
                    scope.namespace.as_str().into(),
                    scope.env.as_str().into(),
                ],
            ))
            .await?;
        let domain = self.decode_scope_rows(&rows)?;
        if !domain.state.runtime_enabled {
            return Err(AgentInvokeError::Unavailable);
        }
        let row = &rows[0];
        if row.get("registration_id") == Some(&DbValue::Null) {
            return Err(AgentInvokeError::Forbidden);
        }
        if string(row, "bot_uuid")? != identity.bot_id
            || row.get("agent_code") == Some(&DbValue::Null)
            || string(row, "agent_code")? != identity.agent_code
            || string(row, "actor_kind")? != "bot"
            || boolean(row, "is_deleted")?
        {
            return Err(AgentInvokeError::Forbidden);
        }
        let status = string(row, "status")?;
        if !matches!(status.as_str(), "online" | "offline" | "hidden") {
            return Err(AgentInvokeError::InvalidState);
        }
        if status == "hidden" || string(row, "visibility")? == "hidden" {
            return Err(AgentInvokeError::Forbidden);
        }
        if row.get("fact_bot_id") == Some(&DbValue::Null) {
            return Err(AgentInvokeError::Unavailable);
        }
        if string(row, "fact_bot_id")? != identity.bot_id {
            return Err(AgentInvokeError::InvalidState);
        }
        let registration = AgentInvokeRegistrationBinding {
            registration_id: positive(row, "registration_id")?,
            identity_revision: positive(row, "identity_revision")?,
        };
        positive(row, "fact_revision")?;
        if positive(row, "bound_registration_id")? != registration.registration_id
            || positive(row, "bound_identity_revision")? != registration.identity_revision
            || !boolean(row, "eligible")?
            || !boolean(row, "active")?
            || boolean(row, "hidden")?
        {
            return Err(AgentInvokeError::Forbidden);
        }
        let valid_until = positive(row, "valid_until_ms")?.get();
        if valid_until <= domain.now_ms {
            return Err(AgentInvokeError::Forbidden);
        }
        if valid_until - domain.now_ms > self.max_fact_ttl_ms.get() {
            return Err(AgentInvokeError::InvalidState);
        }
        Ok(AgentInvokeAuthenticatedSource {
            registration,
            binding_generation: positive(row, "binding_generation")?,
            eligibility_generation: positive(row, "eligibility_generation")?,
        })
    }
}
