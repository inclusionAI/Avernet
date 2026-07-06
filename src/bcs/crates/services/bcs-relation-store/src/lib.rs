//! Database-backed implementation of relation repository ports.
//!
//! This store owns the relation SQL and depends only on the driver-level
//! `bcs-db-api` contract. The composition root decides which concrete DB
//! plugin backs it.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{DbError, DbExecuteResult, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue};
pub use bcs_service_api::port::repo::RelationRepoPort;
use bcs_service_api::{EnsureOwnerEdgesResult, RelationEdge, ServiceError, ServiceResult};
use tracing::warn;

pub type RelationSqlFlavor = DbSqlFlavor;

pub mod memory;

pub use memory::MemoryRelationRepo;

/// MySQL-backed relation repository.
pub type MysqlRelationRepo = DbRelationStore;

/// SQLite-backed relation repository.
pub type SqliteRelationRepo = DbRelationStore;

pub struct DbRelationStore {
    db: Arc<dyn DbPlugin>,
    flavor: RelationSqlFlavor,
}

impl DbRelationStore {
    pub fn new(db: Arc<dyn DbPlugin>, flavor: RelationSqlFlavor) -> Self {
        Self { db, flavor }
    }

    pub fn mysql(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, RelationSqlFlavor::Mysql)
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, RelationSqlFlavor::Sqlite)
    }

    pub fn flavor(&self) -> RelationSqlFlavor {
        self.flavor
    }

    async fn exec_upsert(
        &self,
        from_id: &str,
        to_id: &str,
        env: &str,
        is_creator: bool,
    ) -> ServiceResult<()> {
        let is_creator_val: i64 = if is_creator { 1 } else { 0 };
        self.execute(
            "upsert",
            DbStatement::with_params(
                self.upsert_sql(),
                vec![
                    DbValue::from(from_id),
                    DbValue::from(to_id),
                    DbValue::from(env),
                    DbValue::from(is_creator_val),
                ],
            ),
        )
        .await
    }

    async fn exec_upsert_result(
        &self,
        from_id: &str,
        to_id: &str,
        env: &str,
        is_creator: bool,
    ) -> ServiceResult<DbExecuteResult> {
        let is_creator_val: i64 = if is_creator { 1 } else { 0 };
        self.execute_result(
            "upsert",
            DbStatement::with_params(
                self.upsert_sql(),
                vec![
                    DbValue::from(from_id),
                    DbValue::from(to_id),
                    DbValue::from(env),
                    DbValue::from(is_creator_val),
                ],
            ),
        )
        .await
    }

    async fn exec_insert_friend(&self, from_id: &str, to_id: &str, env: &str) -> ServiceResult<()> {
        self.execute(
            "insert_friend",
            DbStatement::with_params(
                self.insert_friend_sql(),
                vec![
                    DbValue::from(from_id),
                    DbValue::from(to_id),
                    DbValue::from(env),
                ],
            ),
        )
        .await
    }

    async fn exec_insert_friend_result(
        &self,
        from_id: &str,
        to_id: &str,
        env: &str,
    ) -> ServiceResult<DbExecuteResult> {
        self.execute_result(
            "insert_friend",
            DbStatement::with_params(
                self.insert_friend_sql(),
                vec![
                    DbValue::from(from_id),
                    DbValue::from(to_id),
                    DbValue::from(env),
                ],
            ),
        )
        .await
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
            warn!(operation, error = %err, "db_relation: execute failed");
            service_db_error(operation, err)
        })
    }

    fn upsert_sql(&self) -> &'static str {
        match self.flavor {
            RelationSqlFlavor::Mysql => {
                "INSERT INTO bcs_actor_relations \
                 (from_id, to_id, env, kinds, allow, deny, is_creator) \
                 VALUES (?, ?, ?, 0, 0, 0, ?) \
                 ON DUPLICATE KEY UPDATE \
                     kinds = VALUES(kinds), \
                     allow = VALUES(allow), \
                     deny = VALUES(deny), \
                     is_creator = GREATEST(is_creator, VALUES(is_creator))"
            }
            RelationSqlFlavor::Sqlite => {
                "INSERT INTO bcs_actor_relations \
                 (from_id, to_id, env, kinds, allow, deny, is_creator) \
                 VALUES (?, ?, ?, 0, 0, 0, ?) \
                 ON CONFLICT(from_id, to_id, env) DO UPDATE SET \
                     kinds = excluded.kinds, \
                     allow = excluded.allow, \
                     deny = excluded.deny, \
                     is_creator = MAX(bcs_actor_relations.is_creator, excluded.is_creator)"
            }
        }
    }

    fn insert_friend_sql(&self) -> &'static str {
        match self.flavor {
            RelationSqlFlavor::Mysql => {
                "INSERT IGNORE INTO bcs_actor_relations \
                 (from_id, to_id, env, kinds, allow, deny, is_creator) \
                 VALUES (?, ?, ?, 0, 0, 0, 0)"
            }
            RelationSqlFlavor::Sqlite => {
                "INSERT OR IGNORE INTO bcs_actor_relations \
                 (from_id, to_id, env, kinds, allow, deny, is_creator) \
                 VALUES (?, ?, ?, 0, 0, 0, 0)"
            }
        }
    }
}

#[async_trait]
impl RelationRepoPort for DbRelationStore {
    async fn upsert_edge(&self, edge: RelationEdge) -> ServiceResult<()> {
        self.exec_upsert(&edge.from_id, &edge.to_id, &edge.env, edge.is_creator)
            .await
    }

    async fn delete_edge(&self, from_id: &str, to_id: &str, env: &str) -> ServiceResult<()> {
        self.execute(
            "delete_edge",
            DbStatement::with_params(
                "DELETE FROM bcs_actor_relations WHERE from_id = ? AND to_id = ? AND env = ?",
                vec![
                    DbValue::from(from_id),
                    DbValue::from(to_id),
                    DbValue::from(env),
                ],
            ),
        )
        .await
    }

    async fn get_edge(
        &self,
        from_id: &str,
        to_id: &str,
        env: &str,
    ) -> ServiceResult<Option<RelationEdge>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT from_id, to_id, env, kinds, allow, deny, is_creator \
                 FROM bcs_actor_relations \
                 WHERE from_id = ? AND to_id = ? AND env = ? LIMIT 1",
                vec![
                    DbValue::from(from_id),
                    DbValue::from(to_id),
                    DbValue::from(env),
                ],
            ))
            .await
            .map_err(|err| {
                warn!(
                    from_id = %from_id,
                    to_id = %to_id,
                    env = %env,
                    error = %err,
                    "db_relation: get_edge query failed"
                );
                service_db_error("get_edge", err)
            })?;

        rows.into_iter().next().map(row_to_edge).transpose()
    }

    async fn ensure_owner_edges(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<()> {
        self.exec_upsert(human_id, bot_id, env, true).await?;
        self.exec_insert_friend(bot_id, human_id, env).await?;
        Ok(())
    }

    async fn ensure_owner_edges_counted(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<EnsureOwnerEdgesResult> {
        match self.flavor {
            RelationSqlFlavor::Mysql => {
                let mut result = EnsureOwnerEdgesResult::default();

                let forward = self.exec_upsert_result(human_id, bot_id, env, true).await?;
                count_mysql_owner_upsert(&mut result, forward.affected_rows);

                let reverse = self
                    .exec_insert_friend_result(bot_id, human_id, env)
                    .await?;
                if reverse.affected_rows > 0 {
                    result.created += 1;
                }

                return Ok(result);
            }
            RelationSqlFlavor::Sqlite => {}
        }

        // SQLite cannot distinguish insert/update/no-op for this UPSERT shape
        // using affected_rows alone, so the local implementation keeps the
        // read-before-write approximation. It is suitable for single-box local
        // tests, while MySQL/ZDAS keeps the production atomic affected-row
        // semantics above.
        let forward_before = self.get_edge(human_id, bot_id, env).await?;
        let reverse_before = self.get_edge(bot_id, human_id, env).await?;

        self.exec_upsert(human_id, bot_id, env, true).await?;
        self.exec_insert_friend(bot_id, human_id, env).await?;

        let mut result = EnsureOwnerEdgesResult::default();
        match forward_before {
            None => result.created += 1,
            Some(edge) if !edge.is_creator => result.upgraded += 1,
            Some(_) => {}
        }
        if reverse_before.is_none() {
            result.created += 1;
        }
        Ok(result)
    }

    async fn add_friend_edges(&self, a: &str, b: &str, env: &str) -> ServiceResult<()> {
        self.exec_insert_friend(a, b, env).await?;
        self.exec_insert_friend(b, a, env).await?;
        Ok(())
    }

    async fn remove_friend_edges(&self, a: &str, b: &str, env: &str) -> ServiceResult<()> {
        self.execute(
            "remove_friend_edges",
            DbStatement::with_params(
                "DELETE FROM bcs_actor_relations \
                 WHERE env = ? AND is_creator = 0 \
                   AND ((from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?))",
                vec![
                    DbValue::from(env),
                    DbValue::from(a),
                    DbValue::from(b),
                    DbValue::from(b),
                    DbValue::from(a),
                ],
            ),
        )
        .await
    }

    async fn remove_all_friend_edges(&self, actor_id: &str, env: &str) -> ServiceResult<()> {
        self.execute(
            "remove_all_friend_edges",
            DbStatement::with_params(
                "DELETE FROM bcs_actor_relations \
                 WHERE env = ? AND is_creator = 0 \
                   AND (from_id = ? OR to_id = ?)",
                vec![
                    DbValue::from(env),
                    DbValue::from(actor_id),
                    DbValue::from(actor_id),
                ],
            ),
        )
        .await
    }

    async fn add_relation_edge(&self, caller: &str, target: &str, env: &str) -> ServiceResult<()> {
        self.exec_insert_friend(caller, target, env).await
    }

    async fn list_friends_via_relation(
        &self,
        actor_id: &str,
        env: &str,
    ) -> ServiceResult<Vec<String>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT a.to_id AS peer \
                 FROM bcs_actor_relations a \
                 JOIN bcs_actor_relations b \
                   ON b.from_id = a.to_id \
                  AND b.to_id = a.from_id \
                  AND b.env = a.env \
                  AND b.is_creator = 0 \
                 WHERE a.from_id = ? AND a.env = ? AND a.is_creator = 0",
                vec![DbValue::from(actor_id), DbValue::from(env)],
            ))
            .await
            .map_err(|err| service_db_error("list_friends_via_relation", err))?;

        let mut out = Vec::with_capacity(rows.len());
        for row in rows {
            if let Some(peer) = row
                .get_string("peer")
                .map_err(|err| service_db_error("list_friends_via_relation.peer", err))?
            {
                out.push(peer);
            }
        }
        Ok(out)
    }
}

fn count_mysql_owner_upsert(result: &mut EnsureOwnerEdgesResult, affected_rows: u64) {
    match affected_rows {
        1 => result.created += 1,
        2 => result.upgraded += 1,
        _ => {}
    }
}

fn row_to_edge(row: DbRow) -> ServiceResult<RelationEdge> {
    Ok(RelationEdge {
        from_id: required_string(&row, "from_id")?,
        to_id: required_string(&row, "to_id")?,
        env: required_string(&row, "env")?,
        kinds: optional_u64(&row, "kinds")?,
        allow: optional_u64(&row, "allow")?,
        deny: optional_u64(&row, "deny")?,
        is_creator: row
            .get_bool("is_creator")
            .map_err(|err| service_db_error("row.is_creator", err))?
            .unwrap_or(false),
    })
}

fn required_string(row: &DbRow, column: &'static str) -> ServiceResult<String> {
    row.get_string(column)
        .map_err(|err| service_db_error(column, err))?
        .ok_or_else(|| ServiceError::InternalError(format!("missing relation column {}", column)))
}

fn optional_u64(row: &DbRow, column: &'static str) -> ServiceResult<u64> {
    Ok(row
        .get_i64(column)
        .map_err(|err| service_db_error(column, err))?
        .unwrap_or(0)
        .max(0) as u64)
}

fn service_db_error(operation: &'static str, err: DbError) -> ServiceError {
    ServiceError::InternalError(format!("relation db {}: {}", operation, err))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;
    use std::sync::Mutex;

    use bcs_db_api::{DbHealth, DbResult, DbTransactionStep, DbTransactionStepResult};
    use bcs_db_local::LocalSqliteDbPlugin;

    struct ScriptedDbPlugin {
        execute_results: Mutex<VecDeque<DbExecuteResult>>,
    }

    impl ScriptedDbPlugin {
        fn with_affected_rows(rows: impl IntoIterator<Item = u64>) -> Self {
            let execute_results = rows
                .into_iter()
                .map(|affected_rows| DbExecuteResult {
                    affected_rows,
                    last_insert_id: None,
                })
                .collect();
            Self {
                execute_results: Mutex::new(execute_results),
            }
        }
    }

    #[async_trait]
    impl DbPlugin for ScriptedDbPlugin {
        async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
            Err(DbError::Backend(
                "mysql counted path must not query before write".to_string(),
            ))
        }

        async fn execute(&self, _statement: DbStatement) -> DbResult<DbExecuteResult> {
            self.execute_results
                .lock()
                .map_err(|_| DbError::Backend("script lock poisoned".to_string()))?
                .pop_front()
                .ok_or_else(|| DbError::Backend("missing scripted execute result".to_string()))
        }

        async fn transaction(
            &self,
            _steps: Vec<DbTransactionStep>,
        ) -> DbResult<Vec<DbTransactionStepResult>> {
            Err(DbError::Unsupported(
                "scripted db plugin does not support transactions".to_string(),
            ))
        }

        async fn health_check(&self) -> DbResult<DbHealth> {
            Ok(DbHealth::healthy())
        }
    }

    fn must_service<T>(result: ServiceResult<T>) -> T {
        match result {
            Ok(value) => value,
            Err(err) => panic!("expected service Ok, got {}", err),
        }
    }

    fn must_db<T>(result: DbResult<T>) -> T {
        match result {
            Ok(value) => value,
            Err(err) => panic!("expected db Ok, got {}", err),
        }
    }

    async fn sqlite_store() -> DbRelationStore {
        let db = must_db(LocalSqliteDbPlugin::new());
        must_db(
            db.execute(DbStatement::new(
                "CREATE TABLE bcs_actor_relations (
                    from_id VARCHAR(128) NOT NULL,
                    to_id VARCHAR(128) NOT NULL,
                    env VARCHAR(32) NOT NULL,
                    kinds BIGINT NOT NULL DEFAULT 0,
                    allow BIGINT NOT NULL DEFAULT 0,
                    deny BIGINT NOT NULL DEFAULT 0,
                    is_creator INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (from_id, to_id, env)
                )",
            ))
            .await,
        );
        DbRelationStore::sqlite(Arc::new(db))
    }

    fn edge(from: &str, to: &str, env: &str, is_creator: bool) -> RelationEdge {
        RelationEdge {
            from_id: from.to_string(),
            to_id: to.to_string(),
            env: env.to_string(),
            kinds: 0,
            allow: 0,
            deny: 0,
            is_creator,
        }
    }

    #[test]
    fn mysql_owner_upsert_count_uses_odku_affected_rows() {
        let mut result = EnsureOwnerEdgesResult::default();
        count_mysql_owner_upsert(&mut result, 1);
        assert_eq!(result.created, 1);
        assert_eq!(result.upgraded, 0);

        count_mysql_owner_upsert(&mut result, 2);
        assert_eq!(result.created, 1);
        assert_eq!(result.upgraded, 1);

        count_mysql_owner_upsert(&mut result, 0);
        assert_eq!(result.created, 1);
        assert_eq!(result.upgraded, 1);
    }

    #[tokio::test]
    async fn mysql_owner_edges_counted_uses_execute_affected_rows() {
        let db = Arc::new(ScriptedDbPlugin::with_affected_rows([2, 1]));
        let store = DbRelationStore::mysql(db);

        let result = must_service(store.ensure_owner_edges_counted("h", "b", "dev").await);

        assert_eq!(result.created, 1);
        assert_eq!(result.upgraded, 1);
    }

    #[tokio::test]
    async fn sqlite_upsert_does_not_downgrade_creator() {
        let store = sqlite_store().await;

        must_service(store.upsert_edge(edge("h", "b", "dev", true)).await);
        must_service(store.upsert_edge(edge("h", "b", "dev", false)).await);

        let got = must_service(store.get_edge("h", "b", "dev").await);
        match got {
            Some(edge) => assert!(edge.is_creator),
            None => panic!("expected relation edge"),
        }
    }

    #[tokio::test]
    async fn sqlite_owner_edges_count_created_and_upgraded() {
        let store = sqlite_store().await;

        let first = must_service(store.ensure_owner_edges_counted("h", "b", "dev").await);
        assert_eq!(first.created, 2);
        assert_eq!(first.upgraded, 0);

        must_service(store.upsert_edge(edge("h2", "b2", "dev", false)).await);
        let second = must_service(store.ensure_owner_edges_counted("h2", "b2", "dev").await);
        assert_eq!(second.created, 1);
        assert_eq!(second.upgraded, 1);
    }

    #[tokio::test]
    async fn sqlite_friend_listing_requires_two_non_creator_edges() {
        let store = sqlite_store().await;

        must_service(store.add_friend_edges("a", "b", "dev").await);
        must_service(store.add_relation_edge("a", "c", "dev").await);
        must_service(store.ensure_owner_edges("a", "owned-bot", "dev").await);

        let mut friends = must_service(store.list_friends_via_relation("a", "dev").await);
        friends.sort();
        assert_eq!(friends, vec!["b".to_string()]);

        let from_bot = must_service(store.list_friends_via_relation("owned-bot", "dev").await);
        assert!(from_bot.is_empty());
    }

    #[tokio::test]
    async fn sqlite_remove_friend_edges_keeps_owner_edges() {
        let store = sqlite_store().await;

        must_service(store.ensure_owner_edges("h", "b", "dev").await);
        must_service(store.remove_friend_edges("h", "b", "dev").await);

        let owner = must_service(store.get_edge("h", "b", "dev").await);
        match owner {
            Some(edge) => assert!(edge.is_creator),
            None => panic!("owner edge must survive friend removal"),
        }
        assert!(must_service(store.get_edge("b", "h", "dev").await).is_none());
    }
}
