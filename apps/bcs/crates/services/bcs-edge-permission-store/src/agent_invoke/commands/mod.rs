//! Durable command orchestration. Every permission effect, audit and receipt
//! belongs to one fenced transaction. No legacy write chaining or blind retry.
mod codec;
mod plan;
mod transaction;
use super::management_reads::{ManagementRead, principal_hash};
use super::*;
use async_trait::async_trait;
use bcs_db_api::{DbTransactionParam as P, DbTransactionStep as Step, DbTransactionStepResult};
use bcs_domain::edge_permission::{EdgeStatus, RequestStatus};
use bcs_service_api::core::agent_invoke::AgentInvokePolicyCoreService;
use bcs_service_api::port::repo::agent_invoke::AgentInvokeCommandRepoPort;
use codec::*;
use plan::*;

pub struct DbAgentInvokeCommandStore {
    store: Arc<DbAgentInvokeStore>,
    policy: Arc<dyn AgentInvokePolicyCoreService>,
}
impl DbAgentInvokeCommandStore {
    pub fn new(
        store: Arc<DbAgentInvokeStore>,
        policy: Arc<dyn AgentInvokePolicyCoreService>,
    ) -> Self {
        Self { store, policy }
    }
    async fn replay(
        &self,
        c: &AgentInvokeCommand,
        m: &ManagementRead,
    ) -> AgentInvokeResult<Option<AgentInvokeReceipt>> {
        let hash = principal_hash(&c.principal);
        let rows=self.store.query(DbStatement::with_params("SELECT * FROM agent_invoke_receipts WHERE scope_id = ? AND principal_hash = ? AND idempotency_key = ? LIMIT 2",
            vec![m.pair.domain.id.into(),hash.clone().into(),DbValue::Bytes(c.idempotency_key.as_bytes().to_vec())])).await?;
        let row = match rows.as_slice() {
            [] => return Ok(None),
            [row] => row,
            _ => return Err(AgentInvokeError::InvalidState),
        };
        if string(row, "principal_hash")? != hash
            || string(row, "principal_kind")? != principal_kind(&c.principal)
            || string(row, "principal_id")? != c.principal.id()
        {
            return Err(AgentInvokeError::InvalidState);
        }
        if string(row, "command_json")? != command_json(c) {
            return Err(AgentInvokeError::Conflict);
        }
        let mut receipt = decode_receipt(&string(row, "receipt_json")?)?;
        if receipt.action_id != string(row, "action_id")? {
            return Err(AgentInvokeError::InvalidState);
        }
        receipt.replayed = true;
        receipt.current_grant = m.pair.approval.as_ref().map(grant_view).transpose()?;
        receipt.current_request = if let Some(original) = &receipt.original_request {
            let mut lookup = c.clone();
            lookup.change = AgentInvokeChange::Cancel {
                request_id: original.request_id.clone(),
                expected_version: original.version,
            };
            let rows = self
                .store
                .query(self.store.request_statement(&lookup))
                .await?;
            match rows.as_slice() {
                [] => None,
                [row] => {
                    if number(row, "metadata_scope_id")? != m.pair.domain.id {
                        return Err(AgentInvokeError::InvalidState);
                    }
                    Some(request_view(row)?)
                }
                _ => return Err(AgentInvokeError::InvalidState),
            }
        } else {
            None
        };
        Ok(Some(receipt))
    }
    async fn counts(&self, c: &AgentInvokeCommand) -> AgentInvokeResult<(DbStatement, DbRow)> {
        let s = &self.store;
        let env = s.scope.env.as_str();
        let mut params = Vec::new();
        let mut fields = Vec::new();
        for (name, table, kind, status, column, value, opposite) in [
            (
                "out_active",
                "edge_grants",
                "grant_kind",
                "approved",
                "from_id",
                c.pair.from_bot_id.as_str(),
                "to_id",
            ),
            (
                "in_active",
                "edge_grants",
                "grant_kind",
                "approved",
                "to_id",
                c.pair.to_bot_id.as_str(),
                "from_id",
            ),
            (
                "out_pending",
                "permission_requests",
                "request_kind",
                "pending",
                "from_id",
                c.pair.from_bot_id.as_str(),
                "",
            ),
            (
                "in_pending",
                "permission_requests",
                "request_kind",
                "pending",
                "to_id",
                c.pair.to_bot_id.as_str(),
                "",
            ),
        ] {
            let count = if opposite.is_empty() {
                "COUNT(*)".into()
            } else {
                format!("COUNT(DISTINCT {})", s.exact(opposite))
            };
            fields.push(format!("(SELECT {count} FROM {table} WHERE env=? AND {}=? AND {column}=? AND {}=? AND {kind}='agent_invoke' AND status='{status}') AS {name}",s.exact("env"),s.exact(column)));
            params.extend([env.into(), env.into(), value.into(), value.into()]);
        }
        fields.push(format!("(SELECT COUNT(*) FROM permission_requests WHERE env=? AND {}=? AND from_id=? AND {}=? AND to_id=? AND {}=? AND request_kind='agent_invoke' AND status='pending') AS pair_pending",s.exact("env"),s.exact("from_id"),s.exact("to_id")));
        params.extend([
            env.into(),
            env.into(),
            c.pair.from_bot_id.as_str().into(),
            c.pair.from_bot_id.as_str().into(),
            c.pair.to_bot_id.as_str().into(),
            c.pair.to_bot_id.as_str().into(),
        ]);
        let statement = DbStatement::with_params(format!("SELECT {}", fields.join(", ")), params);
        let rows = s.query(statement.clone()).await?;
        let [row] = rows.as_slice() else {
            return Err(AgentInvokeError::InvalidState);
        };
        Ok((statement, row.clone()))
    }
}
#[async_trait]
impl AgentInvokeCommandRepoPort for DbAgentInvokeCommandStore {
    async fn execute(
        &self,
        scope: &AgentInvokeScope,
        c: AgentInvokeCommand,
        snapshot: AgentInvokeManagementSnapshot,
    ) -> AgentInvokeResult<AgentInvokeReceipt> {
        self.store.require_scope(scope)?;
        self.policy.validate_command(scope, &c)?;
        if c.principal.id().chars().count() > 256 {
            return Err(AgentInvokeError::InvalidInput);
        }
        let mut m = self.store.read_management_material(&c).await?;
        self.policy.authorize_change(scope, &c, &m.snapshot)?;
        // Recovery is authorized against current roles, but precedes mutable
        // qualification, state, expected-version and stale-snapshot checks.
        if let Some(receipt) = self.replay(&c, &m).await? {
            return Ok(receipt);
        }
        if m.snapshot != snapshot {
            return Err(AgentInvokeError::Conflict);
        }
        if !m.pair.domain.state.writer_enabled || !m.pair.domain.state.runtime_enabled {
            return Err(AgentInvokeError::Unavailable);
        }
        let (count_statement, counts) = self.counts(&c).await?;
        let plan = Plan::prepare(&self.store, &c, &m, &counts)?;
        m.groups.push((count_statement, vec![counts]));
        let steps = transaction::build(&self.store, &c, &m, &plan)?;
        let results = self
            .store
            .db
            .transaction_with_outcome(steps, self.store.transaction_options())
            .await
            .map_err(write_error)?;
        let Some(DbTransactionStepResult::Rows(rows)) = results.last() else {
            return Err(AgentInvokeError::CommitUnknown);
        };
        let [row] = rows.as_slice() else {
            return Err(AgentInvokeError::CommitUnknown);
        };
        // A malformed committed receipt cannot be reported as successful.
        decode_receipt(&string(row, "receipt_json").map_err(|_| AgentInvokeError::CommitUnknown)?)
            .map_err(|_| AgentInvokeError::CommitUnknown)
    }
}
