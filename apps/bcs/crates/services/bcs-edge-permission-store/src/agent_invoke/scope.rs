use bcs_db_api::DbTransactionStep;
use super::*;

impl DbAgentInvokeStore {
    /// Trusted composition only. Unique(env) prohibits claiming a second
    /// namespace over legacy tables that do not carry a tenant/domain key.
    /// Reopening a scope never changes limits or turns on a writer/runtime.
    pub async fn initialize_scope(&self) -> AgentInvokeResult<AgentInvokeScopeState> {
        if self.scope_owner_exists().await? {
            return Ok(self.read_scope_row().await?.state);
        }
        let insert = DbStatement::with_params(
            "INSERT INTO agent_invoke_scopes (namespace, env, revision, max_out_degree, max_in_degree, max_pending_out, max_pending_in, max_fact_ttl_ms, writer_enabled, runtime_enabled) VALUES (?, ?, 1, ?, ?, ?, ?, ?, 0, 0)",
            vec![self.scope.namespace.as_str().into(), self.scope.env.as_str().into(),
                (self.limits.max_out_degree.get() as u64).into(), (self.limits.max_in_degree.get() as u64).into(),
                (self.limits.max_pending_out.get() as u64).into(), (self.limits.max_pending_in.get() as u64).into(), self.max_fact_ttl_ms.get().into()],
        );
        match self.db.execute(insert).await {
            Ok(_) => {},
            Err(error) if error.is_duplicate_key() => {}, // Re-read, never blindly upsert a conflicting domain.
            Err(_) => return Err(AgentInvokeError::Unavailable),
        }
        if !self.scope_owner_exists().await? { return Err(AgentInvokeError::Unavailable); }
        Ok(self.read_scope_row().await?.state)
    }

    async fn scope_owner_exists(&self) -> AgentInvokeResult<bool> {
        let existing = self.query(DbStatement::with_params(
            format!("SELECT namespace, env FROM agent_invoke_scopes WHERE env = ? AND {} = ?", self.exact("env")),
            vec![self.scope.env.as_str().into(), self.scope.env.as_str().into()],
        )).await?;
        match existing.as_slice() {
            [] => Ok(false),
            [row] => {
                if string(row, "namespace")? != self.scope.namespace || string(row, "env")? != self.scope.env {
                    return Err(AgentInvokeError::Conflict);
                }
                Ok(true)
            }
            _ => Err(AgentInvokeError::InvalidState),
        }
    }

    pub async fn scope_state(&self) -> AgentInvokeResult<AgentInvokeScopeState> {
        Ok(self.read_scope_row().await?.state)
    }

    /// Explicit config/capability gate, not a public management operation.
    /// Bootstrap must verify compatible readers/runtime and a fenced host fact
    /// publisher BEFORE requesting activation. No eligible facts are invented.
    pub async fn set_enabled(
        &self, expected_revision: NonZeroU64, writer: bool, runtime: bool,
    ) -> AgentInvokeResult<AgentInvokeScopeState> {
        if writer && !runtime { return Err(AgentInvokeError::InvalidInput); }
        let row = self.read_scope_row().await?;
        if row.revision != expected_revision { return Err(AgentInvokeError::Conflict); }
        let revision = advance(row.revision.get())?;
        self.db.transaction_with_outcome(vec![DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "UPDATE agent_invoke_scopes SET revision = ?, writer_enabled = ?, runtime_enabled = ? WHERE id = ? AND revision = ?",
                vec![revision.get().into(), (writer as i64).into(), (runtime as i64).into(), row.id.into(), row.revision.get().into()],
            ), expected_affected_rows: 1,
        }], self.transaction_options()).await.map_err(write_error)?;
        Ok(AgentInvokeScopeState { revision, writer_enabled: writer, runtime_enabled: runtime })
    }

    /// DB clock for trusted publisher lease construction / diagnostics.
    /// Runtime admission must use its consistent snapshot, not this scalar.
    pub async fn database_time_ms(&self) -> AgentInvokeResult<u64> {
        Ok(self.read_scope_row().await?.now_ms)
    }
}
