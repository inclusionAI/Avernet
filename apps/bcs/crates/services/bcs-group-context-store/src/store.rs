use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbTransactionStep, DbTransactionStepResult, DbValue, db_get_column, db_get_column_opt,
};
use bcs_domain::{GroupContextEntry, GroupContextEntryMeta, GroupContextPolicy, GroupContextTime, ScopeLevel};
use chrono::{NaiveDateTime, TimeZone, Utc};

const TIMESTAMP_PARAM_MARKER: &str = "__bcs_group_context_timestamp_ms__";

pub type StoreResult<T> = Result<T, GroupContextStoreError>;

#[derive(Debug, thiserror::Error)]
pub enum GroupContextStoreError {
    #[error("active group context already exists for name={name} scope={scope}")]
    DuplicateActive { name: String, scope: String },
    #[error("active group context not found for name={name} scope={scope}")]
    NotFound { name: String, scope: String },
    #[error("concurrent group context update lost for name={name} scope={scope}")]
    Conflict { name: String, scope: String },
    #[error("group context storage error: {0}")]
    Backend(String),
}

impl GroupContextStoreError {
    fn backend(error: impl std::fmt::Display) -> Self {
        Self::Backend(error.to_string())
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct NewGroupContextEntry {
    pub context_id: String,
    pub name: String,
    pub scope_level: ScopeLevel,
    pub scope: String,
    pub content: String,
    pub visible_to: GroupContextPolicy,
    pub collect_from: GroupContextPolicy,
    pub supersedes: Option<i64>,
    pub change_reason: Option<String>,
    pub valid_from: GroupContextTime,
    pub valid_to: Option<GroupContextTime>,
    pub tx_time: GroupContextTime,
}

#[async_trait]
pub trait GroupContextRepo: Send + Sync {
    async fn create_active(&self, params: NewGroupContextEntry) -> StoreResult<GroupContextEntry>;

    /// Atomically supersede the currently active row (matched by `name`, `scope`,
    /// and `expected_active_id`) and insert a new version row.
    ///
    /// The `expected_active_id` is the id of the row read at the service layer
    /// during permission checking. It is used as a CAS guard: the UPDATE only
    /// succeeds if the active row has not been superseded by another
    /// concurrent transaction. If `affected_rows == 0`, the whole transaction
    /// is rolled back and `GroupContextStoreError::Conflict` is returned.
    ///
    /// # How it works (one atomic transaction)
    ///
    /// 1. `UPDATE … SET is_superseded = TRUE WHERE id = expected_active_id AND is_superseded = FALSE`
    ///    — if another Tx already superseded this row, `affected_rows == 0` → Conflict
    /// 2. `INSERT INTO …` the new row with `supersedes = expected_active_id`
    ///    — preserves the version chain regardless of which row we read first
    /// 3. Return the newly inserted row (loaded by `last_insert_id`)
    async fn supersede_active(
        &self,
        name: &str,
        scope: &str,
        expected_active_id: i64,
        params: NewGroupContextEntry,
    ) -> StoreResult<GroupContextEntry>;

    async fn get_active(&self, name: &str, scope: &str) -> StoreResult<Option<GroupContextEntry>>;

    async fn get_active_by_name_and_scopes(
        &self,
        name: &str,
        scopes: &[String],
    ) -> StoreResult<Vec<GroupContextEntry>>;

    async fn list_active_meta_by_scopes(&self, scopes: &[String])
    -> StoreResult<Vec<GroupContextEntryMeta>>;

    async fn list_versions(&self, context_id: &str) -> StoreResult<Vec<GroupContextEntry>>;
}

#[derive(Clone)]
pub struct SqlGroupContextStore {
    db: Arc<dyn DbPlugin>,
    flavor: DbSqlFlavor,
}

impl SqlGroupContextStore {
    pub fn oceanbase(db: Arc<dyn DbPlugin>) -> Self {
        Self::with_flavor(db, DbSqlFlavor::Mysql)
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>) -> Self {
        Self::with_flavor(db, DbSqlFlavor::Sqlite)
    }

    pub fn with_flavor(db: Arc<dyn DbPlugin>, flavor: DbSqlFlavor) -> Self {
        Self { db, flavor }
    }

    /// Generate a SQL expression that projects a `DATETIME`/`TIMESTAMP` column
    /// into milliseconds-since-epoch as an integer, regardless of backend.
    ///
    /// MySQL: `CAST(UNIX_TIMESTAMP(col) * 1000 AS UNSIGNED)` — forces an
    /// integer so the text-protocol parser produces `DbValue::U64` (or `I64`)
    /// instead of `DbValue::F64` (which `db_get_column::<i64>` cannot decode).
    ///
    /// SQLite: seconds are recovered via `strftime('%s')` and
    /// fractional seconds via `strftime('%f')` (format `SS.SSS`), with
    /// `substr(…, 4, 3)` extracting the millis component.
    fn timestamp_ms_expr(&self, column: &str, alias: &str) -> String {
        match self.flavor {
            DbSqlFlavor::Mysql => {
                format!("CAST(UNIX_TIMESTAMP({column}) * 1000 AS UNSIGNED) AS {alias}")
            }
            DbSqlFlavor::Sqlite => {
                format!(
                    "(CAST(strftime('%s', {column}) AS INTEGER) * 1000 + \
                     CAST(substr(strftime('%f', {column}), 4, 3) AS INTEGER)) AS {alias}"
                )
            }
        }
    }

    fn select_cols(&self) -> String {
        let base = "id, context_id, name, scope_level, scope, content, visible_to_json, \
                    collect_from_json, supersedes, change_reason, valid_from, valid_to, \
                    is_superseded, gmt_superseded, tx_time, gmt_create, gmt_modified";
        format!(
            "{base}, {vfm}, {vtm}, {gsm}, {ttm}, {gcm}, {gmm}",
            base = base,
            vfm = self.timestamp_ms_expr("valid_from", "valid_from_ms"),
            vtm = self.timestamp_ms_expr("valid_to", "valid_to_ms"),
            gsm = self.timestamp_ms_expr("gmt_superseded", "gmt_superseded_ms"),
            ttm = self.timestamp_ms_expr("tx_time", "tx_time_ms"),
            gcm = self.timestamp_ms_expr("gmt_create", "gmt_create_ms"),
            gmm = self.timestamp_ms_expr("gmt_modified", "gmt_modified_ms"),
        )
    }

    fn select_meta_cols(&self) -> String {
        let base = "name, scope_level, scope, visible_to_json, collect_from_json";
        format!(
            "{base}, {gcm}, {gmm}",
            base = base,
            gcm = self.timestamp_ms_expr("gmt_create", "gmt_create_ms"),
            gmm = self.timestamp_ms_expr("gmt_modified", "gmt_modified_ms"),
        )
    }

    fn sql_with_timestamp_params(&self, sql: &str) -> String {
        let expression = match self.flavor {
            DbSqlFlavor::Mysql => "FROM_UNIXTIME(? / 1000.0)",
            DbSqlFlavor::Sqlite => "?",
        };
        sql.replace(TIMESTAMP_PARAM_MARKER, expression)
    }

    fn timestamp_param(&self, time: GroupContextTime) -> DbValue {
        match self.flavor {
            // Bind the epoch rather than a timezone-free UTC string. FROM_UNIXTIME
            // renders the epoch in the current MySQL session timezone, and TIMESTAMP
            // stores the same instant regardless of that session timezone.
            DbSqlFlavor::Mysql => DbValue::from(time.timestamp_millis()),
            DbSqlFlavor::Sqlite => DbValue::from(format_db_time(time)),
        }
    }

    async fn load_by_id(&self, id: i64) -> StoreResult<GroupContextEntry> {
        let sql = format!(
            "SELECT {} FROM bcs_group_contexts WHERE id = ? LIMIT 1",
            self.select_cols()
        );
        let rows = self
            .db
            .query(DbStatement::with_params(&sql, vec![DbValue::from(id)]))
            .await
            .map_err(GroupContextStoreError::backend)?;
        rows.into_iter()
            .next()
            .ok_or_else(|| GroupContextStoreError::Backend(format!("inserted row {id} missing")))
            .and_then(|row| self.row_to_entry(&row))
    }

    fn row_to_entry(&self, row: &DbRow) -> StoreResult<GroupContextEntry> {
        let scope_level_raw: String =
            db_get_column(row, "scope_level").map_err(GroupContextStoreError::backend)?;
        let scope_level = serde_json::from_value(serde_json::Value::String(scope_level_raw))
            .map_err(GroupContextStoreError::backend)?;
        let visible_raw: String =
            db_get_column(row, "visible_to_json").map_err(GroupContextStoreError::backend)?;
        let collect_raw: String =
            db_get_column(row, "collect_from_json").map_err(GroupContextStoreError::backend)?;
        let valid_to_ms: Option<i64> =
            db_get_column_opt(row, "valid_to_ms").map_err(GroupContextStoreError::backend)?;
        let gmt_superseded_ms: Option<i64> =
            db_get_column_opt(row, "gmt_superseded_ms").map_err(GroupContextStoreError::backend)?;
        Ok(GroupContextEntry {
            id: db_get_column(row, "id").map_err(GroupContextStoreError::backend)?,
            context_id: db_get_column(row, "context_id")
                .map_err(GroupContextStoreError::backend)?,
            name: db_get_column(row, "name").map_err(GroupContextStoreError::backend)?,
            scope_level,
            scope: db_get_column(row, "scope").map_err(GroupContextStoreError::backend)?,
            content: db_get_column(row, "content").map_err(GroupContextStoreError::backend)?,
            visible_to: serde_json::from_str(&visible_raw)
                .map_err(GroupContextStoreError::backend)?,
            collect_from: serde_json::from_str(&collect_raw)
                .map_err(GroupContextStoreError::backend)?,
            supersedes: db_get_column_opt(row, "supersedes")
                .map_err(GroupContextStoreError::backend)?,
            change_reason: db_get_column_opt(row, "change_reason")
                .map_err(GroupContextStoreError::backend)?,
            valid_from: millis_column(row, "valid_from_ms")?,
            valid_to: valid_to_ms.map(time_from_millis).transpose()?,
            is_superseded: db_get_column(row, "is_superseded")
                .map_err(GroupContextStoreError::backend)?,
            gmt_superseded: gmt_superseded_ms.map(time_from_millis).transpose()?,
            tx_time: millis_column(row, "tx_time_ms")?,
            gmt_create: millis_column(row, "gmt_create_ms")?,
            gmt_modified: millis_column(row, "gmt_modified_ms")?,
        })
    }

    fn row_to_meta(&self, row: &DbRow) -> StoreResult<GroupContextEntryMeta> {
        let scope_level_raw: String =
            db_get_column(row, "scope_level").map_err(GroupContextStoreError::backend)?;
        let scope_level = serde_json::from_value(serde_json::Value::String(scope_level_raw))
            .map_err(GroupContextStoreError::backend)?;
        let visible_raw: String =
            db_get_column(row, "visible_to_json").map_err(GroupContextStoreError::backend)?;
        let collect_raw: String =
            db_get_column(row, "collect_from_json").map_err(GroupContextStoreError::backend)?;
        Ok(GroupContextEntryMeta {
            name: db_get_column(row, "name").map_err(GroupContextStoreError::backend)?,
            scope_level,
            scope: db_get_column(row, "scope").map_err(GroupContextStoreError::backend)?,
            visible_to: serde_json::from_str(&visible_raw)
                .map_err(GroupContextStoreError::backend)?,
            collect_from: serde_json::from_str(&collect_raw)
                .map_err(GroupContextStoreError::backend)?,
            gmt_create: millis_column(row, "gmt_create_ms")?,
            gmt_modified: millis_column(row, "gmt_modified_ms")?,
        })
    }
}

#[async_trait]
impl GroupContextRepo for SqlGroupContextStore {
    async fn create_active(&self, params: NewGroupContextEntry) -> StoreResult<GroupContextEntry> {
        let visible =
            serde_json::to_string(&params.visible_to).map_err(GroupContextStoreError::backend)?;
        let collect =
            serde_json::to_string(&params.collect_from).map_err(GroupContextStoreError::backend)?;
        let scope_level = serde_json::to_value(params.scope_level)
            .map_err(GroupContextStoreError::backend)?
            .as_str()
            .unwrap_or("group")
            .to_string();
        let sql = self.sql_with_timestamp_params(
            "INSERT INTO bcs_group_contexts \
             (context_id, name, scope_level, scope, content, visible_to_json, collect_from_json, \
              supersedes, change_reason, valid_from, valid_to, is_superseded, gmt_superseded, tx_time) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, __bcs_group_context_timestamp_ms__, \
                     __bcs_group_context_timestamp_ms__, FALSE, NULL, __bcs_group_context_timestamp_ms__)",
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(params.context_id.as_str()),
                    DbValue::from(params.name.as_str()),
                    DbValue::from(scope_level.as_str()),
                    DbValue::from(params.scope.as_str()),
                    DbValue::from(params.content.as_str()),
                    DbValue::from(visible.as_str()),
                    DbValue::from(collect.as_str()),
                    params
                        .supersedes
                        .map(DbValue::from)
                        .unwrap_or(DbValue::Null),
                    DbValue::from(params.change_reason.as_deref()),
                    self.timestamp_param(params.valid_from),
                    params
                        .valid_to
                        .map(|time| self.timestamp_param(time))
                        .unwrap_or(DbValue::Null),
                    self.timestamp_param(params.tx_time),
                ],
            ))
            .await
            .map_err(|error| {
                if error.is_duplicate_key() {
                    GroupContextStoreError::DuplicateActive {
                        name: params.name.clone(),
                        scope: params.scope.clone(),
                    }
                } else {
                    GroupContextStoreError::backend(error)
                }
            })?;
        if let Some(id) = result.last_insert_id.and_then(|id| i64::try_from(id).ok()) {
            return self.load_by_id(id).await;
        }
        self.get_active(&params.name, &params.scope)
            .await?
            .ok_or_else(|| GroupContextStoreError::Backend("created active row missing".into()))
    }

    async fn supersede_active(
        &self,
        name: &str,
        scope: &str,
        expected_active_id: i64,
        params: NewGroupContextEntry,
    ) -> StoreResult<GroupContextEntry> {
        let visible =
            serde_json::to_string(&params.visible_to).map_err(GroupContextStoreError::backend)?;
        let collect =
            serde_json::to_string(&params.collect_from).map_err(GroupContextStoreError::backend)?;
        let scope_level = serde_json::to_value(params.scope_level)
            .map_err(GroupContextStoreError::backend)?
            .as_str()
            .unwrap_or("group")
            .to_string();
        let tx_time = self.timestamp_param(params.tx_time);
        let valid_from = self.timestamp_param(params.valid_from);
        let valid_to = params
            .valid_to
            .map(|time| self.timestamp_param(time))
            .unwrap_or(DbValue::Null);
        let insert_sql = self.sql_with_timestamp_params(
            "INSERT INTO bcs_group_contexts \
             (context_id, name, scope_level, scope, content, visible_to_json, collect_from_json, \
              supersedes, change_reason, valid_from, valid_to, is_superseded, gmt_superseded, tx_time) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, __bcs_group_context_timestamp_ms__, \
                     __bcs_group_context_timestamp_ms__, FALSE, NULL, __bcs_group_context_timestamp_ms__)",
        );

        // CAS (Compare-And-Swap) transaction — two steps, one row must be affected at each step:
        //
        // Step 1: Mark the old active row as superseded.  The WHERE clause uses
        //         `expected_active_id` as the CAS token.  If another transaction
        //         already superseded this row (step 1 matching 0 rows), the
        //         ExecuteChecked will fail with ConditionFailed → Conflict.
        //
        // Step 2: Insert the new version row, linking it back to
        //         `expected_active_id` via the `supersedes` column.
        let steps = vec![
            DbTransactionStep::ExecuteChecked {
                statement: DbStatement::with_params(
                    &self.sql_with_timestamp_params(
                        "UPDATE bcs_group_contexts \
                         SET is_superseded = TRUE, \
                             gmt_superseded = __bcs_group_context_timestamp_ms__, \
                             gmt_modified = __bcs_group_context_timestamp_ms__ \
                         WHERE id = ? AND is_superseded = FALSE",
                    ),
                    vec![
                        tx_time.clone(),
                        tx_time.clone(),
                        DbValue::from(expected_active_id),
                    ],
                ),
                expected_affected_rows: 1,
            },
            DbTransactionStep::Execute(DbStatement::with_params(
                insert_sql,
                vec![
                    DbValue::from(params.context_id.as_str()),
                    DbValue::from(params.name.as_str()),
                    DbValue::from(scope_level.as_str()),
                    DbValue::from(params.scope.as_str()),
                    DbValue::from(params.content.as_str()),
                    DbValue::from(visible.as_str()),
                    DbValue::from(collect.as_str()),
                    DbValue::from(expected_active_id),
                    DbValue::from(params.change_reason.as_deref()),
                    valid_from,
                    valid_to,
                    tx_time.clone(),
                ],
            )),
        ];
        let results = self
            .db
            .transaction(steps)
            .await
            .map_err(|error| match error {
                DbError::ConditionFailed { .. } => GroupContextStoreError::Conflict {
                    name: name.to_string(),
                    scope: scope.to_string(),
                },
                DbError::InvalidInput(message) if message.contains("references missing row 0") => {
                    GroupContextStoreError::NotFound {
                        name: name.to_string(),
                        scope: scope.to_string(),
                    }
                }
                other => GroupContextStoreError::backend(other),
            })?;

        // Transaction committed: step 1 CAS UPDATE (affected_rows=1) and step 2
        // INSERT both succeeded. Extract the new row id to return the fresh
        // active entry.
        //
        // MySQL drivers return last_insert_id, but SQLite always returns None
        // (connection-scope issue: UPDATE/DELETE/stale values). Fall back to
        // get_active, which works because the INSERT just made this row the
        // active one for (name, scope).
        if let Some(id) = results.get(1).and_then(|result| match result {
            DbTransactionStepResult::Executed(executed) => executed.last_insert_id,
            DbTransactionStepResult::Rows(_) => None,
        }).and_then(|id| i64::try_from(id).ok()) {
            return self.load_by_id(id).await;
        }
        // Fallback for backends that don't surface last_insert_id inside
        // transactions (SQLite): the newly inserted row is the active one.
        self.get_active(name, scope)
            .await?
            .ok_or_else(|| GroupContextStoreError::Backend("superseded replacement row missing".into()))
    }

    async fn get_active(&self, name: &str, scope: &str) -> StoreResult<Option<GroupContextEntry>> {
        let sql = format!(
            "SELECT {} FROM bcs_group_contexts WHERE name = ? AND scope = ? AND is_superseded = FALSE LIMIT 1",
            self.select_cols()
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                &sql,
                vec![DbValue::from(name), DbValue::from(scope)],
            ))
            .await
            .map_err(GroupContextStoreError::backend)?;
        rows.into_iter()
            .next()
            .map(|row| self.row_to_entry(&row))
            .transpose()
    }

    async fn get_active_by_name_and_scopes(
        &self,
        name: &str,
        scopes: &[String],
    ) -> StoreResult<Vec<GroupContextEntry>> {
        if scopes.is_empty() {
            return Ok(Vec::new());
        }
        let placeholders = std::iter::repeat("?")
            .take(scopes.len())
            .collect::<Vec<_>>()
            .join(", ");
        let sql = format!(
            "SELECT {} FROM bcs_group_contexts \
             WHERE name = ? AND scope IN ({}) AND is_superseded = FALSE ORDER BY id ASC",
            self.select_cols(),
            placeholders
        );
        let mut params = vec![DbValue::from(name)];
        params.extend(scopes.iter().map(|scope| DbValue::from(scope.as_str())));
        let rows = self
            .db
            .query(DbStatement::with_params(&sql, params))
            .await
            .map_err(GroupContextStoreError::backend)?;
        rows.into_iter()
            .map(|row| self.row_to_entry(&row))
            .collect()
    }

    async fn list_active_meta_by_scopes(
        &self,
        scopes: &[String],
    ) -> StoreResult<Vec<GroupContextEntryMeta>> {
        if scopes.is_empty() {
            return Ok(Vec::new());
        }
        let placeholders = std::iter::repeat("?")
            .take(scopes.len())
            .collect::<Vec<_>>()
            .join(", ");
        let sql = format!(
            "SELECT {} FROM bcs_group_contexts \
             WHERE scope IN ({}) AND is_superseded = FALSE ORDER BY id ASC",
            self.select_meta_cols(),
            placeholders
        );
        let params = scopes.iter().map(|scope| DbValue::from(scope.as_str())).collect();
        let rows = self
            .db
            .query(DbStatement::with_params(&sql, params))
            .await
            .map_err(GroupContextStoreError::backend)?;
        rows.into_iter()
            .map(|row| self.row_to_meta(&row))
            .collect()
    }

    async fn list_versions(&self, context_id: &str) -> StoreResult<Vec<GroupContextEntry>> {
        let sql = format!(
            "SELECT {} FROM bcs_group_contexts WHERE context_id = ? ORDER BY id ASC",
            self.select_cols()
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                &sql,
                vec![DbValue::from(context_id)],
            ))
            .await
            .map_err(GroupContextStoreError::backend)?;
        rows.into_iter()
            .map(|row| self.row_to_entry(&row))
            .collect()
    }
}

fn format_db_time(time: GroupContextTime) -> String {
    time.format("%Y-%m-%d %H:%M:%S%.3f").to_string()
}

fn millis_column(row: &DbRow, column: &str) -> StoreResult<GroupContextTime> {
    let millis: i64 = db_get_column(row, column).map_err(GroupContextStoreError::backend)?;
    time_from_millis(millis)
}

fn time_from_millis(millis: i64) -> StoreResult<GroupContextTime> {
    Utc.timestamp_millis_opt(millis).single().ok_or_else(|| {
        GroupContextStoreError::Backend(format!("invalid timestamp millis {millis}"))
    })
}

#[allow(dead_code)]
fn parse_db_time(raw: &str) -> StoreResult<GroupContextTime> {
    NaiveDateTime::parse_from_str(raw, "%Y-%m-%d %H:%M:%S%.f")
        .map(|naive| Utc.from_utc_datetime(&naive))
        .map_err(GroupContextStoreError::backend)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    fn dummy_plugin() -> Arc<dyn DbPlugin> {
        Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().expect("sqlite plugin for unit tests"))
    }

    #[test]
    fn mysql_binds_epoch_inside_from_unixtime() {
        let store = SqlGroupContextStore::with_flavor(dummy_plugin(), DbSqlFlavor::Mysql);
        let sql = store.sql_with_timestamp_params(
            "VALUES (__bcs_group_context_timestamp_ms__, __bcs_group_context_timestamp_ms__)",
        );
        assert_eq!(
            sql,
            "VALUES (FROM_UNIXTIME(? / 1000.0), FROM_UNIXTIME(? / 1000.0))"
        );

        let time = Utc
            .timestamp_millis_opt(1_728_374_400_123)
            .single()
            .expect("timestamp");
        assert_eq!(store.timestamp_param(time), DbValue::from(1_728_374_400_123_i64));
    }

    #[test]
    fn sqlite_keeps_canonical_utc_text() {
        let store = SqlGroupContextStore::with_flavor(dummy_plugin(), DbSqlFlavor::Sqlite);
        let sql = store.sql_with_timestamp_params("VALUES (__bcs_group_context_timestamp_ms__)");
        assert_eq!(sql, "VALUES (?)");

        let time = Utc
            .timestamp_millis_opt(1_728_374_400_123)
            .single()
            .expect("timestamp");
        assert_eq!(
            store.timestamp_param(time),
            DbValue::from("2024-10-08 08:00:00.123")
        );
    }

    #[test]
    fn mysql_timestamp_ms_expr_is_cast_unsigned() {
        let store = SqlGroupContextStore::with_flavor(dummy_plugin(), DbSqlFlavor::Mysql);
        let sql = store.select_cols();
        assert!(sql.contains("CAST(UNIX_TIMESTAMP(valid_from) * 1000 AS UNSIGNED) AS valid_from_ms"));
        assert!(sql.contains("CAST(UNIX_TIMESTAMP(valid_to) * 1000 AS UNSIGNED) AS valid_to_ms"));
        assert!(
            sql.contains("CAST(UNIX_TIMESTAMP(gmt_superseded) * 1000 AS UNSIGNED) AS gmt_superseded_ms")
        );
        assert!(sql.contains("CAST(UNIX_TIMESTAMP(tx_time) * 1000 AS UNSIGNED) AS tx_time_ms"));
        assert!(sql.contains("CAST(UNIX_TIMESTAMP(gmt_create) * 1000 AS UNSIGNED) AS gmt_create_ms"));
        assert!(
            sql.contains("CAST(UNIX_TIMESTAMP(gmt_modified) * 1000 AS UNSIGNED) AS gmt_modified_ms")
        );
        // The old bare `)*1000 AS` pattern must be absent.
        assert!(!sql.contains(")*1000 AS"));
    }

    #[test]
    fn sqlite_timestamp_ms_expr_preserves_millis() {
        let store = SqlGroupContextStore::with_flavor(dummy_plugin(), DbSqlFlavor::Sqlite);
        let sql = store.select_cols();
        assert!(sql.contains("strftime('%s', valid_from)"));
        assert!(sql.contains("strftime('%f', valid_from)"));
        assert!(sql.contains("substr(strftime('%f', valid_from), 4, 3)"));
        assert!(sql.contains("AS valid_from_ms"));
        // The old bare `)*1000 AS` pattern must be absent for SQLite too.
        assert!(!sql.contains(")*1000 AS"));
    }

    #[test]
    fn meta_columns_exclude_payload_and_validity_fields() {
        for flavor in [DbSqlFlavor::Mysql, DbSqlFlavor::Sqlite] {
            let store = SqlGroupContextStore::with_flavor(dummy_plugin(), flavor);
            let sql = store.select_meta_cols();
            for column in [
                "name",
                "scope_level",
                "scope",
                "visible_to_json",
                "collect_from_json",
                "gmt_create_ms",
                "gmt_modified_ms",
            ] {
                assert!(sql.contains(column), "missing meta column {column} for {flavor:?}");
            }
            assert!(!sql.contains("content"), "meta projection must not read content");
            assert!(!sql.contains("valid_from"), "meta projection must not read valid_from");
            assert!(!sql.contains("valid_to"), "meta projection must not read valid_to");
        }
    }

    #[test]
    fn base_columns_are_present() {
        for flavor in [DbSqlFlavor::Mysql, DbSqlFlavor::Sqlite] {
            let store = SqlGroupContextStore::with_flavor(dummy_plugin(), flavor);
            let sql = store.select_cols();
            for col in [
                "id",
                "context_id",
                "name",
                "scope_level",
                "scope",
                "content",
                "visible_to_json",
                "collect_from_json",
                "supersedes",
                "change_reason",
                "valid_from",
                "valid_to",
                "is_superseded",
                "gmt_superseded",
                "tx_time",
                "gmt_create",
                "gmt_modified",
            ] {
                assert!(
                    sql.contains(col),
                    "missing base column {col} for {flavor:?}"
                );
            }
        }
    }

    #[test]
    fn oceanbase_constructor_uses_mysql_projection() {
        let store = SqlGroupContextStore::oceanbase(dummy_plugin());
        assert!(store
            .select_cols()
            .contains("CAST(UNIX_TIMESTAMP(valid_from) * 1000 AS UNSIGNED) AS valid_from_ms"));
    }

    #[test]
    fn backend_errors_and_out_of_range_timestamps_are_wrapped() {
        let backend = GroupContextStoreError::backend("boom");
        assert!(matches!(backend, GroupContextStoreError::Backend(message) if message == "boom"));

        let invalid_time = time_from_millis(i64::MAX)
            .expect_err("out-of-range timestamp must be a backend error");
        assert!(matches!(
            invalid_time,
            GroupContextStoreError::Backend(message) if message.contains("invalid timestamp millis")
        ));
    }

    #[tokio::test]
    async fn sqlite_load_by_id_returns_the_created_active_row() {
        let db = dummy_plugin();
        for sql in include_str!("../../../../migrations/sqlite/034_group_context.sql")
            .split(';')
            .map(str::trim)
            .filter(|sql| !sql.is_empty())
        {
            db.execute(DbStatement::new(sql)).await.expect("migration sql");
        }
        let store = SqlGroupContextStore::sqlite(db);
        let now = Utc
            .timestamp_millis_opt(1_728_374_400_000)
            .single()
            .expect("timestamp");
        let created = store
            .create_active(NewGroupContextEntry {
                context_id: "ctx-load-by-id".into(),
                name: "player_state".into(),
                scope_level: ScopeLevel::Session,
                scope: "sess-001".into(),
                content: "张三出局".into(),
                visible_to: GroupContextPolicy::users(["judge_bot"]),
                collect_from: GroupContextPolicy::users(["judge_bot"]),
                supersedes: None,
                change_reason: None,
                valid_from: now,
                valid_to: None,
                tx_time: now,
            })
            .await
            .expect("create active");

        let loaded = store.load_by_id(created.id).await.expect("load by id");
        assert_eq!(loaded, created);
    }

    #[tokio::test]
    async fn sqlite_get_active_by_name_and_scopes_matches_exact_name_and_requested_scopes() {
        let db = dummy_plugin();
        for sql in include_str!("../../../../migrations/sqlite/034_group_context.sql")
            .split(';')
            .map(str::trim)
            .filter(|sql| !sql.is_empty())
        {
            db.execute(DbStatement::new(sql)).await.expect("migration sql");
        }
        let store = SqlGroupContextStore::sqlite(db);
        let now = Utc
            .timestamp_millis_opt(1_728_374_400_000)
            .single()
            .expect("timestamp");

        for (context_id, name, scope_level, scope) in [
            ("ctx-target-s1", "player_state", ScopeLevel::Session, "sess-001"),
            ("ctx-other-s1", "other_state", ScopeLevel::Session, "sess-001"),
            ("ctx-target-s2", "player_state", ScopeLevel::Group, "group-001"),
        ] {
            store
                .create_active(NewGroupContextEntry {
                    context_id: context_id.into(),
                    name: name.into(),
                    scope_level,
                    scope: scope.into(),
                    content: name.into(),
                    visible_to: GroupContextPolicy::users(["judge_bot"]),
                    collect_from: GroupContextPolicy::users(["judge_bot"]),
                    supersedes: None,
                    change_reason: None,
                    valid_from: now,
                    valid_to: None,
                    tx_time: now,
                })
                .await
                .expect("create active row");
        }

        let rows = store
            .get_active_by_name_and_scopes(
                "player_state",
                &["sess-001".to_string(), "group-001".to_string()],
            )
            .await
            .expect("get active rows by name and scopes");
        assert_eq!(rows.len(), 2);
        assert!(rows
            .iter()
            .any(|row| row.scope == "sess-001" && row.name == "player_state"));
        assert!(rows
            .iter()
            .any(|row| row.scope == "group-001" && row.name == "player_state"));
        assert!(!rows.iter().any(|row| row.name == "other_state"));
    }

    #[test]
    fn parse_db_time_converts_database_timestamps() {
        let parsed = parse_db_time("2026-10-12 14:56:08.123").expect("valid database time");
        assert_eq!(parsed.timestamp_millis(), 1_791_816_968_123);

        let invalid = parse_db_time("not-a-time").expect_err("invalid database time");
        assert!(matches!(invalid, GroupContextStoreError::Backend(_)));
    }
}