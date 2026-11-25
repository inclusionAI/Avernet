//! Strict management facts. These reads confer no permission to mutate: the
//! command store must lock/revalidate the returned fence in its write commit.
use super::*;
use bcs_db_api::{DbTransactionStep, DbTransactionStepResult};
use sha2::{Digest, Sha256};

impl DbAgentInvokeStore {
    pub(super) fn bot_lock_statement(&self, bot_id: &str) -> DbStatement {
        let lock = if self.flavor == DbSqlFlavor::Mysql {
            " FOR UPDATE"
        } else {
            ""
        };
        // Call once per Bot in Rust byte order. ORDER BY on a combined IN
        // query is not proof of physical InnoDB lock acquisition order.
        DbStatement::with_params(
            format!(
                "SELECT id, bot_uuid FROM bcs_bots WHERE env = ? AND {} = ? AND bot_uuid = ? AND {} = ?{lock}",
                self.exact("env"),
                self.exact("bot_uuid")
            ),
            vec![
                self.scope.env.as_str().into(),
                self.scope.env.as_str().into(),
                bot_id.into(),
                bot_id.into(),
            ],
        )
    }

    pub(super) fn request_statement(&self, command: &AgentInvokeCommand) -> DbStatement {
        let Some(id) = request_id(command) else {
            return DbStatement::new("SELECT 1 AS no_request WHERE 1 = 0");
        };
        DbStatement::with_params(
            format!(
                "SELECT p.*, m.scope_id AS metadata_scope_id, m.version AS request_version, m.creator_kind, \
             m.source_ownership_version, m.source_authority_sequence, m.edge_version, \
             m.from_binding_generation, m.to_binding_generation, m.from_eligibility_generation, m.to_eligibility_generation \
             FROM permission_requests p LEFT JOIN agent_invoke_requests m ON m.request_id = p.request_id AND {} = {} \
             WHERE p.request_id = ? AND {} = ? AND p.env = ? AND {} = ? \
             AND p.from_id = ? AND {} = ? AND p.to_id = ? AND {} = ? AND p.request_kind = 'agent_invoke' LIMIT 2",
                self.exact("m.request_id"),
                self.exact("p.request_id"),
                self.exact("p.request_id"),
                self.exact("p.env"),
                self.exact("p.from_id"),
                self.exact("p.to_id")
            ),
            vec![
                id.into(),
                id.into(),
                self.scope.env.as_str().into(),
                self.scope.env.as_str().into(),
                command.pair.from_bot_id.as_str().into(),
                command.pair.from_bot_id.as_str().into(),
                command.pair.to_bot_id.as_str().into(),
                command.pair.to_bot_id.as_str().into(),
            ],
        )
    }

    fn authority_statement(&self, command: &AgentInvokeCommand) -> DbStatement {
        let mut values = Vec::new();
        let mut select = Vec::new();
        let human_join = format!(
            "JOIN bcs_bots h ON h.bot_uuid = e.from_id AND {} = {} AND h.env = e.env AND {} = {} AND h.actor_kind = 'human' AND {} = 'human' AND h.is_deleted = 0",
            self.exact("h.bot_uuid"),
            self.exact("e.from_id"),
            self.exact("h.env"),
            self.exact("e.env"),
            self.exact("h.actor_kind")
        );
        let canonical = format!(
            "{} = 'human_' AND LENGTH(e.from_id) > 6 AND e.grant_ref_id = 0 AND e.rules IS NULL AND {} = 'same_as_from' AND e.originator_policy_data IS NULL",
            self.exact("SUBSTR(e.from_id, 1, 6)"),
            self.exact("e.originator_policy_type")
        );
        let env = self.scope.env.as_str();
        for (name, bot) in [
            ("source", command.pair.from_bot_id.as_str()),
            ("target", command.pair.to_bot_id.as_str()),
        ] {
            let filter = format!(
                "e.to_id = ? AND {} = ? AND e.env = ? AND {} = ? AND e.grant_kind = 'owner' AND e.status = 'approved'",
                self.exact("e.to_id"),
                self.exact("e.env")
            );
            for (suffix, join, extra) in [
                ("owner_count", "", "".to_owned()),
                (
                    "canonical_owner_count",
                    human_join.as_str(),
                    format!(
                        " AND {canonical} AND {} = 'owner' AND {} = 'approved'",
                        self.exact("e.grant_kind"),
                        self.exact("e.status")
                    ),
                ),
            ] {
                select.push(format!("(SELECT COUNT(*) FROM edge_grants e {join} WHERE {filter}{extra}) AS {name}_{suffix}"));
                values.extend([bot.into(), bot.into(), env.into(), env.into()]);
            }
            select.push(format!("(SELECT COALESCE(MAX(id), 0) FROM bot_authority_changes WHERE bot_id = ? AND {} = ? AND env = ? AND {} = ?) AS {name}_epoch", self.exact("bot_id"), self.exact("env")));
            values.extend([bot.into(), bot.into(), env.into(), env.into()]);
        }
        for (column, actor, bot) in [
            (
                "source_role",
                "a.operator_id",
                command.pair.from_bot_id.as_str(),
            ),
            (
                "target_role",
                "a.operator_id",
                command.pair.to_bot_id.as_str(),
            ),
            (
                "requester_role",
                "p.created_by",
                command.pair.from_bot_id.as_str(),
            ),
        ] {
            select.push(format!("(SELECT e.grant_kind FROM edge_grants e {human_join} WHERE e.from_id = {actor} AND {} = {} \
                AND e.to_id = ? AND {} = ? AND e.env = ? AND {} = ? AND e.grant_kind IN ('owner', 'manager') \
                AND e.status = 'approved' AND {canonical} ORDER BY e.grant_kind DESC LIMIT 1) AS {column}",
                self.exact("e.from_id"), self.exact(actor), self.exact("e.to_id"), self.exact("e.env")));
            values.extend([bot.into(), bot.into(), env.into(), env.into()]);
        }
        for (name, actor) in [
            ("operator_registration_id", "a.operator_id"),
            ("requester_registration_id", "p.created_by"),
        ] {
            select.push(format!("(SELECT h.id FROM bcs_bots h WHERE h.bot_uuid = {actor} AND {} = {} AND h.env = ? AND {} = ? AND h.actor_kind = 'human' AND h.is_deleted = 0 LIMIT 1) AS {name}", self.exact("h.bot_uuid"), self.exact(actor), self.exact("h.env")));
            values.extend([env.into(), env.into()]);
        }
        // An absent request joins no requester. It never borrows the deciding
        // actor's source authority. Only the separately decoded scoped request
        // can make this projection usable.
        values.extend([
            command.principal.id().into(),
            request_id(command)
                .map(DbValue::from)
                .unwrap_or(DbValue::Null),
            env.into(),
            env.into(),
        ]);
        DbStatement::with_params(
            format!(
                "SELECT {} FROM (SELECT ? AS operator_id) a \
            LEFT JOIN permission_requests p ON p.request_id = ? AND p.env = ? AND {} = ? AND p.request_kind = 'agent_invoke'",
                select.join(", "),
                self.exact("p.env")
            ),
            values,
        )
    }

    async fn delegation(
        &self,
        scope_id: u64,
        principal: &AgentInvokePrincipal,
        pair: &AgentInvokePair,
    ) -> AgentInvokeResult<(Vec<AgentInvokeAction>, Vec<DbRow>)> {
        if !matches!(principal, AgentInvokePrincipal::Governance(_)) {
            return Ok((Vec::new(), Vec::new()));
        }
        let hash = principal_hash(principal);
        let rows = self.query(DbStatement::with_params(format!(
            "SELECT * FROM agent_invoke_governance WHERE scope_id = ? AND principal_hash = ? AND from_id = ? AND to_id = ? AND {} = ? AND {} = ? LIMIT 2",
            self.exact("from_id"), self.exact("to_id")),
            vec![scope_id.into(), hash.as_str().into(), pair.from_bot_id.as_str().into(), pair.to_bot_id.as_str().into(), pair.from_bot_id.as_str().into(), pair.to_bot_id.as_str().into()])).await?;
        let actions = match rows.as_slice() {
            [] => Vec::new(),
            [row] => {
                if string(row, "principal_id")? != principal.id()
                    || string(row, "principal_hash")? != hash
                    || number(row, "scope_id")? != scope_id
                    || string(row, "from_id")? != pair.from_bot_id
                    || string(row, "to_id")? != pair.to_bot_id
                {
                    return Err(AgentInvokeError::InvalidState);
                }
                positive(row, "revision")?;
                let names: Vec<String> = serde_json::from_str(&string(row, "actions_json")?)
                    .map_err(|_| AgentInvokeError::InvalidState)?;
                let mut actions = Vec::new();
                for name in names {
                    let action = match name.as_str() {
                        "request" => AgentInvokeAction::Request,
                        "grant" => AgentInvokeAction::Grant,
                        "approve" => AgentInvokeAction::Approve,
                        "reject" => AgentInvokeAction::Reject,
                        "cancel" => AgentInvokeAction::Cancel,
                        "revoke" => AgentInvokeAction::Revoke,
                        "query" => AgentInvokeAction::Query,
                        _ => return Err(AgentInvokeError::InvalidState),
                    };
                    if actions.contains(&action) {
                        return Err(AgentInvokeError::InvalidState);
                    }
                    actions.push(action);
                }
                actions
            }
            _ => return Err(AgentInvokeError::InvalidState),
        };
        Ok((actions, rows))
    }

    pub(super) async fn read_management_snapshot(
        &self,
        command: &AgentInvokeCommand,
    ) -> AgentInvokeResult<AgentInvokeManagementSnapshot> {
        if command.authenticated_scope != self.scope {
            return Err(AgentInvokeError::NotFound);
        }
        let mut bots = [&command.pair.from_bot_id, &command.pair.to_bot_id];
        bots.sort();
        let steps = vec![
            DbTransactionStep::Query(self.scope_statement(true)),
            DbTransactionStep::Query(self.bot_lock_statement(bots[0])),
            DbTransactionStep::Query(self.bot_lock_statement(bots[1])),
            DbTransactionStep::Query(self.pair_statement(&command.pair)),
            DbTransactionStep::Query(self.request_statement(command)),
            DbTransactionStep::Query(self.authority_statement(command)),
        ];
        // Read-only transaction: a lost acknowledgement is an unavailable
        // snapshot, not an uncertain permission mutation. No external I/O is
        // performed under scope/Bot locks.
        let results = self
            .db
            .transaction_with_options(steps, self.transaction_options())
            .await
            .map_err(|_| AgentInvokeError::Unavailable)?;
        let scope = self.decode_scope_rows(tx_rows(&results, 0)?)?;
        let pair = self.decode_pair(tx_rows(&results, 3)?, &command.pair, false)?;
        if scope.id != pair.domain.id || scope.revision != pair.domain.revision {
            return Err(AgentInvokeError::Conflict);
        }
        let requests = tx_rows(&results, 4)?;
        let request_creator = match (request_id(command), requests) {
            (None, []) => None,
            (Some(_), []) => return Err(AgentInvokeError::NotFound),
            (Some(id), [row]) => {
                if string(row, "request_id")? != id
                    || string(row, "request_kind")? != "agent_invoke"
                    || number(row, "metadata_scope_id")? != scope.id
                    || string(row, "env")? != self.scope.env
                    || string(row, "from_id")? != command.pair.from_bot_id
                    || string(row, "to_id")? != command.pair.to_bot_id
                {
                    return Err(AgentInvokeError::InvalidState);
                }
                positive(row, "request_version")?;
                for key in [
                    "from_binding_generation",
                    "to_binding_generation",
                    "from_eligibility_generation",
                    "to_eligibility_generation",
                ] {
                    positive(row, key)?;
                }
                for key in [
                    "source_ownership_version",
                    "source_authority_sequence",
                    "edge_version",
                ] {
                    number(row, key)?;
                }
                let request = crate::mapping::row_to_permission_request(row)
                    .map_err(|_| AgentInvokeError::InvalidState)?;
                if request.requested_rules.is_some()
                    || !matches!(request.requested_ref_id, None | Some(0))
                    || (request.status == bcs_domain::edge_permission::RequestStatus::Approved)
                        != request.edge_id.is_some()
                {
                    return Err(AgentInvokeError::InvalidState);
                }
                let id = string(row, "created_by")?;
                if id.trim().is_empty() {
                    return Err(AgentInvokeError::InvalidState);
                }
                Some(match string(row, "creator_kind")?.as_str() {
                    "human" if id.starts_with("human_") && id.len() > 6 => {
                        AgentInvokePrincipal::Human(id)
                    }
                    "governance" => AgentInvokePrincipal::Governance(id),
                    _ => return Err(AgentInvokeError::InvalidState),
                })
            }
            _ => return Err(AgentInvokeError::InvalidState),
        };
        let [authority] = tx_rows(&results, 5)? else {
            return Err(AgentInvokeError::InvalidState);
        };
        let role = |name: &str, prefix: &str| -> AgentInvokeResult<Option<AgentInvokeRole>> {
            let initialized = number(
                &pair.row,
                &format!("{prefix}_registration_ownership_version"),
            )? > 0;
            let deleted = boolean(&pair.row, &format!("{prefix}_registration_is_deleted"))?;
            if !initialized || deleted {
                return Ok(None);
            }
            if number(authority, &format!("{prefix}_owner_count"))? != 1
                || number(authority, &format!("{prefix}_canonical_owner_count"))? != 1
            {
                return Err(AgentInvokeError::InvalidState);
            }
            match authority.get(name) {
                Some(DbValue::Null) => Ok(None),
                _ => match string(authority, name)?.as_str() {
                    "owner" => Ok(Some(AgentInvokeRole::Owner)),
                    "manager" => Ok(Some(AgentInvokeRole::Manager)),
                    _ => Err(AgentInvokeError::InvalidState),
                },
            }
        };
        number(authority, "source_epoch")?;
        number(authority, "target_epoch")?;
        let human = matches!(command.principal, AgentInvokePrincipal::Human(_));
        let source_role = role("source_role", "source")?.filter(|_| human);
        let target_role = role("target_role", "target")?.filter(|_| human);
        let requester_role = role("requester_role", "source")?;
        let (delegated_actions, operator_delegation) = self
            .delegation(scope.id, &command.principal, &command.pair)
            .await?;
        let (requester_actions, requester_delegation) = if let Some(creator) = &request_creator {
            self.delegation(scope.id, creator, &command.pair).await?
        } else {
            (Vec::new(), Vec::new())
        };
        // Governance publishers must advance the shared scope revision. A
        // stable revision proves their point reads were also valid during the
        // preceding locked pair/authority snapshot; no mixed-generation facts
        // escape if a publication raced the lock release.
        if matches!(command.principal, AgentInvokePrincipal::Governance(_))
            || matches!(request_creator, Some(AgentInvokePrincipal::Governance(_)))
        {
            let after = self.read_scope_row().await?;
            if after.id != scope.id || after.revision != scope.revision {
                return Err(AgentInvokeError::Conflict);
            }
        }
        let requester_source_authorized = match &request_creator {
            Some(AgentInvokePrincipal::Human(_)) => requester_role.is_some(),
            Some(AgentInvokePrincipal::Governance(_)) => {
                requester_actions.contains(&AgentInvokeAction::Request)
            }
            _ => false,
        };
        let fence = snapshot_fence([
            tx_rows(&results, 0)?,
            tx_rows(&results, 3)?,
            requests,
            tx_rows(&results, 5)?,
            operator_delegation.as_slice(),
            requester_delegation.as_slice(),
        ])?;
        Ok(AgentInvokeManagementSnapshot {
            scope: self.scope.clone(),
            pair: command.pair.clone(),
            principal: command.principal.clone(),
            source: pair.source,
            target: pair.target,
            source_role,
            target_role,
            delegated_actions,
            request_creator,
            requester_source_authorized,
            fence,
        })
    }
}

pub(super) fn request_id(command: &AgentInvokeCommand) -> Option<&str> {
    match &command.change {
        AgentInvokeChange::Approve { request_id, .. }
        | AgentInvokeChange::Reject { request_id, .. }
        | AgentInvokeChange::Cancel { request_id, .. } => Some(request_id),
        _ => None,
    }
}
fn tx_rows(results: &[DbTransactionStepResult], index: usize) -> AgentInvokeResult<&[DbRow]> {
    match results.get(index) {
        Some(DbTransactionStepResult::Rows(rows)) => Ok(rows),
        _ => Err(AgentInvokeError::InvalidState),
    }
}
pub(super) fn principal_hash(principal: &AgentInvokePrincipal) -> String {
    let kind = match principal {
        AgentInvokePrincipal::Human(_) => "human",
        AgentInvokePrincipal::Agent(_) => "agent",
        AgentInvokePrincipal::Governance(_) => "governance",
    };
    let digest = Sha256::digest(
        serde_json::json!([kind, principal.id()])
            .to_string()
            .as_bytes(),
    );
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}
fn snapshot_fence<'a>(groups: impl IntoIterator<Item = &'a [DbRow]>) -> AgentInvokeResult<String> {
    let mut encoded = Vec::new();
    for rows in groups {
        let mut group = Vec::new();
        for row in rows {
            let mut fields = Vec::new();
            for (key, value) in row.columns() {
                if key == "db_now_ms" {
                    continue;
                }
                let value = match value {
                    DbValue::Null => serde_json::json!(["null"]),
                    DbValue::Bool(v) => serde_json::json!(["bool", v]),
                    DbValue::I64(v) => serde_json::json!(["i64", v]),
                    DbValue::U64(v) => serde_json::json!(["u64", v]),
                    DbValue::String(v) => serde_json::json!(["string", v]),
                    DbValue::Bytes(v) => serde_json::json!(["bytes", v]),
                    DbValue::F64(_) => return Err(AgentInvokeError::InvalidState),
                };
                fields.push(serde_json::json!([key, value]));
            }
            group.push(fields);
        }
        encoded.push(group);
    }
    let digest =
        Sha256::digest(serde_json::to_vec(&encoded).map_err(|_| AgentInvokeError::InvalidState)?);
    Ok(digest.iter().map(|byte| format!("{byte:02x}")).collect())
}
