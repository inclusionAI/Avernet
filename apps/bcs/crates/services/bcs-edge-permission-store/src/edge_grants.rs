//! Edge grant SQL persistence.
use super::*;

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

    async fn execute(&self, operation: &'static str, statement: DbStatement) -> ServiceResult<()> {
        self.execute_result(operation, statement).await.map(|_| ())
    }

    async fn execute_result(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<DbExecuteResult> {
        self.db.execute(statement).await.map_err(|err| {
            warn!(operation, error = %err, "db_edge_grant: execute failed");
            service_db_error(operation, err)
        })
    }

    async fn query(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<Vec<DbRow>> {
        self.db.query(statement).await.map_err(|err| {
            warn!(operation, error = %err, "db_edge_grant: query failed");
            service_db_error(operation, err)
        })
    }

    /// INSERT with idempotent UPSERT on the unique key
    /// `(from_id, to_id, env, grant_kind, grant_ref_id)`: SQLite
    /// `ON CONFLICT ... DO UPDATE` vs MySQL `ON DUPLICATE KEY UPDATE`.
    ///
    /// The conflicting row is refreshed in place and keeps its id: a re-add
    /// after `revoke_grant` REACTIVATES the revoked edge (unfriend →
    /// refriend → active) instead of returning the stale revoked row id
    /// while the edge stays invisible to `has_friend_edge` / `list_friends`.
    ///
    /// Since the 033/034 bot-authority migration the unique key
    /// carries `grant_kind`: owner/manager management edges use
    /// `grant_ref_id = 0` for the same (from, to) pair as runtime
    /// profile/rules edges, and owner/manager must coexist.
    fn insert_grant_sql(&self) -> String {
        let upsert = self.flavor.on_conflict_update(
            &["from_id", "to_id", "env", "grant_kind", "grant_ref_id"],
            &["rules", "status", "originator_policy_type", "originator_policy_data"],
            &[("gmt_modified", "CURRENT_TIMESTAMP")],
        );
        format!(
            "INSERT INTO edge_grants \
             (env, from_id, to_id, grant_kind, grant_ref_id, rules, \
              status, originator_policy_type, originator_policy_data) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) {upsert}"
        )
    }
}

#[async_trait]
impl EdgeGrantRepoPort for DbEdgeGrantStore {
    async fn list_active_grants(&self, from: &str, to: &str, env: &str) -> Vec<EdgeGrant> {
        let rows = self
            .query(
                "list_active_grants",
                // Runtime grants only: owner/manager are
                // metadata edges and must never surface as
                // runtime authorization (spec §3.2.5 / A16;
                // no-op until Task 2 writes such rows).
                DbStatement::with_params(
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

    async fn insert_grant(&self, grant: EdgeGrant) -> ServiceResult<u64> {
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
        } = grant;
        let rules_val = json_to_db_value(&rules);
        let policy_data_val = json_to_db_value(&originator_policy_data);
        let result = self
            .execute_result(
                "insert_grant",
                DbStatement::with_params(
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
                ),
            )
            .await?;
        if let Some(id) = result.last_insert_id {
            if id != 0 {
                return Ok(id);
            }
        }
        self.query(
            "insert_grant_lookup",
            // The check-back must be scoped by grant_kind too: with the
            // bot-authority unique key
            // (from_id, to_id, env, grant_kind, grant_ref_id), owner
            // and manager edges share `grant_ref_id = 0` with runtime
            // edges of the same (from, to) pair, so a kind-blind
            // lookup would return the wrong row's id.
            DbStatement::with_params(
                "SELECT id FROM edge_grants \
                 WHERE from_id = ? AND to_id = ? AND env = ? \
                   AND grant_kind = ? AND grant_ref_id = ? LIMIT 1",
                vec![
                    DbValue::from(from_id),
                    DbValue::from(to_id),
                    DbValue::from(env),
                    DbValue::from(grant_kind_str(grant_kind)),
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

    async fn revoke_grant(&self, edge_id: u64, env: &str) -> ServiceResult<()> {
        self.execute(
            "revoke_grant",
            DbStatement::with_params(
                "UPDATE edge_grants SET status = 'revoked', \
                     gmt_modified = CURRENT_TIMESTAMP \
                 WHERE id = ? AND env = ?",
                vec![DbValue::from(edge_id), DbValue::from(env)],
            ),
        )
        .await
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
