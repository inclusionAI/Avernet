//! Trusted host delegation feed. This is NOT an ingress/admin endpoint and may
//! only be selected by validated composition, never a self-asserted Agent body.
use super::management_reads::principal_hash;
use super::*;
use bcs_service_api::port::repo::agent_invoke::AgentInvokeGovernanceRepoPort;
use bcs_db_api::DbTransactionStep as Step;
impl DbAgentInvokeStore {
    async fn publish_delegation_inner(
        &self,
        principal: &AgentInvokePrincipal,
        pair: &AgentInvokePair,
        expected_revision: u64,
        actions: Vec<AgentInvokeAction>,
        reason: &str,
    ) -> AgentInvokeResult<NonZeroU64> {
        Self::validate_pair(pair)?;
        if !matches!(principal, AgentInvokePrincipal::Governance(_))
            || principal.id().trim().is_empty()
            || principal.id().chars().count() > 256
            || reason.trim().is_empty()
            || reason.len() > 2048
        {
            return Err(AgentInvokeError::InvalidInput);
        }
        for (index, action) in actions.iter().enumerate() {
            if actions[..index].contains(action) {
                return Err(AgentInvokeError::InvalidInput);
            }
        }
        let domain = self.read_scope_row().await?;
        let statement = self.delegation_statement(domain.id, principal, pair);
        let rows = self.query(statement).await?;
        let before = match rows.as_slice() {
            [] => None,
            [row] => {
                if string(row, "principal_id")? != principal.id() {
                    return Err(AgentInvokeError::InvalidState);
                }
                Some(row)
            }
            _ => return Err(AgentInvokeError::InvalidState),
        };
        if before
            .map(|r| positive(r, "revision"))
            .transpose()?
            .map_or(0, NonZeroU64::get)
            != expected_revision
        {
            return Err(AgentInvokeError::Conflict);
        }
        let revision = advance(expected_revision)?;
        let scope_revision = advance(domain.revision.get())?;
        let name = |a: &AgentInvokeAction| match a {
            AgentInvokeAction::Request => "request",
            AgentInvokeAction::Grant => "grant",
            AgentInvokeAction::Approve => "approve",
            AgentInvokeAction::Reject => "reject",
            AgentInvokeAction::Cancel => "cancel",
            AgentInvokeAction::Revoke => "revoke",
            AgentInvokeAction::Query => "query",
        };
        let names: Vec<_> = actions.iter().map(name).collect();
        let json = serde_json::json!(names).to_string();
        let hash = principal_hash(principal);
        let mut params = vec![revision.get().into(), json.clone().into()];
        let mutation = if before.is_some() {
            params.extend([
                domain.id.into(),
                hash.as_str().into(),
                principal.id().into(),
                pair.from_bot_id.as_str().into(),
                pair.to_bot_id.as_str().into(),
                expected_revision.into(),
            ]);
            DbStatement::with_params(
                format!(
                    "UPDATE agent_invoke_governance SET revision=?,actions_json=?,updated_at_ms={} WHERE scope_id=? AND principal_hash=? AND principal_id=? AND from_id=? AND to_id=? AND revision=?",
                    self.now_ms()
                ),
                params,
            )
        } else {
            DbStatement::with_params(
                format!(
                    "INSERT INTO agent_invoke_governance (revision,actions_json,scope_id,principal_hash,principal_id,from_id,to_id,updated_at_ms) VALUES (?,?,?,?,?,?,?,{})",
                    self.now_ms()
                ),
                vec![
                    revision.get().into(),
                    json.clone().into(),
                    domain.id.into(),
                    hash.into(),
                    principal.id().into(),
                    pair.from_bot_id.as_str().into(),
                    pair.to_bot_id.as_str().into(),
                ],
            )
        };
        let after=serde_json::json!({"principal_id":principal.id(),"actions":names,"revision":revision.get()}).to_string();
        let before = if let Some(row) = before {
            serde_json::json!({"principal_id":principal.id(),"actions":serde_json::from_str::<serde_json::Value>(&string(row,"actions_json")?).map_err(|_|AgentInvokeError::InvalidState)?,"revision":expected_revision}).to_string()
        } else {
            "null".into()
        };
        let steps = vec![
            Step::ExecuteChecked {
                statement: DbStatement::with_params(
                    "UPDATE agent_invoke_scopes SET revision=? WHERE id=? AND revision=?",
                    vec![
                        scope_revision.get().into(),
                        domain.id.into(),
                        domain.revision.get().into(),
                    ],
                ),
                expected_affected_rows: 1,
            },
            Step::ExecuteChecked {
                statement: mutation,
                expected_affected_rows: 1,
            },
            Step::ExecuteChecked {
                statement: DbStatement::with_params(
                    format!(
                        "INSERT INTO agent_invoke_audit (scope_id,action_id,action,principal_kind,principal_id,from_id,to_id,reason,before_json,after_json,created_at_ms) VALUES (?,?,'delegation_publish','inventory','trusted-delegation-publisher',?,?,?,?,?,{})",
                        self.now_ms()
                    ),
                    vec![
                        domain.id.into(),
                        format!("delegation-{}", scope_revision.get()).into(),
                        pair.from_bot_id.as_str().into(),
                        pair.to_bot_id.as_str().into(),
                        reason.into(),
                        before.into(),
                        after.into(),
                    ],
                ),
                expected_affected_rows: 1,
            },
        ];
        self.db
            .transaction_with_options(steps, self.transaction_options())
            .await
            .map_err(write_error)?;
        Ok(revision)
    }
}

#[async_trait::async_trait]
impl AgentInvokeGovernanceRepoPort for DbAgentInvokeStore {
    async fn publish_delegation(
        &self,
        scope: &AgentInvokeScope,
        p: AgentInvokeDelegationPublication,
    ) -> AgentInvokeResult<NonZeroU64> {
        self.require_scope(scope)?;
        self.publish_delegation_inner(
            &p.principal,
            &p.pair,
            p.expected_revision,
            p.actions,
            &p.reason,
        )
        .await
    }
}
