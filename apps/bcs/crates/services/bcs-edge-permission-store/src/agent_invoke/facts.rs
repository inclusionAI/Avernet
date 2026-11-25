use super::*;
use async_trait::async_trait;
use bcs_db_api::DbTransactionStep;
use bcs_service_api::port::repo::agent_invoke::AgentInvokeFactRepoPort;

fn decode_fact(row: &DbRow) -> AgentInvokeResult<AgentInvokePublishedFact> {
    let registration_id = number(row, "registration_id")?;
    let registration_revision = number(row, "registration_revision")?;
    if (registration_id == 0) != (registration_revision == 0) {
        return Err(AgentInvokeError::InvalidState);
    }
    Ok(AgentInvokePublishedFact {
        state: AgentInvokeBotState {
            bot_id: string(row, "bot_id")?,
            binding_generation: positive(row, "binding_generation")?,
            eligibility_generation: positive(row, "eligibility_generation")?,
            eligible: boolean(row, "eligible")?,
            active: boolean(row, "active")?,
            hidden: boolean(row, "hidden")?,
        },
        revision: positive(row, "revision")?,
        valid_until_ms: positive(row, "valid_until_ms")?.get(),
    })
}

fn fact_json(fact: &AgentInvokePublishedFact, registration: (u64, u64)) -> String {
    serde_json::json!({
        "bot_id": fact.state.bot_id, "binding_generation": fact.state.binding_generation.get(),
        "eligibility_generation": fact.state.eligibility_generation.get(), "eligible": fact.state.eligible,
        "active": fact.state.active, "hidden": fact.state.hidden, "revision": fact.revision.get(),
        "valid_until_ms": fact.valid_until_ms, "registration_id":registration.0, "registration_revision":registration.1,
    }).to_string()
}

#[async_trait]
impl AgentInvokeFactRepoPort for DbAgentInvokeStore {
    async fn read_fact(
        &self,
        scope: &AgentInvokeScope,
        bot_id: &str,
    ) -> AgentInvokeResult<Option<AgentInvokePublishedFact>> {
        self.require_scope(scope)?;
        Self::validate_bot_id(bot_id)?;
        let domain = self.read_scope_row().await?;
        self.read_fact_in_scope(domain.id, bot_id).await
    }

    async fn publish_fact(
        &self,
        scope: &AgentInvokeScope,
        publication: AgentInvokeFactPublication,
    ) -> AgentInvokeResult<AgentInvokePublishedFact> {
        self.require_scope(scope)?;
        Self::validate_bot_id(&publication.state.bot_id)?;
        let domain = self.read_scope_row().await?;
        if publication.valid_until_ms > i64::MAX as u64
            || publication.valid_until_ms <= domain.now_ms
            || publication.valid_until_ms - domain.now_ms > self.max_fact_ttl_ms.get()
            || publication.state.binding_generation.get() > i64::MAX as u64
            || publication.state.eligibility_generation.get() > i64::MAX as u64
        {
            return Err(AgentInvokeError::InvalidInput);
        }
        let rows=self.query(DbStatement::with_params(format!("SELECT f.*, b.id AS live_registration_id, b.agent_invoke_identity_revision AS live_registration_revision, b.actor_kind AS live_actor_kind FROM agent_invoke_scopes d LEFT JOIN agent_invoke_facts f ON f.scope_id=d.id AND f.bot_id=? AND {}=? LEFT JOIN bcs_bots b ON b.bot_uuid=? AND {}=? AND b.env=d.env AND {}={} WHERE d.id=? LIMIT 2",self.exact("f.bot_id"),self.exact("b.bot_uuid"),self.exact("b.env"),self.exact("d.env")),vec![publication.state.bot_id.as_str().into(),publication.state.bot_id.as_str().into(),publication.state.bot_id.as_str().into(),publication.state.bot_id.as_str().into(),domain.id.into()])).await?;
        let [raw] = rows.as_slice() else {
            return Err(AgentInvokeError::InvalidState);
        };
        let previous = if raw.get("bot_id") == Some(&DbValue::Null) {
            None
        } else {
            Some(decode_fact(raw)?)
        };
        let registration = if raw.get("live_registration_id") == Some(&DbValue::Null) {
            (0, 0)
        } else {
            (
                positive(raw, "live_registration_id")?.get(),
                positive(raw, "live_registration_revision")?.get(),
            )
        };
        if registration.0 > 0 && string(raw, "live_actor_kind")? != "bot" {
            return Err(AgentInvokeError::InvalidState);
        }
        if let Some(old) = &previous {
            let previous_registration = (
                number(raw, "registration_id")?,
                number(raw, "registration_revision")?,
            );
            if previous_registration.0 != registration.0
                && publication.state.binding_generation <= old.state.binding_generation
            {
                return Err(AgentInvokeError::Conflict);
            }
            if previous_registration.0 == registration.0
                && previous_registration.1 != registration.1
                && publication.state.binding_generation <= old.state.binding_generation
                && publication.state.eligibility_generation <= old.state.eligibility_generation
            {
                return Err(AgentInvokeError::Conflict);
            }
        }

        if previous.as_ref().map_or(0, |f| f.revision.get()) != publication.expected_revision {
            return Err(AgentInvokeError::Conflict);
        }
        if let Some(previous) = &previous {
            let old = &previous.state;
            let new = &publication.state;
            let qualification_changed =
                (old.eligible, old.active, old.hidden) != (new.eligible, new.active, new.hidden);
            if new.binding_generation < old.binding_generation
                || new.eligibility_generation < old.eligibility_generation
                || ((qualification_changed || previous.valid_until_ms <= domain.now_ms)
                    && new.eligibility_generation <= old.eligibility_generation)
            {
                return Err(AgentInvokeError::Conflict);
            }
        }
        let published = AgentInvokePublishedFact {
            state: publication.state,
            revision: advance(publication.expected_revision)?,
            valid_until_ms: publication.valid_until_ms,
        };
        let scope_revision = advance(domain.revision.get())?;
        let now = self.now_ms();
        let mut params = vec![
            registration.0.into(),
            registration.1.into(),
            published.revision.get().into(),
            published.state.binding_generation.get().into(),
            published.state.eligibility_generation.get().into(),
            (published.state.eligible as i64).into(),
            (published.state.active as i64).into(),
            (published.state.hidden as i64).into(),
            published.valid_until_ms.into(),
        ];
        let statement = if previous.is_some() {
            params.extend([
                domain.id.into(),
                published.state.bot_id.as_str().into(),
                published.state.bot_id.as_str().into(),
                publication.expected_revision.into(),
                published.state.eligibility_generation.get().into(),
                published.valid_until_ms.into(),
                published.valid_until_ms.into(),
                self.max_fact_ttl_ms.get().into(),
            ]);
            DbStatement::with_params(
                format!(
                    "UPDATE agent_invoke_facts SET registration_id = ?, registration_revision = ?, revision = ?, binding_generation = ?, eligibility_generation = ?, eligible = ?, active = ?, hidden = ?, valid_until_ms = ? \
                 WHERE scope_id = ? AND bot_id = ? AND {} = ? AND revision = ? \
                 AND (valid_until_ms > {now} OR eligibility_generation < ?) AND ? > {now} AND ? - {now} <= ?",
                    self.exact("bot_id")
                ),
                params,
            )
        } else {
            let mut insert = vec![domain.id.into(), published.state.bot_id.as_str().into()];
            insert.extend(params);
            insert.extend([
                published.valid_until_ms.into(),
                published.valid_until_ms.into(),
                self.max_fact_ttl_ms.get().into(),
            ]);
            DbStatement::with_params(
                format!(
                    "INSERT INTO agent_invoke_facts (scope_id, bot_id, registration_id, registration_revision, revision, binding_generation, eligibility_generation, eligible, active, hidden, valid_until_ms) \
                 SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE ? > {now} AND ? - {now} <= ?"
                ),
                insert,
            )
        };
        let mut statement_params = statement.params().to_vec();
        let registration_filter = format!(
            "bot_uuid=? AND {}=? AND env=? AND {}=?",
            self.exact("bot_uuid"),
            self.exact("env")
        );
        statement_params.extend([
            published.state.bot_id.as_str().into(),
            published.state.bot_id.as_str().into(),
            self.scope.env.as_str().into(),
            self.scope.env.as_str().into(),
        ]);
        let registration_guard = if registration.0 == 0 {
            format!("NOT EXISTS (SELECT 1 FROM bcs_bots WHERE {registration_filter})")
        } else {
            statement_params.extend([registration.0.into(), registration.1.into()]);
            format!(
                "EXISTS (SELECT 1 FROM bcs_bots WHERE {registration_filter} AND id=? AND agent_invoke_identity_revision=? AND actor_kind='bot')"
            )
        };
        let statement = DbStatement::with_params(
            format!("{} AND {registration_guard}", statement.sql()),
            statement_params,
        );
        let action_id = format!("fact-{}", scope_revision.get());
        let audit = DbStatement::with_params(
            format!(
                "INSERT INTO agent_invoke_audit (scope_id, action_id, action, principal_kind, principal_id, from_id, to_id, reason, before_json, after_json, created_at_ms) \
             VALUES (?, ?, 'qualification_publish', 'inventory', 'trusted-fact-publisher', ?, ?, 'trusted fact publication', ?, ?, {now})"
            ),
            vec![
                domain.id.into(),
                action_id.into(),
                published.state.bot_id.as_str().into(),
                published.state.bot_id.as_str().into(),
                match &previous {
                    Some(old) => fact_json(
                        old,
                        (
                            number(raw, "registration_id")?,
                            number(raw, "registration_revision")?,
                        ),
                    ),
                    None => "null".into(),
                }
                .into(),
                fact_json(&published, registration).into(),
            ],
        );
        let mut steps = vec![
            DbTransactionStep::ExecuteChecked {
                statement: DbStatement::with_params(
                    "UPDATE agent_invoke_scopes SET revision = ? WHERE id = ? AND revision = ?",
                    vec![
                        scope_revision.get().into(),
                        domain.id.into(),
                        domain.revision.get().into(),
                    ],
                ),
                expected_affected_rows: 1,
            },
            DbTransactionStep::Query(self.bot_lock_statement(&published.state.bot_id)),
            DbTransactionStep::ExecuteChecked {
                statement,
                expected_affected_rows: 1,
            },
        ];
        steps.extend(self.invalidation_steps(
            domain.id,
            &published,
            registration,
            scope_revision.get(),
            steps.len(),
        )?);
        steps.push(DbTransactionStep::ExecuteChecked {
            statement: audit,
            expected_affected_rows: 1,
        });
        self.db
            .transaction_with_options(steps, self.transaction_options())
            .await
            .map_err(write_error)?;
        Ok(published)
    }
}

impl DbAgentInvokeStore {
    async fn read_fact_in_scope(
        &self,
        scope_id: u64,
        bot_id: &str,
    ) -> AgentInvokeResult<Option<AgentInvokePublishedFact>> {
        let sql = format!(
            "SELECT * FROM agent_invoke_facts WHERE scope_id = ? AND bot_id = ? AND {} = ?",
            self.exact("bot_id")
        );
        let rows = self
            .query(DbStatement::with_params(
                sql,
                vec![scope_id.into(), bot_id.into(), bot_id.into()],
            ))
            .await?;
        match rows.as_slice() {
            [] => Ok(None),
            [row] => {
                let fact = decode_fact(row)?;
                if fact.state.bot_id != bot_id {
                    return Err(AgentInvokeError::InvalidState);
                }
                Ok(Some(fact))
            }
            _ => Err(AgentInvokeError::InvalidState),
        }
    }
}
