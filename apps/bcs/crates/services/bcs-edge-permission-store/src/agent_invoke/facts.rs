use async_trait::async_trait;
use bcs_db_api::DbTransactionStep;
use bcs_service_api::port::repo::agent_invoke::AgentInvokeFactRepoPort;
use super::*;

fn decode_fact(row: &DbRow) -> AgentInvokeResult<AgentInvokePublishedFact> {
    Ok(AgentInvokePublishedFact {
        state: AgentInvokeBotState {
            bot_id: string(row, "bot_id")?, binding_generation: positive(row, "binding_generation")?,
            eligibility_generation: positive(row, "eligibility_generation")?,
            eligible: boolean(row, "eligible")?, active: boolean(row, "active")?, hidden: boolean(row, "hidden")?,
        },
        revision: positive(row, "revision")?, valid_until_ms: positive(row, "valid_until_ms")?.get(),
    })
}

fn fact_json(fact: &AgentInvokePublishedFact) -> String {
    serde_json::json!({
        "bot_id": fact.state.bot_id, "binding_generation": fact.state.binding_generation.get(),
        "eligibility_generation": fact.state.eligibility_generation.get(), "eligible": fact.state.eligible,
        "active": fact.state.active, "hidden": fact.state.hidden, "revision": fact.revision.get(),
        "valid_until_ms": fact.valid_until_ms,
    }).to_string()
}

#[async_trait]
impl AgentInvokeFactRepoPort for DbAgentInvokeStore {
    async fn read_fact(&self, scope: &AgentInvokeScope, bot_id: &str) -> AgentInvokeResult<Option<AgentInvokePublishedFact>> {
        self.require_scope(scope)?; Self::validate_bot_id(bot_id)?;
        let domain = self.read_scope_row().await?;
        self.read_fact_in_scope(domain.id, bot_id).await
    }

    async fn publish_fact(&self, scope: &AgentInvokeScope, publication: AgentInvokeFactPublication) -> AgentInvokeResult<AgentInvokePublishedFact> {
        self.require_scope(scope)?; Self::validate_bot_id(&publication.state.bot_id)?;
        let domain = self.read_scope_row().await?;
        if publication.valid_until_ms > i64::MAX as u64 || publication.valid_until_ms <= domain.now_ms
            || publication.valid_until_ms - domain.now_ms > self.max_fact_ttl_ms.get()
            || publication.state.binding_generation.get() > i64::MAX as u64
            || publication.state.eligibility_generation.get() > i64::MAX as u64
        { return Err(AgentInvokeError::InvalidInput); }
        let previous = self.read_fact_in_scope(domain.id, &publication.state.bot_id).await?;
        if previous.as_ref().map_or(0, |f| f.revision.get()) != publication.expected_revision {
            return Err(AgentInvokeError::Conflict);
        }
        if let Some(previous) = &previous {
            let old = &previous.state; let new = &publication.state;
            let qualification_changed = (old.eligible, old.active, old.hidden) != (new.eligible, new.active, new.hidden);
            if new.binding_generation < old.binding_generation || new.eligibility_generation < old.eligibility_generation
                || ((qualification_changed || previous.valid_until_ms <= domain.now_ms)
                    && new.eligibility_generation <= old.eligibility_generation)
            { return Err(AgentInvokeError::Conflict); }
        }
        let published = AgentInvokePublishedFact {
            state: publication.state, revision: advance(publication.expected_revision)?, valid_until_ms: publication.valid_until_ms,
        };
        let scope_revision = advance(domain.revision.get())?;
        let now = self.now_ms();
        let mut params = vec![
            published.revision.get().into(), published.state.binding_generation.get().into(),
            published.state.eligibility_generation.get().into(), (published.state.eligible as i64).into(),
            (published.state.active as i64).into(), (published.state.hidden as i64).into(), published.valid_until_ms.into(),
        ];
        let statement = if previous.is_some() {
            params.extend([domain.id.into(), published.state.bot_id.as_str().into(), published.state.bot_id.as_str().into(), publication.expected_revision.into(),
                published.state.eligibility_generation.get().into(), published.valid_until_ms.into(), published.valid_until_ms.into(), self.max_fact_ttl_ms.get().into()]);
            DbStatement::with_params(format!(
                "UPDATE agent_invoke_facts SET revision = ?, binding_generation = ?, eligibility_generation = ?, eligible = ?, active = ?, hidden = ?, valid_until_ms = ? \
                 WHERE scope_id = ? AND bot_id = ? AND {} = ? AND revision = ? \
                 AND (valid_until_ms > {now} OR eligibility_generation < ?) AND ? > {now} AND ? - {now} <= ?", self.exact("bot_id")), params)
        } else {
            let mut insert = vec![domain.id.into(), published.state.bot_id.as_str().into()]; insert.extend(params);
            insert.extend([published.valid_until_ms.into(), published.valid_until_ms.into(), self.max_fact_ttl_ms.get().into()]);
            DbStatement::with_params(format!(
                "INSERT INTO agent_invoke_facts (scope_id, bot_id, revision, binding_generation, eligibility_generation, eligible, active, hidden, valid_until_ms) \
                 SELECT ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE ? > {now} AND ? - {now} <= ?"), insert)
        };
        let action_id = format!("fact-{}", scope_revision.get());
        let audit = DbStatement::with_params(format!(
            "INSERT INTO agent_invoke_audit (scope_id, action_id, action, principal_kind, principal_id, from_id, to_id, reason, before_json, after_json, created_at_ms) \
             VALUES (?, ?, 'qualification_publish', 'inventory', 'trusted-fact-publisher', ?, ?, 'trusted fact publication', ?, ?, {now})"),
            vec![domain.id.into(), action_id.into(), published.state.bot_id.as_str().into(), published.state.bot_id.as_str().into(),
                previous.as_ref().map(fact_json).unwrap_or_else(|| "null".into()).into(), fact_json(&published).into()],
        );
        let steps = vec![
            DbTransactionStep::ExecuteChecked {
                statement: DbStatement::with_params("UPDATE agent_invoke_scopes SET revision = ? WHERE id = ? AND revision = ?",
                    vec![scope_revision.get().into(), domain.id.into(), domain.revision.get().into()]), expected_affected_rows: 1,
            },
            DbTransactionStep::ExecuteChecked { statement, expected_affected_rows: 1 },
            DbTransactionStep::ExecuteChecked { statement: audit, expected_affected_rows: 1 },
        ];
        self.db.transaction_with_options(steps, self.transaction_options()).await.map_err(write_error)?;
        Ok(published)
    }
}

impl DbAgentInvokeStore {
    async fn read_fact_in_scope(&self, scope_id: u64, bot_id: &str) -> AgentInvokeResult<Option<AgentInvokePublishedFact>> {
        let sql = format!("SELECT * FROM agent_invoke_facts WHERE scope_id = ? AND bot_id = ? AND {} = ?", self.exact("bot_id"));
        let rows = self.query(DbStatement::with_params(sql, vec![scope_id.into(), bot_id.into(), bot_id.into()])).await?;
        match rows.as_slice() {
            [] => Ok(None),
            [row] => {
                let fact = decode_fact(row)?;
                if fact.state.bot_id != bot_id { return Err(AgentInvokeError::InvalidState); }
                Ok(Some(fact))
            }
            _ => Err(AgentInvokeError::InvalidState),
        }
    }
}
