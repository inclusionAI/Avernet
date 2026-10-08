//! DB-backed `EdgeGrantRepoPort` implementation: the SQL for `edge_grants`
//! split out of the former over-limit `lib.rs` (plan Task 3 lib split).
//!
//! Role rows (`grant_kind` owner/manager) are NOT read here: they never
//! join friend semantics or runtime admission (spec §5.1/§13.4 — role
//! edges do not enter the friend list or A2A runtime grants). The runtime
//! read lane [`Self::list_active_grants`] (also serving `is_authorized`,
//! the two-path admission SoR) whitelists the runtime grant kinds in SQL
//! (`grant_kind IN ('permission_profile', 'rules')`, plan Task 12), so a
//! role edge is not selected at all — not merely warn-skipped at decode.
//! Their strict reads live in [`crate::authority`] and reject undecodable
//! shapes instead of warn-skipping rows.
//!
//! Plan Task 12 (spec §12.5): the INSERT/REVOKE write lanes carry the
//! REQUIRED [`BotOperationContext`] and commit the edge row plus its
//! `bcs_bot_action_audits` record in ONE DbPlugin transaction.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{
    DbExecuteResult, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbTransactionStep,
    DbTransactionStepResult, DbValue,
};
use bcs_domain::edge_permission::{
    EdgeGrant, EdgeStatus, GrantKind, OriginatorPolicyType,
};
pub use bcs_service_api::port::repo::EdgeGrantRepoPort;
use bcs_service_api::port::repo::edge_grant::{FriendIdsPage, FriendListQuery};
use bcs_service_api::types::{
    BotActionAuditPhase, BotActionAuditRecord, BotActionKind, BotActionResourceKind,
    BotOperationContext, stable_step_key,
};
use bcs_domain::ActorKind;
use bcs_service_api::{ServiceError, ServiceResult};
use tracing::warn;

use crate::authority::audit::{
    business_action_audit_id, business_action_audit_insert, require_business_operation,
};
use crate::common::{
    json_to_db_value, optional_string, parse_json_opt, required_string, required_u64,
    service_db_error,
};

pub type EdgeGrantSqlFlavor = DbSqlFlavor;

/// MySQL-backed edge-grant repository.
pub type MysqlEdgeGrantRepo = DbEdgeGrantStore;

/// SQLite-backed edge-grant repository.
pub type SqliteEdgeGrantRepo = DbEdgeGrantStore;

/// DB-backed `EdgeGrantRepoPort` implementation.
///
/// Holds an `Arc<dyn DbPlugin>` + flavor, like `DbRelationStore`. The store
/// owns the SQL; callers inject the concrete plugin (mysql / sqlite local).
pub struct DbEdgeGrantStore {
    pub(super) db: Arc<dyn DbPlugin>,
    flavor: EdgeGrantSqlFlavor,
}

impl DbEdgeGrantStore {
    pub fn new(db: Arc<dyn DbPlugin>, flavor: EdgeGrantSqlFlavor) -> Self {
        Self { db, flavor }
    }

    pub fn mysql(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, EdgeGrantSqlFlavor::Mysql)
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, EdgeGrantSqlFlavor::Sqlite)
    }

    pub fn flavor(&self) -> EdgeGrantSqlFlavor {
        self.flavor
    }

    pub(super) async fn execute(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<()> {
        self.execute_result(operation, statement).await.map(|_| ())
    }

    pub(super) async fn execute_result(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<DbExecuteResult> {
        self.db.execute(statement).await.map_err(|err| {
            warn!(operation, error = %err, "db_edge_grant: execute failed");
            service_db_error(operation, err)
        })
    }

    pub(super) async fn query(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<Vec<DbRow>> {
        self.db.query(statement).await.map_err(|err| {
            warn!(operation, error = %err, "db_edge_grant: query failed");
            service_db_error(operation, err)
        })
    }

    /// INSERT of one new edge row. The idempotency of a SURVIVING row under
    /// the same natural key is answered by the pre-read in [`Self::insert_grant`]
    /// (returning the existing id, no audit); losing a race fails the whole
    /// transaction fail-closed on the UNIQUE violation instead of silently
    /// writing a phantom audit for a no-change.
    fn insert_grant_sql(&self) -> &'static str {
        "INSERT INTO edge_grants \
         (env, from_id, to_id, grant_kind, grant_ref_id, rules, \
          status, originator_policy_type, originator_policy_data) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    }
}

#[async_trait]
impl EdgeGrantRepoPort for DbEdgeGrantStore {
    async fn list_active_grants(&self, from: &str, to: &str, env: &str) -> Vec<EdgeGrant> {
        let rows = self
            .query(
                "list_active_grants",
                DbStatement::with_params(
                    // Runtime whitelist (plan Task 12, spec §13.4): only the
                    // permission kinds ever join runtime admission or the A2A
                    // authz context. Role rows (owner/manager) never admit
                    // runtime friendship — the selection excludes them in SQL
                    // instead of warn-skipping at decode time.
                    "SELECT id, env, from_id, to_id, grant_kind, grant_ref_id, \
                            rules, status, originator_policy_type, originator_policy_data \
                     FROM edge_grants \
                     WHERE from_id = ? AND to_id = ? AND env = ? AND status = 'approved' \
                       AND grant_kind IN ('permission_profile', 'rules')",
                    vec![
                        DbValue::from(from),
                        DbValue::from(to),
                        DbValue::from(env),
                    ],
                ),
            )
            .await;
        match rows {
            Ok(rows) => rows
                .iter()
                .filter_map(|row| match row_to_edge_grant(row) {
                    Ok(grant) => Some(grant),
                    Err(err) => {
                        warn!(error = %err, "db_edge_grant: list_active_grants row skipped");
                        None
                    }
                })
                .collect(),
            Err(err) => {
                warn!(error = %err, "db_edge_grant: list_active_grants failed");
                Vec::new()
            }
        }
    }

    async fn is_authorized(&self, from: &str, to: &str, env: &str) -> bool {
        // Any active approved edge from→to admits (friend OR non-friend).
        // `list_active_grants` already filters `status='approved'`, so a
        // non-empty result ⇒ authorized. Keep the repo-level call (not a raw
        // SELECT 1) so this stays a pure projection over the same SoR.
        !self.list_active_grants(from, to, env).await.is_empty()
    }

    async fn has_friend_edge(&self, x: &str, y: &str, env: &str) -> bool {
        // D12: any-direction default-profile edge. x→y uses y's default (dy);
        // y→x uses x's default (dx). Either direction being a friend edge
        // counts (None default ⇒ that side is human ⇒ that direction cannot
        // be a friend edge).
        let dy = self.get_default_profile_id(y, env).await;
        let dx = self.get_default_profile_id(x, env).await;

        if let Some(dy) = dy {
            if self.has_default_edge(x, y, env, Some(dy)).await {
                return true;
            }
        }
        if let Some(dx) = dx {
            if self.has_default_edge(y, x, env, Some(dx)).await {
                return true;
            }
        }
        false
    }

    async fn list_friends(&self, actor: &str, env: &str) -> Vec<String> {
        // §4.6 two-branch index scan + (bot_id,env)→default cache +
        // memory compare. Avoids a SQL join.
        let d_actor = self.get_default_profile_id(actor, env).await;

        let mut cache: HashMap<String, Option<u64>> = HashMap::new();
        let mut friends: HashSet<String> = HashSet::new();

        // Branch ① actor initiated: actor → to_id. Keep if grant_ref_id ==
        // to_id's default profile id (Some).
        let outbound = self
            .query(
                "list_friends_outbound",
                DbStatement::with_params(
                    "SELECT to_id, grant_ref_id FROM edge_grants \
                     WHERE from_id = ? AND env = ? AND status = 'approved' \
                       AND grant_kind = 'permission_profile'",
                    vec![DbValue::from(actor), DbValue::from(env)],
                ),
            )
            .await;
        if let Ok(rows) = outbound {
            for row in rows {
                let to_id = match required_string(&row, "to_id") {
                    Ok(v) => v,
                    Err(err) => {
                        warn!(error = %err, "list_friends_outbound: missing to_id");
                        continue;
                    }
                };
                let grant_ref_id = match required_u64(&row, "grant_ref_id") {
                    Ok(v) => v,
                    Err(err) => {
                        warn!(error = %err, "list_friends_outbound: missing grant_ref_id");
                        continue;
                    }
                };
                let d_y = match cache.get(&to_id) {
                    Some(v) => *v,
                    None => {
                        let v = self.get_default_profile_id(&to_id, env).await;
                        cache.insert(to_id.clone(), v);
                        v
                    }
                };
                // Outbound friend edge actor→to_id is keyed on to_id's
                // default profile id (dy), per §4.6 ①.
                if let Some(d_y) = d_y {
                    if grant_ref_id == d_y {
                        friends.insert(to_id);
                    }
                }
            }
        }

        // Branch ② others initiated: from_id → actor. Only if actor has a
        // default profile (is a bot). Keep if grant_ref_id == actor's default.
        if let Some(d_actor) = d_actor {
            let inbound = self
                .query(
                    "list_friends_inbound",
                    DbStatement::with_params(
                        "SELECT from_id, grant_ref_id FROM edge_grants \
                         WHERE to_id = ? AND env = ? AND status = 'approved' \
                           AND grant_kind = 'permission_profile'",
                        vec![DbValue::from(actor), DbValue::from(env)],
                    ),
                )
                .await;
            if let Ok(rows) = inbound {
                for row in rows {
                    let from_id = match required_string(&row, "from_id") {
                        Ok(v) => v,
                        Err(err) => {
                            warn!(error = %err, "list_friends_inbound: missing from_id");
                            continue;
                        }
                    };
                    let grant_ref_id = match required_u64(&row, "grant_ref_id") {
                        Ok(v) => v,
                        Err(err) => {
                            warn!(error = %err, "list_friends_inbound: missing grant_ref_id");
                            continue;
                        }
                    };
                    if grant_ref_id == d_actor {
                        friends.insert(from_id);
                    }
                }
            }
        }

        let mut out: Vec<String> = friends.into_iter().collect();
        out.sort();
        out
    }

    async fn list_friends_paginated(
        &self,
        actor: &str,
        env: &str,
        query: FriendListQuery,
    ) -> ServiceResult<FriendIdsPage> {
        let statement = friend_list_statement(self.flavor, actor, env, query)?;
        let rows = self.query("list_friends_paginated", statement).await?;
        let first = rows.first().ok_or_else(|| ServiceError::InternalError(
            "friend list query did not return its count".into(),
        ))?;
        let total = required_u64(first, "total")?;
        let mut items = Vec::new();
        for row in &rows {
            // LEFT JOIN yields a single NULL peer when the requested page is empty.
            if let Some(id) = optional_string(row, "peer_id")? {
                items.push(id);
            }
        }
        Ok(FriendIdsPage { items, total })
    }

    async fn insert_grant(
        &self,
        grant: EdgeGrant,
        operation: &BotOperationContext,
    ) -> ServiceResult<u64> {
        require_business_operation(operation)?;
        let EdgeGrant {
            edge_id: _,
            env,
            from_id,
            to_id,
            grant_kind,
            grant_ref_id,
            rules,
            status,
            originator_policy_type,
            originator_policy_data,
            // Source columns start with the Task 2 authority
            // migration; currently all writers produce non-role
            // (`none/none`) edges.
            management_source_kind: _,
            management_source_id: _,
        } = grant;
        // Idempotent pre-check: a surviving row under the same natural key
        // returns its id with NO new audit row (spec §12.5: 幂等无变化不造
        // applied 事件). A concurrent racer that wins between this read and
        // the write transaction below makes the UNIQUE violation fail the
        // whole transaction fail-closed — never a phantom audit for a
        // no-change.
        let existing = self
            .query(
                "insert_grant_lookup",
                DbStatement::with_params(
                    "SELECT id FROM edge_grants WHERE from_id = ? AND to_id = ? AND env = ? AND grant_ref_id = ? LIMIT 1",
                    vec![
                        DbValue::from(from_id.clone()),
                        DbValue::from(to_id.clone()),
                        DbValue::from(env.clone()),
                        DbValue::from(grant_ref_id),
                    ],
                ),
            )
            .await?;
        if let Some(id) = existing
            .into_iter()
            .next()
            .and_then(|row| row.get_i64("id").ok().flatten())
            .and_then(|id| if id < 0 { None } else { Some(id as u64) })
        {
            return Ok(id);
        }
        let rules_val = json_to_db_value(&rules);
        let policy_data_val = json_to_db_value(&originator_policy_data);
        // Plan Task 12 (spec §12.5): the edge INSERT and its ordinary
        // business audit row commit in ONE DbPlugin transaction — an audit
        // failure rolls the business edge back with it. The audit names the
        // logical grant identity (from->to) because the auto-increment id is
        // only known after the insert.
        let audit_resource = format!("{from_id}->{to_id}");
        let audit = edge_audit_record(operation, &env, &audit_resource, BotActionKind::Invite);
        let results = self
            .db
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    self.insert_grant_sql(),
                    vec![
                        DbValue::from(env.clone()),
                        DbValue::from(from_id.clone()),
                        DbValue::from(to_id.clone()),
                        DbValue::from(grant_kind_str(grant_kind)),
                        DbValue::from(grant_ref_id),
                        rules_val,
                        DbValue::from(edge_status_str(status)),
                        DbValue::from(originator_policy_type_str(originator_policy_type)),
                        policy_data_val,
                    ],
                )),
                DbTransactionStep::Execute(business_action_audit_insert(&audit)),
            ])
            .await
            .map_err(|err| {
                warn!(operation = "insert_grant", error = %err, "db_edge_grant: transaction failed");
                service_db_error("insert_grant", err)
            })?;
        let exec = match &results[0] {
            DbTransactionStepResult::Executed(exec) => exec,
            _ => {
                return Err(ServiceError::InternalError(
                    "edge_grants insert did not return an execute result".to_string(),
                ))
            }
        };
        if let Some(id) = exec.last_insert_id {
            if id != 0 {
                return Ok(id);
            }
        }
        // Backends without a usable last_insert_id: resolve the id of the row
        // this transaction just made durable.
        self.query(
            "insert_grant_lookup",
            DbStatement::with_params(
                "SELECT id FROM edge_grants WHERE from_id = ? AND to_id = ? AND env = ? AND grant_ref_id = ? LIMIT 1",
                vec![
                    DbValue::from(from_id),
                    DbValue::from(to_id),
                    DbValue::from(env),
                    DbValue::from(grant_ref_id),
                ],
            ),
        )
        .await?
        .into_iter()
        .next()
        .and_then(|row| row.get_i64("id").ok().flatten())
        .and_then(|id| if id < 0 { None } else { Some(id as u64) })
        .ok_or_else(|| ServiceError::InternalError("edge_grants insert did not return an id".to_string()))
    }

    async fn revoke_grant(
        &self,
        edge_id: u64,
        env: &str,
        operation: &BotOperationContext,
    ) -> ServiceResult<()> {
        require_business_operation(operation)?;
        // Idempotent pre-check: revoking a non-active/missing edge is a
        // no-change — no UPDATE, no audit row (spec §12.5 幂等无变化不造
        // applied 事件).
        let active = self
            .query(
                "revoke_grant_precheck",
                DbStatement::with_params(
                    "SELECT 1 AS one FROM edge_grants \
                     WHERE id = ? AND env = ? AND status = 'approved' LIMIT 1",
                    vec![DbValue::from(edge_id), DbValue::from(env)],
                ),
            )
            .await?;
        if active.is_empty() {
            return Ok(());
        }
        let audit = edge_audit_record(operation, env, &edge_id.to_string(), BotActionKind::Delete);
        self.db
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    "UPDATE edge_grants SET status = 'revoked', \
                         gmt_modified = CURRENT_TIMESTAMP \
                     WHERE id = ? AND env = ? AND status = 'approved'",
                    vec![DbValue::from(edge_id), DbValue::from(env)],
                )),
                DbTransactionStep::Execute(business_action_audit_insert(&audit)),
            ])
            .await
            .map_err(|err| {
                warn!(operation = "revoke_grant", error = %err, "db_edge_grant: transaction failed");
                service_db_error("revoke_grant", err)
            })
            .map(|_| ())
    }

    async fn get_default_profile_id(&self, bot_id: &str, env: &str) -> Option<u64> {
        let rows = self
            .query(
                "get_default_profile_id",
                DbStatement::with_params(
                    "SELECT id FROM permission_profiles \
                     WHERE bot_id = ? AND env = ? AND is_default = 1 \
                       AND status = 'active' LIMIT 1",
                    vec![DbValue::from(bot_id), DbValue::from(env)],
                ),
            )
            .await;
        match rows {
            Ok(rows) => rows.into_iter().next().and_then(|row| {
                row.get_i64("id").ok().flatten().and_then(|value| if value < 0 { None } else { Some(value as u64) })
            }),
            Err(err) => {
                warn!(error = %err, "db_edge_grant: get_default_profile_id failed");
                None
            }
        }
    }
}

impl DbEdgeGrantStore {
    /// Helper for `has_friend_edge`: does an approved default-profile edge
    /// `from → to` exist with `grant_ref_id` equal to `default_ref`?
    async fn has_default_edge(
        &self,
        from: &str,
        to: &str,
        env: &str,
        default_ref: Option<u64>,
    ) -> bool {
        let Some(default_ref) = default_ref else {
            return false;
        };
        let rows = self
            .query(
                "has_default_edge",
                DbStatement::with_params(
                    "SELECT 1 AS hit FROM edge_grants \
                     WHERE from_id = ? AND to_id = ? AND env = ? \
                       AND status = 'approved' \
                       AND grant_kind = 'permission_profile' \
                       AND grant_ref_id = ? LIMIT 1",
                    vec![
                        DbValue::from(from),
                        DbValue::from(to),
                        DbValue::from(env),
                        DbValue::from(default_ref),
                    ],
                ),
            )
            .await;
        match rows {
            Ok(rows) => !rows.is_empty(),
            Err(err) => {
                warn!(error = %err, "db_edge_grant: has_default_edge failed");
                false
            }
        }
    }
}

fn edge_audit_record(
    operation: &BotOperationContext,
    env: &str,
    resource: &str,
    action: BotActionKind,
) -> BotActionAuditRecord {
    let step_key = stable_step_key(
        action,
        BotActionResourceKind::Friend,
        BotActionAuditPhase::Applied,
    );
    BotActionAuditRecord {
        audit_id: business_action_audit_id(env, &operation.operation_id, &step_key),
        env: env.to_string(),
        operation_id: operation.operation_id.clone(),
        step_key,
        operator: operation.actor.clone(),
        resource_kind: BotActionResourceKind::Friend,
        resource_id: resource.to_string(),
        action,
        phase: BotActionAuditPhase::Applied,
        reason_code: None,
    }
}

fn row_to_edge_grant(row: &DbRow) -> ServiceResult<EdgeGrant> {
    Ok(EdgeGrant {
        edge_id: required_u64(row, "id")?,
        env: required_string(row, "env")?,
        from_id: required_string(row, "from_id")?,
        to_id: required_string(row, "to_id")?,
        grant_kind: parse_grant_kind(&required_string(row, "grant_kind")?)?,
        grant_ref_id: required_u64(row, "grant_ref_id")?,
        rules: parse_json_opt(&optional_string(row, "rules")?)?,
        status: parse_edge_status(&required_string(row, "status")?)?,
        originator_policy_type: parse_originator_policy_type(
            &required_string(row, "originator_policy_type")?,
        )?,
        originator_policy_data: parse_json_opt(&optional_string(row, "originator_policy_data")?)?,
        // Until the Task 2 authority migration adds the source
        // columns, every persisted edge is a non-role edge: fixed
        // `none/none` encoding (spec §5.1). Role kinds are
        // rejected by `parse_grant_kind` until role sources
        // carry their dedicated columns, so a missing source can
        // never silently decode into a valid role edge.
        management_source_kind: bcs_domain::NON_ROLE_SOURCE_KIND.to_string(),
        management_source_id: bcs_domain::NON_ROLE_SOURCE_ID.to_string(),
    })
}

fn parse_grant_kind(value: &str) -> ServiceResult<GrantKind> {
    match value {
        "permission_profile" => Ok(GrantKind::PermissionProfile),
        "rules" => Ok(GrantKind::Rules),
        // Role kinds stay rejected in the FRIEND paths: a runtime read must
        // never turn an owner/manager row into a friend or admission grant.
        // The strict authority reads (crate::authority) decode them through
        // the dedicated codec instead of this projection.
        other => Err(ServiceError::InternalError(format!(
            "unknown grant_kind: {}",
            other
        ))),
    }
}

fn parse_edge_status(value: &str) -> ServiceResult<EdgeStatus> {
    match value {
        "approved" => Ok(EdgeStatus::Approved),
        "revoked" => Ok(EdgeStatus::Revoked),
        other => Err(ServiceError::InternalError(format!(
            "unknown edge status: {}",
            other
        ))),
    }
}

fn parse_originator_policy_type(value: &str) -> ServiceResult<OriginatorPolicyType> {
    match value {
        "any" => Ok(OriginatorPolicyType::Any),
        "same_as_from" => Ok(OriginatorPolicyType::SameAsFrom),
        "specific" => Ok(OriginatorPolicyType::Specific),
        "owner" => Ok(OriginatorPolicyType::Owner),
        other => Err(ServiceError::InternalError(format!(
            "unknown originator_policy_type: {}",
            other
        ))),
    }
}

fn grant_kind_str(kind: GrantKind) -> &'static str {
    match kind {
        GrantKind::PermissionProfile => "permission_profile",
        GrantKind::Rules => "rules",
        GrantKind::Owner => "owner",
        GrantKind::Manager => "manager",
    }
}

fn edge_status_str(status: EdgeStatus) -> &'static str {
    match status {
        EdgeStatus::Approved => "approved",
        EdgeStatus::Revoked => "revoked",
    }
}

fn originator_policy_type_str(policy: OriginatorPolicyType) -> &'static str {
    match policy {
        OriginatorPolicyType::Any => "any",
        OriginatorPolicyType::SameAsFrom => "same_as_from",
        OriginatorPolicyType::Specific => "specific",
        OriginatorPolicyType::Owner => "owner",
    }
}

/// A single read returns count + page from the same statement snapshot, even
/// beyond the last page. Only the requested IDs cross the DB plugin boundary.
fn friend_list_statement(
    flavor: EdgeGrantSqlFlavor,
    actor: &str,
    env: &str,
    query: FriendListQuery,
) -> ServiceResult<DbStatement> {
    if query.limit == 0 || query.limit > 100 || query.offset > i64::MAX as u64 {
        return Err(ServiceError::InvalidOperation {
            message: "invalid friend pagination bounds".into(),
            request_id: None,
        });
    }
    // Binary comparison preserves Rust String ordering and HashSet identity,
    // independent of MySQL's default case-insensitive database collation.
    let (outbound_id, inbound_id, prefix) = match flavor {
        EdgeGrantSqlFlavor::Mysql => (
            "CAST(g.to_id AS BINARY)", "CAST(g.from_id AS BINARY)",
            "SUBSTR(peer_id, 1, 6) = CAST('human_' AS BINARY)",
        ),
        EdgeGrantSqlFlavor::Sqlite => (
            "g.to_id COLLATE BINARY", "g.from_id COLLATE BINARY",
            "SUBSTR(peer_id, 1, 6) COLLATE BINARY = 'human_'",
        ),
    };
    let filter = match query.target_type {
        None => "1 = 1".to_string(),
        Some(ActorKind::Human) => prefix.to_string(),
        Some(ActorKind::Bot) => format!("NOT ({prefix})"),
    };
    let sql = format!(
        "WITH friend_ids AS (
           SELECT {outbound_id} AS peer_id FROM edge_grants g
           JOIN permission_profiles p ON p.id = g.grant_ref_id
             AND p.bot_id = g.to_id AND p.env = g.env
             AND p.is_default = 1 AND p.status = 'active'
           WHERE g.from_id = ? AND g.env = ? AND g.status = 'approved'
             AND g.grant_kind = 'permission_profile'
           UNION
           SELECT {inbound_id} AS peer_id FROM edge_grants g
           JOIN permission_profiles p ON p.id = g.grant_ref_id
             AND p.bot_id = g.to_id AND p.env = g.env
             AND p.is_default = 1 AND p.status = 'active'
           WHERE g.to_id = ? AND g.env = ? AND g.status = 'approved'
             AND g.grant_kind = 'permission_profile'
         ), filtered AS (SELECT peer_id FROM friend_ids WHERE {filter})
         SELECT totals.total, page.peer_id
         FROM (SELECT COUNT(*) AS total FROM filtered) totals
         LEFT JOIN (SELECT peer_id FROM filtered ORDER BY peer_id LIMIT ? OFFSET ?) page
           ON 1 = 1 ORDER BY page.peer_id"
    );
    Ok(DbStatement::with_params(sql, vec![
        DbValue::from(actor), DbValue::from(env),
        DbValue::from(actor), DbValue::from(env),
        DbValue::from(u64::from(query.limit)), DbValue::from(query.offset),
    ]))
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use bcs_db_local::LocalSqliteDbPlugin;
    use bcs_db_api::{
        DbExecuteResult, DbHealth, DbResult, DbTransactionStep, DbTransactionStepResult,
    };
    use bcs_domain::edge_permission::{
        EdgeGrant, EdgeStatus, GrantKind, OriginatorPolicyType,
    };

    use super::*;
    use crate::DbPermissionProfileStore;
    use bcs_service_api::port::repo::PermissionProfileRepoPort;

    fn test_operation(label: &str) -> BotOperationContext {
        BotOperationContext {
            operation_id: format!("test-op-{label}"),
            actor: bcs_service_api::types::BotOperationActor::Human {
                user_id: "85020".to_string(),
                effective_actor_id: "human_85020".to_string(),
            },
        }
    }

    async fn sqlite_store() -> DbEdgeGrantStore {
        let db = LocalSqliteDbPlugin::new().expect("local sqlite");
        // edge_grants + permission_profiles schema (mirrors
        // migrations/mysql/014_edge_permission.sql for SQLite).
        db.execute(DbStatement::new(
            "CREATE TABLE edge_grants (\
                id INTEGER PRIMARY KEY AUTOINCREMENT, \
                env VARCHAR(32) NOT NULL, \
                from_id VARCHAR(128) NOT NULL, \
                to_id VARCHAR(128) NOT NULL, \
                grant_kind VARCHAR(32) NOT NULL, \
                grant_ref_id INTEGER NOT NULL, \
                rules TEXT, \
                status VARCHAR(16) NOT NULL DEFAULT 'approved', \
                originator_policy_type VARCHAR(32) NOT NULL DEFAULT 'any', \
                originator_policy_data TEXT, \
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                UNIQUE (from_id, to_id, env, grant_ref_id))",
        ))
        .await
        .expect("create edge_grants");
        // Plan Task 12: insert/revoke commit one bcs_bot_action_audits row
        // in the same transaction (mirrors migration-032).
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_bot_action_audits (\
                id INTEGER PRIMARY KEY AUTOINCREMENT, \
                audit_id VARCHAR(128) NOT NULL, \
                env VARCHAR(32) NOT NULL, \
                operation_id VARCHAR(64) NOT NULL, \
                step_key VARCHAR(128) NOT NULL, \
                operator_kind VARCHAR(16) NOT NULL, \
                operator_id VARCHAR(256) NOT NULL, \
                operator_user_id VARCHAR(256), \
                effective_actor_id VARCHAR(256) NOT NULL, \
                resource_kind VARCHAR(32) NOT NULL, \
                resource_id VARCHAR(256) NOT NULL, \
                action VARCHAR(32) NOT NULL, \
                phase VARCHAR(16) NOT NULL, \
                reason_code VARCHAR(64), \
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        ))
        .await
        .expect("create bcs_bot_action_audits");
        db.execute(DbStatement::new(
            "CREATE TABLE permission_profiles (\
                id INTEGER PRIMARY KEY AUTOINCREMENT, \
                bot_id VARCHAR(128) NOT NULL, \
                env VARCHAR(32) NOT NULL, \
                name VARCHAR(128) NOT NULL DEFAULT 'default', \
                description VARCHAR(512), \
                rules_template TEXT NOT NULL, \
                revision INTEGER NOT NULL DEFAULT 1, \
                digest VARCHAR(128) NOT NULL, \
                is_default INTEGER NOT NULL DEFAULT 0, \
                status VARCHAR(16) NOT NULL DEFAULT 'active', \
                created_by VARCHAR(128) NOT NULL, \
                updated_by VARCHAR(128), \
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                UNIQUE (bot_id, env, is_default, status))",
        ))
        .await
        .expect("create permission_profiles");
        DbEdgeGrantStore::sqlite(Arc::new(db))
    }

    async fn seed_default(store: &DbEdgeGrantStore, bot_id: &str, env: &str) -> u64 {
        let profile_store = DbPermissionProfileStore::sqlite(store.db.clone());
        profile_store
            .ensure_default_profile(bot_id, env)
            .await
            .expect("seed profile")
    }

    fn default_grant(from: &str, to: &str, env: &str, ref_id: u64) -> EdgeGrant {
        EdgeGrant {
            edge_id: 0,
            env: env.to_string(),
            from_id: from.to_string(),
            to_id: to.to_string(),
            grant_kind: GrantKind::PermissionProfile,
            grant_ref_id: ref_id,
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
            management_source_kind: "none".into(),
            management_source_id: "none".into(),
        }
    }

    #[tokio::test]
    async fn get_default_profile_id_roundtrip() {
        let store = sqlite_store().await;
        let profile_id = seed_default(&store, "bot_a", "dev").await;
        assert_eq!(store.get_default_profile_id("bot_a", "dev").await, Some(profile_id));
        assert_eq!(store.get_default_profile_id("human_x", "dev").await, None);
    }

    #[tokio::test]
    async fn insert_and_list_active_grants() {
        let store = sqlite_store().await;
        let ref_id = seed_default(&store, "b", "dev").await;
        let g = default_grant("a", "b", "dev", ref_id);
        let edge_id = store.insert_grant(g.clone(), &test_operation("one")).await.expect("insert");
        let listed = store.list_active_grants("a", "b", "dev").await;
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0].edge_id, edge_id);
        assert_eq!(listed[0].from_id, "a");
        assert_eq!(listed[0].grant_kind, GrantKind::PermissionProfile);
    }

    #[tokio::test]
    async fn insert_idempotent_on_unique_key() {
        let store = sqlite_store().await;
        let ref_id = seed_default(&store, "b", "dev").await;
        let mut g = default_grant("a", "b", "dev", ref_id);
        let edge_id = store
            .insert_grant(g.clone(), &test_operation("idem-1"))
            .await
            .expect("insert 1");
        // Re-insert with same (from,to,env,ref) but different edge_id: DO NOTHING.
        g.edge_id = 9999;
        let dup_id = store
            .insert_grant(g, &test_operation("idem-2"))
            .await
            .expect("insert 2");
        let listed = store.list_active_grants("a", "b", "dev").await;
        assert_eq!(listed.len(), 1);
        // The original auto-generated edge_id survives.
        assert_eq!(listed[0].edge_id, edge_id);
        assert_eq!(dup_id, edge_id);
    }

    #[tokio::test]
    async fn revoke_removes_from_active() {
        let store = sqlite_store().await;
        let ref_id = seed_default(&store, "b", "dev").await;
        let g = default_grant("a", "b", "dev", ref_id);
        let edge_id = store.insert_grant(g.clone(), &test_operation("rev")).await.expect("insert");
        store.revoke_grant(edge_id, "dev", &test_operation("rev-i")).await.expect("revoke");
        let listed = store.list_active_grants("a", "b", "dev").await;
        assert!(listed.is_empty());
    }

    #[tokio::test]
    async fn has_friend_edge_any_direction() {
        let store = sqlite_store().await;
        let ref_id = seed_default(&store, "bot_b", "dev").await;
        // a (human) → b : friend edge (ref = b's default).
        let g = default_grant("human_a", "bot_b", "dev", ref_id);
        store
            .insert_grant(g, &test_operation("friend-edge"))
            .await
            .expect("insert");
        assert!(store.has_friend_edge("human_a", "bot_b", "dev").await);
        assert!(store.has_friend_edge("bot_b", "human_a", "dev").await);
    }

    #[tokio::test]
    async fn list_friends_outbound_human_actor() {
        let store = sqlite_store().await;
        let ref_b = seed_default(&store, "bot_b", "dev").await;
        let ref_c = seed_default(&store, "bot_c", "dev").await;
        store
            .insert_grant(default_grant("human_a", "bot_b", "dev", ref_b), &test_operation("b"))
            .await
            .expect("insert b");
        store
            .insert_grant(default_grant("human_a", "bot_c", "dev", ref_c), &test_operation("c"))
            .await
            .expect("insert c");
        // non-friend (wrong ref) should not be listed
        store
            .insert_grant(default_grant("human_a", "bot_c", "dev", ref_b), &test_operation("wrong-ref"))
            .await
            .expect("insert wrong ref (different ref)");

        let mut friends = store.list_friends("human_a", "dev").await;
        friends.sort();
        assert_eq!(friends, vec!["bot_b".to_string(), "bot_c".to_string()]);
    }

    #[tokio::test]
    async fn friend_page_conformance_filters_deduplicates_and_counts_before_paging() {
        let store = sqlite_store().await;
        let bot_default = seed_default(&store, "bot-main", "dev").await;
        // Deliberately unsorted. Near-prefix IDs must remain Bots, not Humans.
        for peer in ["human_2002", "humanX1001", "bot-z", "Human_1001", "human_1001", "bot-A", "bot-a"] {
            store.insert_grant(default_grant(peer, "bot-main", "dev", bot_default), &test_operation("peer")).await.unwrap();
        }
        let peer_default = seed_default(&store, "bot-z", "dev").await;
        store.insert_grant(default_grant("bot-main", "bot-z", "dev", peer_default), &test_operation("bot-z")).await.unwrap();
        assert!(store.list_active_grants("bot-main", "human_1001", "dev").await.is_empty());

        let repo: &dyn EdgeGrantRepoPort = &store;
        let query = |target_type, offset, limit| FriendListQuery { target_type, offset, limit };
        let first = repo.list_friends_paginated("bot-main", "dev", query(Some(ActorKind::Human), 0, 1)).await.unwrap();
        assert_eq!(first, FriendIdsPage { items: vec!["human_1001".into()], total: 2 });
        let second = repo.list_friends_paginated("bot-main", "dev", query(Some(ActorKind::Human), 1, 1)).await.unwrap();
        assert_eq!(second, FriendIdsPage { items: vec!["human_2002".into()], total: 2 });
        let bots = repo.list_friends_paginated("bot-main", "dev", query(Some(ActorKind::Bot), 0, 100)).await.unwrap();
        assert_eq!(bots.total, 5);
        assert_eq!(bots.items, vec!["Human_1001", "bot-A", "bot-a", "bot-z", "humanX1001"]);
        let all = repo.list_friends_paginated("bot-main", "dev", query(None, 0, 100)).await.unwrap();
        assert_eq!(all.total, 7); // bot-z has two grants but is one friend.
        let mut assembled = Vec::new();
        for offset in [0, 2, 4, 6] {
            let page = repo.list_friends_paginated("bot-main", "dev", query(None, offset, 2)).await.unwrap();
            assert_eq!(page.total, all.total);
            assembled.extend(page.items);
        }
        assert_eq!(assembled, all.items);
        for offset in [7, u64::from(u32::MAX) * 100] {
            let empty = repo.list_friends_paginated("bot-main", "dev", query(None, offset, 20)).await.unwrap();
            assert_eq!(empty.total, 7);
            assert!(empty.items.is_empty());
        }
        let human = repo.list_friends_paginated("human_1001", "dev", query(Some(ActorKind::Bot), 0, 20)).await.unwrap();
        assert_eq!(human, FriendIdsPage { items: vec!["bot-main".into()], total: 1 });
        let empty = repo.list_friends_paginated("human_1001", "dev", query(Some(ActorKind::Human), 0, 20)).await.unwrap();
        assert_eq!(empty, FriendIdsPage { items: vec![], total: 0 });
        let unknown = repo.list_friends_paginated("unknown", "dev", query(None, 0, 20)).await.unwrap();
        assert_eq!(unknown, FriendIdsPage { items: vec![], total: 0 });
    }

    #[tokio::test]
    async fn friend_page_excludes_non_friend_edges_and_other_environments() {
        let store = sqlite_store().await;
        let active = seed_default(&store, "bot-main", "dev").await;
        let other = seed_default(&store, "other", "dev").await;
        let prod = seed_default(&store, "bot-main", "prod").await;
        for (peer, env, profile) in [
            ("human_valid", "dev", active),
            ("human_wrong_owner", "dev", other),
            ("human_wrong_profile_env", "dev", prod),
            ("human_other_env", "prod", prod),
        ] {
            store.insert_grant(default_grant(peer, "bot-main", env, profile), &test_operation("exo")).await.unwrap();
        }
        let mut revoked = default_grant("human_revoked", "bot-main", "dev", active);
        revoked.status = EdgeStatus::Revoked;
        store.insert_grant(revoked, &test_operation("revoked")).await.unwrap();
        let mut rules = default_grant("human_rules", "bot-main", "dev", active);
        rules.grant_kind = GrantKind::Rules;
        store.insert_grant(rules, &test_operation("rules")).await.unwrap();
        // Outbound edges must also match an active DEFAULT profile.
        for (bot, assignment) in [("inactive-bot", "status = 'inactive'"), ("custom-bot", "is_default = 0")] {
            let profile = seed_default(&store, bot, "dev").await;
            store.insert_grant(default_grant("bot-main", bot, "dev", profile), &test_operation("custom")).await.unwrap();
            store.db.execute(DbStatement::with_params(
                format!("UPDATE permission_profiles SET {assignment} WHERE id = ?"),
                vec![DbValue::from(profile)],
            )).await.unwrap();
        }
        let query = FriendListQuery { target_type: None, offset: 0, limit: 20 };
        assert_eq!(store.list_friends_paginated("bot-main", "dev", query).await.unwrap(),
            FriendIdsPage { items: vec!["human_valid".into()], total: 1 });
        store.db.execute(DbStatement::with_params(
            "UPDATE permission_profiles SET status = 'inactive' WHERE id = ?", vec![DbValue::from(active)],
        )).await.unwrap();
        assert_eq!(store.list_friends_paginated("bot-main", "dev", query).await.unwrap(),
            FriendIdsPage { items: vec![], total: 0 });
    }

    #[tokio::test]
    async fn friend_page_rejects_invalid_bounds_and_propagates_database_errors() {
        let store = sqlite_store().await;
        for (offset, limit) in [(0, 0), (0, 101), (u64::MAX, 20)] {
            assert!(matches!(store.list_friends_paginated("bot", "dev", FriendListQuery {
                target_type: None, offset, limit,
            }).await, Err(ServiceError::InvalidOperation { .. })));
        }
        store.db.execute(DbStatement::new("DROP TABLE permission_profiles")).await.unwrap();
        assert!(matches!(store.list_friends_paginated("bot", "dev", FriendListQuery {
            target_type: None, offset: 0, limit: 20,
        }).await, Err(ServiceError::InternalError(_))));
    }

    #[test]
    fn friend_page_mysql_uses_binary_identity_and_bound_pagination() {
        let statement = friend_list_statement(EdgeGrantSqlFlavor::Mysql, "bot'quote", "dev", FriendListQuery {
            target_type: Some(ActorKind::Human), offset: 40, limit: 20,
        }).unwrap();
        assert!(statement.sql().contains("CAST(g.to_id AS BINARY)"));
        assert!(statement.sql().contains("CAST(g.from_id AS BINARY)"));
        assert!(statement.sql().contains("SUBSTR(peer_id, 1, 6) = CAST('human_' AS BINARY)"));
        assert!(statement.sql().contains("LIMIT ? OFFSET ?"));
        assert!(!statement.sql().contains("bot'quote"));
        assert_eq!(statement.params(), vec![
            DbValue::from("bot'quote"), DbValue::from("dev"),
            DbValue::from("bot'quote"), DbValue::from("dev"),
            DbValue::from(20_u64), DbValue::from(40_u64),
        ]);
    }

    struct RecordingFriendDb {
        inner: Arc<dyn DbPlugin>,
        reads: std::sync::Mutex<Vec<(DbStatement, usize)>>,
    }

    #[async_trait]
    impl DbPlugin for RecordingFriendDb {
        async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
            let rows = self.inner.query(statement.clone()).await?;
            self.reads.lock().unwrap().push((statement, rows.len()));
            Ok(rows)
        }
        async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
            self.inner.execute(statement).await
        }
        async fn transaction(&self, steps: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> {
            self.inner.transaction(steps).await
        }
        async fn health_check(&self) -> DbResult<DbHealth> {
            self.inner.health_check().await
        }
    }

    #[tokio::test]
    async fn friend_page_reads_only_one_bounded_result_from_database() {
        let seed = sqlite_store().await;
        let profile = seed_default(&seed, "bot-main", "dev").await;
        for i in 0..31 {
            seed.insert_grant(default_grant(&format!("human_{i:03}"), "bot-main", "dev", profile), &test_operation("bulk")).await.unwrap();
        }
        let db = Arc::new(RecordingFriendDb { inner: seed.db, reads: std::sync::Mutex::new(Vec::new()) });
        let store = DbEdgeGrantStore::sqlite(db.clone());
        let page = store.list_friends_paginated("bot-main", "dev", FriendListQuery {
            target_type: Some(ActorKind::Human), offset: 10, limit: 5,
        }).await.unwrap();
        assert_eq!(page.total, 31);
        assert_eq!(page.items, vec!["human_010", "human_011", "human_012", "human_013", "human_014"]);
        let reads = db.reads.lock().unwrap();
        assert_eq!(reads.len(), 1, "no full-list or N+1 default-profile queries");
        assert_eq!(reads[0].1, 5, "only this page crossed the DB boundary");
        assert!(reads[0].0.sql().contains("LIMIT ? OFFSET ?"));
    }
}