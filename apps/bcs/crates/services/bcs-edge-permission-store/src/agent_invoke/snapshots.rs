//! Pair-scoped consistent runtime read. Published facts, real registration,
//! approval metadata and the capability gate come from ONE SQL statement.
use super::*;
use async_trait::async_trait;
use bcs_domain::edge_permission::{EdgeGrant, EdgeStatus, GrantKind, OriginatorPolicyType};
use bcs_service_api::port::agent_invoke::AgentInvokeFactsPort;

pub(super) struct PairRead {
    pub domain: ScopeRow,
    pub source: AgentInvokeBotState,
    pub target: AgentInvokeBotState,
    pub approval: Option<AgentInvokeApproval>,
    pub row: DbRow,
}

impl DbAgentInvokeStore {
    pub(super) fn validate_pair(pair: &AgentInvokePair) -> AgentInvokeResult<()> {
        Self::validate_bot_id(&pair.from_bot_id)?;
        Self::validate_bot_id(&pair.to_bot_id)?;
        if pair.from_bot_id == pair.to_bot_id {
            return Err(AgentInvokeError::InvalidInput);
        }
        Ok(())
    }

    pub(super) fn pair_statement(&self, pair: &AgentInvokePair) -> DbStatement {
        let fact_columns = |prefix: &str, alias: &str| {
            [
                "bot_id",
                "binding_generation",
                "eligibility_generation",
                "eligible",
                "active",
                "hidden",
                "revision",
                "valid_until_ms",
                "registration_id",
                "registration_revision",
            ]
            .iter()
            .map(|c| {
                format!(
                    "{alias}.{c} AS {prefix}_{}",
                    if c.starts_with("registration_") {
                        format!("bound_{c}")
                    } else {
                        c.to_string()
                    }
                )
            })
            .collect::<Vec<_>>()
            .join(", ")
        };
        let bot_columns = |prefix: &str, alias: &str| {
            [
                "id",
                "bot_uuid",
                "actor_kind",
                "is_deleted",
                "visibility",
                "ownership_version",
                "agent_invoke_identity_revision",
            ]
            .iter()
            .map(|c| format!("{alias}.{c} AS {prefix}_registration_{c}"))
            .collect::<Vec<_>>()
            .join(", ")
        };
        let mut columns = vec![
            "s.*".to_string(),
            format!("{} AS db_now_ms", self.now_ms()),
            fact_columns("source", "sf"),
            fact_columns("target", "tf"),
            bot_columns("source", "sb"),
            bot_columns("target", "tb"),
        ];
        for c in [
            "id",
            "env",
            "from_id",
            "to_id",
            "grant_kind",
            "grant_ref_id",
            "rules",
            "status",
            "originator_policy_type",
            "originator_policy_data",
        ] {
            columns.push(format!("e.{c} AS edge_{c}"));
        }
        for c in [
            "scope_id",
            "version",
            "from_registration_id",
            "to_registration_id",
            "from_registration_revision",
            "to_registration_revision",
            "from_binding_generation",
            "to_binding_generation",
            "from_eligibility_generation",
            "to_eligibility_generation",
        ] {
            columns.push(format!("g.{c} AS approval_{c}"));
        }
        // Indexed equality narrows candidates; binary residuals preserve exact
        // identities even on retained CI/PAD SPACE registration/edge columns.
        let sql = format!(
            "SELECT {} FROM agent_invoke_scopes s \
             LEFT JOIN agent_invoke_facts sf ON sf.scope_id = s.id AND sf.bot_id = ? AND {} = ? \
             LEFT JOIN agent_invoke_facts tf ON tf.scope_id = s.id AND tf.bot_id = ? AND {} = ? \
             LEFT JOIN bcs_bots sb ON sb.env = s.env AND {} = {} AND sb.bot_uuid = ? AND {} = ? \
             LEFT JOIN bcs_bots tb ON tb.env = s.env AND {} = {} AND tb.bot_uuid = ? AND {} = ? \
             LEFT JOIN edge_grants e ON e.env = s.env AND {} = {} AND e.from_id = ? AND e.to_id = ? \
               AND {} = ? AND {} = ? AND e.grant_kind = 'agent_invoke' \
             LEFT JOIN agent_invoke_grants g ON g.edge_id = e.id \
             WHERE s.namespace = ? AND s.env = ? AND {} = ? AND {} = ? LIMIT 2",
            columns.join(", "),
            self.exact("sf.bot_id"),
            self.exact("tf.bot_id"),
            self.exact("sb.env"),
            self.exact("s.env"),
            self.exact("sb.bot_uuid"),
            self.exact("tb.env"),
            self.exact("s.env"),
            self.exact("tb.bot_uuid"),
            self.exact("e.env"),
            self.exact("s.env"),
            self.exact("e.from_id"),
            self.exact("e.to_id"),
            self.exact("s.namespace"),
            self.exact("s.env")
        );
        let from = pair.from_bot_id.as_str();
        let to = pair.to_bot_id.as_str();
        DbStatement::with_params(
            sql,
            vec![
                from.into(),
                from.into(),
                to.into(),
                to.into(),
                from.into(),
                from.into(),
                to.into(),
                to.into(),
                from.into(),
                to.into(),
                from.into(),
                to.into(),
                self.scope.namespace.as_str().into(),
                self.scope.env.as_str().into(),
                self.scope.namespace.as_str().into(),
                self.scope.env.as_str().into(),
            ],
        )
    }

    pub(super) fn decode_pair(
        &self,
        rows: &[DbRow],
        pair: &AgentInvokePair,
        runtime: bool,
    ) -> AgentInvokeResult<PairRead> {
        let domain = self.decode_scope_rows(rows)?;
        if runtime && !domain.state.runtime_enabled {
            return Err(AgentInvokeError::Unavailable);
        }
        let row = &rows[0];
        let state = |prefix: &str, expected: &str| -> AgentInvokeResult<AgentInvokeBotState> {
            let key = |column: &str| format!("{prefix}_{column}");
            if row.get(&key("bot_id")) == Some(&DbValue::Null) {
                return Err(AgentInvokeError::Unavailable);
            }
            if row.get(&key("registration_id")) == Some(&DbValue::Null) {
                return Err(AgentInvokeError::NotFound);
            }
            if string(row, &key("bot_id"))? != expected
                || string(row, &key("registration_bot_uuid"))? != expected
                || string(row, &key("registration_actor_kind"))? != "bot"
            {
                return Err(AgentInvokeError::InvalidState);
            }
            positive(row, &key("registration_id"))?;
            positive(row, &key("revision"))?;
            let valid_until = positive(row, &key("valid_until_ms"))?.get();
            if valid_until > domain.now_ms
                && valid_until - domain.now_ms > self.max_fact_ttl_ms.get()
            {
                return Err(AgentInvokeError::InvalidState);
            }
            if runtime && valid_until <= domain.now_ms {
                return Err(AgentInvokeError::Unavailable);
            }
            let registration_matches = number(row, &key("bound_registration_id"))?
                == positive(row, &key("registration_id"))?.get()
                && number(row, &key("bound_registration_revision"))?
                    == positive(row, &key("registration_agent_invoke_identity_revision"))?.get();
            if runtime && !registration_matches {
                return Err(AgentInvokeError::Unavailable);
            }
            let visibility = string(row, &key("registration_visibility"))?;
            if !["public", "protected", "hidden"].contains(&visibility.as_str()) {
                return Err(AgentInvokeError::InvalidState);
            }
            Ok(AgentInvokeBotState {
                bot_id: expected.into(),
                binding_generation: positive(row, &key("binding_generation"))?,
                eligibility_generation: positive(row, &key("eligibility_generation"))?,
                eligible: boolean(row, &key("eligible"))?,
                active: registration_matches
                    && boolean(row, &key("active"))?
                    && !boolean(row, &key("registration_is_deleted"))?
                    && valid_until > domain.now_ms,
                hidden: boolean(row, &key("hidden"))? || visibility == "hidden",
            })
        };
        let source = state("source", &pair.from_bot_id)?;
        let target = state("target", &pair.to_bot_id)?;
        let approval = if row.get("edge_id") == Some(&DbValue::Null) {
            None
        } else {
            if string(row, "edge_env")? != self.scope.env
                || string(row, "edge_from_id")? != pair.from_bot_id
                || string(row, "edge_to_id")? != pair.to_bot_id
                || string(row, "edge_grant_kind")? != "agent_invoke"
                || number(row, "edge_grant_ref_id")? != 0
                || row.get("edge_rules") != Some(&DbValue::Null)
                || string(row, "edge_originator_policy_type")? != "same_as_from"
                || row.get("edge_originator_policy_data") != Some(&DbValue::Null)
                || number(row, "approval_scope_id")? != domain.id
            {
                return Err(AgentInvokeError::InvalidState);
            }
            let status = match string(row, "edge_status")?.as_str() {
                "approved" => EdgeStatus::Approved,
                "revoked" => EdgeStatus::Revoked,
                _ => return Err(AgentInvokeError::InvalidState),
            };
            if runtime {
                for (prefix, side) in [("source", "from"), ("target", "to")] {
                    if positive(row, &format!("approval_{side}_registration_id"))?.get()
                        != positive(row, &format!("{prefix}_registration_id"))?.get()
                        || positive(row, &format!("approval_{side}_registration_revision"))?.get()
                            != positive(
                                row,
                                &format!("{prefix}_registration_agent_invoke_identity_revision"),
                            )?
                            .get()
                    {
                        return Err(AgentInvokeError::Forbidden);
                    }
                }
            }
            Some(AgentInvokeApproval {
                edge: EdgeGrant {
                    edge_id: positive(row, "edge_id")?.get(),
                    env: self.scope.env.clone(),
                    from_id: pair.from_bot_id.clone(),
                    to_id: pair.to_bot_id.clone(),
                    grant_kind: GrantKind::AgentInvoke,
                    grant_ref_id: 0,
                    rules: None,
                    status,
                    originator_policy_type: OriginatorPolicyType::SameAsFrom,
                    originator_policy_data: None,
                },
                version: positive(row, "approval_version")?,
                from_binding_generation: positive(row, "approval_from_binding_generation")?,
                to_binding_generation: positive(row, "approval_to_binding_generation")?,
                from_eligibility_generation: positive(row, "approval_from_eligibility_generation")?,
                to_eligibility_generation: positive(row, "approval_to_eligibility_generation")?,
            })
        };
        Ok(PairRead {
            domain,
            source,
            target,
            approval,
            row: row.clone(),
        })
    }
}

#[async_trait]
impl AgentInvokeFactsPort for DbAgentInvokeStore {
    async fn runtime_snapshot(
        &self,
        scope: &AgentInvokeScope,
        pair: &AgentInvokePair,
    ) -> AgentInvokeResult<AgentInvokeRuntimeSnapshot> {
        self.require_scope(scope)?;
        Self::validate_pair(pair)?;
        let rows = self.query(self.pair_statement(pair)).await?;
        let read = self.decode_pair(&rows, pair, true)?;
        Ok(AgentInvokeRuntimeSnapshot {
            scope: self.scope.clone(),
            pair: pair.clone(),
            source: read.source,
            target: read.target,
            approval: read.approval,
        })
    }

    async fn management_snapshot(
        &self,
        scope: &AgentInvokeScope,
        command: &AgentInvokeCommand,
    ) -> AgentInvokeResult<AgentInvokeManagementSnapshot> {
        self.require_scope(scope)?;
        Self::validate_pair(&command.pair)?;
        self.read_management_snapshot(command).await
    }
}
