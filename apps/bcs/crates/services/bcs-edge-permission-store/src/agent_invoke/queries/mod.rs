//! Governance reads never fabricate a write command or infer runtime permits.
mod auth;
mod decode;
mod sql;
use super::*;
use async_trait::async_trait;
use bcs_db_api::{DbTransactionStep as Step, DbTransactionStepResult};
use bcs_service_api::{
    core::agent_invoke::AgentInvokeQueryPolicyCoreService,
    port::repo::agent_invoke::AgentInvokeQueryRepoPort, types::agent_invoke_query::*,
};

pub struct DbAgentInvokeQueryStore {
    store: Arc<DbAgentInvokeStore>,
    policy: Arc<dyn AgentInvokeQueryPolicyCoreService>,
}
impl DbAgentInvokeQueryStore {
    pub fn new(
        store: Arc<DbAgentInvokeStore>,
        policy: Arc<dyn AgentInvokeQueryPolicyCoreService>,
    ) -> Self {
        Self { store, policy }
    }
}
#[async_trait]
impl AgentInvokeQueryRepoPort for DbAgentInvokeQueryStore {
    async fn read_query(
        &self,
        scope: &AgentInvokeScope,
        q: AgentInvokeQuery,
    ) -> AgentInvokeResult<AgentInvokeQueryResult> {
        self.policy.validate_query(scope, &q)?;
        self.store.require_scope(scope)?;
        // Cheap point-only preflight prevents unauthorized callers from causing
        // degree scans. It is NOT authority to return data: reauthorize below.
        let before = self
            .store
            .query(auth::statement(&self.store, &q.context))
            .await?;
        self.policy
            .authorize_query(scope, &q, &auth::decode(&self.store, &q.context, &before)?)?;
        let mut steps = vec![
            Step::Query(self.store.scope_statement(true)),
            Step::Query(self.store.bot_lock_statement(&q.context.target.bot_id)),
        ];
        if let AgentInvokePrincipal::Human(id) = &q.context.principal {
            steps.push(Step::Query(self.store.bot_lock_statement(id)));
        }
        let authority = steps.len();
        steps.push(Step::Query(auth::statement(&self.store, &q.context)));
        let data = steps.len();
        steps.extend(
            sql::statements(&self.store, &q)?
                .into_iter()
                .map(Step::Query),
        );
        let results = self
            .store
            .db
            .transaction_with_options(steps, self.store.transaction_options())
            .await
            .map_err(|_| AgentInvokeError::Unavailable)?;
        let domain = self.store.decode_scope_rows(rows(&results, 0)?)?;
        let authority_scope = self.store.decode_scope_rows(rows(&results, authority)?)?;
        if domain.id != authority_scope.id || domain.revision != authority_scope.revision {
            return Err(AgentInvokeError::Conflict);
        }

        self.policy.authorize_query(
            scope,
            &q,
            &auth::decode(&self.store, &q.context, rows(&results, authority)?)?,
        )?;
        let value = decode::result(&self.store, &q, domain.id, &results[data..])?;
        Ok(AgentInvokeQueryResult {
            context: q.context,
            snapshot_at_ms: domain.now_ms,
            data: value,
        })
    }
}
fn rows(results: &[DbTransactionStepResult], index: usize) -> AgentInvokeResult<&[DbRow]> {
    match results.get(index) {
        Some(DbTransactionStepResult::Rows(r)) => Ok(r),
        _ => Err(AgentInvokeError::InvalidState),
    }
}
fn pair(c: &AgentInvokeQueryContext, d: AgentInvokeDirection) -> Option<AgentInvokePair> {
    c.target.peer_bot_id.as_ref().map(|peer| match d {
        AgentInvokeDirection::Outbound => AgentInvokePair {
            from_bot_id: c.target.bot_id.clone(),
            to_bot_id: peer.clone(),
        },
        AgentInvokeDirection::Inbound => AgentInvokePair {
            from_bot_id: peer.clone(),
            to_bot_id: c.target.bot_id.clone(),
        },
    })
}
