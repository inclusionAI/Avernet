use std::collections::HashMap;
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use std::time::{Duration, Instant};

use bcs_config_api::{DataSourceConfig, MysqlDbConfig, StatementProtocol};
use bcs_db_api::{DbError, DbResult};
use mysql_async::prelude::*;
use mysql_async::{
    Conn, Opts, OptsBuilder, Params, Pool, PoolConstraints, PoolOpts, Row, Transaction, TxOpts,
    Value,
};
use tracing::{error, info, warn};

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct MysqlExecuteResult {
    pub affected_rows: u64,
    pub last_insert_id: Option<u64>,
}

#[derive(Clone)]
pub struct MysqlDbManager {
    pools: Arc<HashMap<String, Pool>>,
    statement_executors: Arc<HashMap<String, StatementExecutor>>,
    config: MysqlDbConfig,
    enabled: bool,
}

/// Backward-compatible alias for callers that use the async manager name.
pub type AsyncMysqlDbManager = MysqlDbManager;

impl MysqlDbManager {
    pub async fn new(config: MysqlDbConfig) -> DbResult<Self> {
        let mut pools = HashMap::new();
        let mut statement_executors = HashMap::new();
        let ds = config.to_datasource_config();
        let pool = create_pool(&ds).await?;
        pools.insert(ds.name.clone(), pool);
        statement_executors.insert(
            ds.name.clone(),
            StatementExecutorFactory::create(ds.statement_protocol),
        );

        info!(
            datasource = %ds.name,
            "mysql database manager initialized"
        );
        Ok(Self {
            pools: Arc::new(pools),
            statement_executors: Arc::new(statement_executors),
            config,
            enabled: true,
        })
    }

    pub fn is_enabled(&self) -> bool {
        self.enabled
    }

    pub fn database_names(&self) -> Vec<&str> {
        self.pools.keys().map(|name| name.as_str()).collect()
    }

    pub fn has_database(&self, database_name: &str) -> bool {
        self.pools.contains_key(database_name)
    }

    pub fn config(&self) -> &MysqlDbConfig {
        &self.config
    }

    pub async fn close(&self) {
        for pool in self.pools.values() {
            let _ = pool.clone().disconnect().await;
        }
    }

    pub async fn query(&self, db: &str, sql: &str) -> DbResult<Vec<Row>> {
        let pool = self.require_pool(db)?;
        let start = Instant::now();
        let mut conn = pool.get_conn().await.map_err(|err| {
            DbError::Backend(format!("get mysql connection for '{db}': {err}"))
        })?;

        let result = conn.query(sql).await;
        let duration_ms = start.elapsed().as_millis();
        match result {
            Ok(rows) => {
                log_sql_ok(db, "query", sql, duration_ms, rows.len(), 0);
                Ok(rows)
            }
            Err(err) => {
                log_sql_error(db, "query", sql, duration_ms, &err.to_string());
                Err(DbError::Backend(format!("mysql query failed: {err}")))
            }
        }
    }

    pub async fn execute(&self, db: &str, sql: &str) -> DbResult<u64> {
        Ok(self.execute_result(db, sql).await?.affected_rows)
    }

    pub async fn execute_result(&self, db: &str, sql: &str) -> DbResult<MysqlExecuteResult> {
        let pool = self.require_pool(db)?;
        let start = Instant::now();
        let mut conn = pool.get_conn().await.map_err(|err| {
            DbError::Backend(format!("get mysql connection for '{db}': {err}"))
        })?;

        let result = conn.query_drop(sql).await;
        let duration_ms = start.elapsed().as_millis();
        match result {
            Ok(()) => {
                let result = MysqlExecuteResult {
                    affected_rows: conn.affected_rows(),
                    last_insert_id: conn.last_insert_id(),
                };
                log_sql_ok(
                    db,
                    "execute",
                    sql,
                    duration_ms,
                    result.affected_rows as usize,
                    0,
                );
                Ok(result)
            }
            Err(err) => {
                log_sql_error(db, "execute", sql, duration_ms, &err.to_string());
                Err(DbError::Backend(format!("mysql execute failed: {err}")))
            }
        }
    }

    pub async fn query_with(
        &self,
        db: &str,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<Vec<Row>> {
        let pool = self.require_pool(db)?;
        let executor = self.statement_executor(db)?;
        let start = Instant::now();
        let params_count = params.len();
        let mut conn = pool.get_conn().await.map_err(|err| {
            DbError::Backend(format!("get mysql connection for '{db}': {err}"))
        })?;

        let result = executor.query_conn(&mut conn, sql, params).await;
        let duration_ms = start.elapsed().as_millis();
        match result {
            Ok(rows) => {
                log_sql_ok(db, "query_with", sql, duration_ms, rows.len(), params_count);
                Ok(rows)
            }
            Err(err) => {
                log_sql_error(db, "query_with", sql, duration_ms, &err.to_string());
                Err(err)
            }
        }
    }

    pub async fn execute_with(&self, db: &str, sql: &str, params: Vec<Value>) -> DbResult<u64> {
        Ok(self
            .execute_with_result(db, sql, params)
            .await?
            .affected_rows)
    }

    pub async fn execute_with_result(
        &self,
        db: &str,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<MysqlExecuteResult> {
        let pool = self.require_pool(db)?;
        let executor = self.statement_executor(db)?;
        let start = Instant::now();
        let params_count = params.len();
        let mut conn = pool.get_conn().await.map_err(|err| {
            DbError::Backend(format!("get mysql connection for '{db}': {err}"))
        })?;

        let result = executor.execute_conn(&mut conn, sql, params).await;
        let duration_ms = start.elapsed().as_millis();
        match result {
            Ok(result) => {
                log_sql_ok(
                    db,
                    "execute_with",
                    sql,
                    duration_ms,
                    result.affected_rows as usize,
                    params_count,
                );
                Ok(result)
            }
            Err(err) => {
                log_sql_error(db, "execute_with", sql, duration_ms, &err.to_string());
                Err(err)
            }
        }
    }

    pub async fn with_transaction<F, T>(&self, db: &str, f: F) -> DbResult<T>
    where
        F: for<'tx> FnOnce(
            &'tx mut MysqlTransaction<'_>,
        ) -> Pin<Box<dyn Future<Output = DbResult<T>> + Send + 'tx>>,
    {
        let pool = self.require_pool(db)?;
        let executor = self.statement_executor(db)?;
        let start = Instant::now();
        let mut conn = pool.get_conn().await.map_err(|err| {
            DbError::Backend(format!("get mysql connection for '{db}': {err}"))
        })?;
        let tx = conn
            .start_transaction(TxOpts::default())
            .await
            .map_err(|err| DbError::Backend(format!("begin mysql transaction: {err}")))?;

        let mut tx_conn = MysqlTransaction {
            tx,
            db: db.to_string(),
            executor,
            statement_count: 0,
        };

        match f(&mut tx_conn).await {
            Ok(result) => {
                let statements = tx_conn.statement_count();
                tx_conn.commit().await?;
                log_tx_ok(db, start.elapsed().as_millis(), statements);
                Ok(result)
            }
            Err(err) => {
                let statements = tx_conn.statement_count();
                let _ = tx_conn.rollback().await;
                error!(
                    db = %db,
                    duration_ms = start.elapsed().as_millis(),
                    statements,
                    error = %err,
                    "mysql transaction failed"
                );
                Err(err)
            }
        }
    }

    fn require_pool(&self, db: &str) -> DbResult<&Pool> {
        if !self.enabled {
            return Err(DbError::Backend(
                "mysql database backend is disabled by configuration".to_string(),
            ));
        }
        self.pools.get(db).ok_or_else(|| {
            let available: Vec<&str> = self.pools.keys().map(|key| key.as_str()).collect();
            DbError::Backend(format!(
                "mysql datasource '{db}' not found; available datasource: {available:?}"
            ))
        })
    }

    fn statement_executor(&self, db: &str) -> DbResult<StatementExecutor> {
        self.statement_executors.get(db).cloned().ok_or_else(|| {
            let available: Vec<&str> = self
                .statement_executors
                .keys()
                .map(|key| key.as_str())
                .collect();
            DbError::Backend(format!(
                "mysql statement executor for '{db}' not found; available datasource: {available:?}"
            ))
        })
    }
}

async fn create_pool(config: &DataSourceConfig) -> DbResult<Pool> {
    let opts = Opts::from_url(&config.to_mysql_url()).map_err(|err| {
        DbError::InvalidInput(format!(
            "parse mysql URL for datasource '{}' (database '{}'): {}",
            config.name, config.database, err
        ))
    })?;

    let max = config.pool_size.max(1) as usize;
    let min = config.min_pool_size.max(1).min(config.pool_size.max(1)) as usize;
    let pool_constraints = PoolConstraints::new(min, max).ok_or_else(|| {
        DbError::InvalidInput(format!(
            "invalid mysql pool constraints for datasource '{}': min={}, max={}",
            config.name, min, max
        ))
    })?;

    let pool_opts = PoolOpts::default()
        .with_constraints(pool_constraints)
        // Keep session init statements stable across pool checkouts. The
        // manager controls transaction rollback explicitly.
        .with_reset_connection(false)
        .with_inactive_connection_ttl(Duration::from_secs(600))
        .with_ttl_check_interval(Duration::from_secs(60));

    let builder = OptsBuilder::from_opts(opts)
        .stmt_cache_size(config.stmt_cache_size as usize)
        .pool_opts(pool_opts)
        .init(init_statements());
    let pool = Pool::new(builder);

    let mut conn = pool.get_conn().await.map_err(|err| {
        DbError::Backend(format!(
            "connect mysql datasource '{}' at {}: {}",
            config.name,
            config.to_safe_url(),
            err
        ))
    })?;
    conn.query_drop("SELECT 1").await.map_err(|err| {
        DbError::Backend(format!(
            "mysql connection test failed for datasource '{}': {}",
            config.name, err
        ))
    })?;
    drop(conn);

    Ok(pool)
}

fn init_statements() -> Vec<&'static str> {
    vec![
        // Text-protocol parameter rendering uses mysql_common escaping that
        // assumes backslash escapes are enabled.
        "SET SESSION sql_mode = REPLACE(REPLACE(REPLACE(@@SESSION.sql_mode, ',NO_BACKSLASH_ESCAPES', ''), 'NO_BACKSLASH_ESCAPES,', ''), 'NO_BACKSLASH_ESCAPES', '')",
    ]
}

#[derive(Debug)]
pub struct MysqlTransaction<'a> {
    tx: Transaction<'a>,
    db: String,
    executor: StatementExecutor,
    statement_count: u32,
}

impl MysqlTransaction<'_> {
    pub async fn query(&mut self, sql: &str, params: Vec<Value>) -> DbResult<Vec<Row>> {
        self.statement_count += 1;
        let params_count = params.len();
        let start = Instant::now();
        let result = self.executor.query_tx(&mut self.tx, sql, params).await;
        let duration_ms = start.elapsed().as_millis();
        match result {
            Ok(rows) => {
                log_sql_ok(
                    &self.db,
                    "tx.query",
                    sql,
                    duration_ms,
                    rows.len(),
                    params_count,
                );
                Ok(rows)
            }
            Err(err) => {
                log_sql_error(&self.db, "tx.query", sql, duration_ms, &err.to_string());
                Err(err)
            }
        }
    }

    pub async fn execute(&mut self, sql: &str, params: Vec<Value>) -> DbResult<u64> {
        Ok(self.execute_result(sql, params).await?.affected_rows)
    }

    pub async fn execute_result(
        &mut self,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<MysqlExecuteResult> {
        self.statement_count += 1;
        let params_count = params.len();
        let start = Instant::now();
        let result = self.executor.execute_tx(&mut self.tx, sql, params).await;
        let duration_ms = start.elapsed().as_millis();
        match result {
            Ok(result) => {
                log_sql_ok(
                    &self.db,
                    "tx.execute",
                    sql,
                    duration_ms,
                    result.affected_rows as usize,
                    params_count,
                );
                Ok(result)
            }
            Err(err) => {
                log_sql_error(&self.db, "tx.execute", sql, duration_ms, &err.to_string());
                Err(err)
            }
        }
    }

    pub fn statement_count(&self) -> u32 {
        self.statement_count
    }

    async fn commit(self) -> DbResult<()> {
        self.tx
            .commit()
            .await
            .map_err(|err| DbError::Backend(format!("commit mysql transaction: {err}")))
    }

    async fn rollback(self) -> DbResult<()> {
        self.tx
            .rollback()
            .await
            .map_err(|err| DbError::Backend(format!("rollback mysql transaction: {err}")))
    }
}

#[derive(Debug, Clone, Copy)]
struct StatementExecutorFactory;

impl StatementExecutorFactory {
    fn create(protocol: StatementProtocol) -> StatementExecutor {
        match protocol {
            StatementProtocol::Text => StatementExecutor::Text(TextStatementExecutor),
            StatementProtocol::Prepared => StatementExecutor::Prepared(PreparedStatementExecutor),
        }
    }
}

#[derive(Debug, Clone)]
enum StatementExecutor {
    Text(TextStatementExecutor),
    Prepared(PreparedStatementExecutor),
}

impl StatementExecutor {
    async fn query_conn(
        &self,
        conn: &mut Conn,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<Vec<Row>> {
        match self {
            StatementExecutor::Text(executor) => executor.query_conn(conn, sql, params).await,
            StatementExecutor::Prepared(executor) => executor.query_conn(conn, sql, params).await,
        }
    }

    async fn execute_conn(
        &self,
        conn: &mut Conn,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<MysqlExecuteResult> {
        match self {
            StatementExecutor::Text(executor) => executor.execute_conn(conn, sql, params).await,
            StatementExecutor::Prepared(executor) => executor.execute_conn(conn, sql, params).await,
        }
    }

    async fn query_tx(
        &self,
        tx: &mut Transaction<'_>,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<Vec<Row>> {
        match self {
            StatementExecutor::Text(executor) => executor.query_tx(tx, sql, params).await,
            StatementExecutor::Prepared(executor) => executor.query_tx(tx, sql, params).await,
        }
    }

    async fn execute_tx(
        &self,
        tx: &mut Transaction<'_>,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<MysqlExecuteResult> {
        match self {
            StatementExecutor::Text(executor) => executor.execute_tx(tx, sql, params).await,
            StatementExecutor::Prepared(executor) => executor.execute_tx(tx, sql, params).await,
        }
    }
}

#[derive(Debug, Clone)]
struct TextStatementExecutor;

impl TextStatementExecutor {
    async fn query_conn(
        &self,
        conn: &mut Conn,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<Vec<Row>> {
        let rendered = render_sql(sql, &params)?;
        conn.query(rendered)
            .await
            .map_err(|err| DbError::Backend(format!("mysql query failed: {err}")))
    }

    async fn execute_conn(
        &self,
        conn: &mut Conn,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<MysqlExecuteResult> {
        let rendered = render_sql(sql, &params)?;
        conn.query_drop(rendered)
            .await
            .map(|()| MysqlExecuteResult {
                affected_rows: conn.affected_rows(),
                last_insert_id: conn.last_insert_id(),
            })
            .map_err(|err| DbError::Backend(format!("mysql execute failed: {err}")))
    }

    async fn query_tx(
        &self,
        tx: &mut Transaction<'_>,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<Vec<Row>> {
        let rendered = render_sql(sql, &params)?;
        tx.query(rendered)
            .await
            .map_err(|err| DbError::Backend(format!("mysql transaction query failed: {err}")))
    }

    async fn execute_tx(
        &self,
        tx: &mut Transaction<'_>,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<MysqlExecuteResult> {
        let rendered = render_sql(sql, &params)?;
        tx.query_drop(rendered)
            .await
            .map(|()| MysqlExecuteResult {
                affected_rows: tx.affected_rows(),
                last_insert_id: tx.last_insert_id(),
            })
            .map_err(|err| DbError::Backend(format!("mysql transaction execute failed: {err}")))
    }
}

#[derive(Debug, Clone)]
struct PreparedStatementExecutor;

impl PreparedStatementExecutor {
    async fn query_conn(
        &self,
        conn: &mut Conn,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<Vec<Row>> {
        if params.is_empty() {
            return conn
                .query(sql)
                .await
                .map_err(|err| DbError::Backend(format!("mysql query failed: {err}")));
        }
        let stmt = conn
            .prep(sql)
            .await
            .map_err(|err| DbError::Backend(format!("mysql prepare query failed: {err}")))?;
        let exec_result = conn.exec(&stmt, Params::Positional(params)).await;
        let close_result = conn.close(stmt).await;
        match (exec_result, close_result) {
            (Ok(rows), Ok(())) => Ok(rows),
            (Ok(_), Err(close_err)) => Err(DbError::Backend(format!(
                "mysql close query statement failed: {close_err}"
            ))),
            (Err(exec_err), Ok(())) => Err(DbError::Backend(format!(
                "mysql prepared query failed: {exec_err}"
            ))),
            (Err(exec_err), Err(close_err)) => Err(DbError::Backend(format!(
                "mysql prepared query failed: {exec_err}; close failed: {close_err}"
            ))),
        }
    }

    async fn execute_conn(
        &self,
        conn: &mut Conn,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<MysqlExecuteResult> {
        if params.is_empty() {
            return conn
                .query_drop(sql)
                .await
                .map(|()| MysqlExecuteResult {
                    affected_rows: conn.affected_rows(),
                    last_insert_id: conn.last_insert_id(),
                })
                .map_err(|err| DbError::Backend(format!("mysql execute failed: {err}")));
        }
        let stmt = conn
            .prep(sql)
            .await
            .map_err(|err| DbError::Backend(format!("mysql prepare execute failed: {err}")))?;
        let exec_result = conn.exec_drop(&stmt, Params::Positional(params)).await;
        let result = MysqlExecuteResult {
            affected_rows: conn.affected_rows(),
            last_insert_id: conn.last_insert_id(),
        };
        let close_result = conn.close(stmt).await;
        match (exec_result, close_result) {
            (Ok(()), Ok(())) => Ok(result),
            (Ok(()), Err(close_err)) => Err(DbError::Backend(format!(
                "mysql close execute statement failed: {close_err}"
            ))),
            (Err(exec_err), Ok(())) => Err(DbError::Backend(format!(
                "mysql prepared execute failed: {exec_err}"
            ))),
            (Err(exec_err), Err(close_err)) => Err(DbError::Backend(format!(
                "mysql prepared execute failed: {exec_err}; close failed: {close_err}"
            ))),
        }
    }

    async fn query_tx(
        &self,
        tx: &mut Transaction<'_>,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<Vec<Row>> {
        if params.is_empty() {
            return tx
                .query(sql)
                .await
                .map_err(|err| DbError::Backend(format!("mysql transaction query failed: {err}")));
        }
        let stmt = tx.prep(sql).await.map_err(|err| {
            DbError::Backend(format!("mysql transaction prepare query failed: {err}"))
        })?;
        let exec_result = tx.exec(&stmt, Params::Positional(params)).await;
        let close_result = tx.close(stmt).await;
        match (exec_result, close_result) {
            (Ok(rows), Ok(())) => Ok(rows),
            (Ok(_), Err(close_err)) => Err(DbError::Backend(format!(
                "mysql close transaction query statement failed: {close_err}"
            ))),
            (Err(exec_err), Ok(())) => Err(DbError::Backend(format!(
                "mysql prepared transaction query failed: {exec_err}"
            ))),
            (Err(exec_err), Err(close_err)) => Err(DbError::Backend(format!(
                "mysql prepared transaction query failed: {exec_err}; close failed: {close_err}"
            ))),
        }
    }

    async fn execute_tx(
        &self,
        tx: &mut Transaction<'_>,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<MysqlExecuteResult> {
        if params.is_empty() {
            return tx
                .query_drop(sql)
                .await
                .map(|()| MysqlExecuteResult {
                    affected_rows: tx.affected_rows(),
                    last_insert_id: tx.last_insert_id(),
                })
                .map_err(|err| {
                    DbError::Backend(format!("mysql transaction execute failed: {err}"))
                });
        }
        let stmt = tx.prep(sql).await.map_err(|err| {
            DbError::Backend(format!("mysql transaction prepare execute failed: {err}"))
        })?;
        let exec_result = tx.exec_drop(&stmt, Params::Positional(params)).await;
        let result = MysqlExecuteResult {
            affected_rows: tx.affected_rows(),
            last_insert_id: tx.last_insert_id(),
        };
        let close_result = tx.close(stmt).await;
        match (exec_result, close_result) {
            (Ok(()), Ok(())) => Ok(result),
            (Ok(()), Err(close_err)) => Err(DbError::Backend(format!(
                "mysql close transaction execute statement failed: {close_err}"
            ))),
            (Err(exec_err), Ok(())) => Err(DbError::Backend(format!(
                "mysql prepared transaction execute failed: {exec_err}"
            ))),
            (Err(exec_err), Err(close_err)) => Err(DbError::Backend(format!(
                "mysql prepared transaction execute failed: {exec_err}; close failed: {close_err}"
            ))),
        }
    }
}

pub(crate) fn render_sql(sql: &str, params: &[Value]) -> DbResult<String> {
    if params.is_empty() {
        return Ok(sql.to_string());
    }

    let mut out = String::with_capacity(sql.len() + params.len() * 8);
    let mut chars = sql.chars().peekable();
    let mut param_index = 0usize;
    let mut state = ScanState::Normal;

    while let Some(ch) = chars.next() {
        match state {
            ScanState::Normal => match ch {
                '?' => {
                    let Some(value) = params.get(param_index) else {
                        return Err(DbError::InvalidInput(format!(
                            "SQL placeholder count mismatch: expected more than {} params",
                            params.len()
                        )));
                    };
                    out.push_str(&value.as_sql(false));
                    param_index += 1;
                }
                '\'' => {
                    out.push(ch);
                    state = ScanState::SingleQuote;
                }
                '"' => {
                    out.push(ch);
                    state = ScanState::DoubleQuote;
                }
                '`' => {
                    out.push(ch);
                    state = ScanState::Backtick;
                }
                '-' if chars.peek() == Some(&'-') => {
                    out.push(ch);
                    if let Some(next) = chars.next() {
                        out.push(next);
                    }
                    state = ScanState::LineComment;
                }
                '#' => {
                    out.push(ch);
                    state = ScanState::LineComment;
                }
                '/' if chars.peek() == Some(&'*') => {
                    out.push(ch);
                    if let Some(next) = chars.next() {
                        out.push(next);
                    }
                    state = ScanState::BlockComment;
                }
                _ => out.push(ch),
            },
            ScanState::SingleQuote => {
                out.push(ch);
                if ch == '\\' {
                    if let Some(next) = chars.next() {
                        out.push(next);
                    }
                } else if ch == '\'' {
                    if chars.peek() == Some(&'\'') {
                        if let Some(next) = chars.next() {
                            out.push(next);
                        }
                    } else {
                        state = ScanState::Normal;
                    }
                }
            }
            ScanState::DoubleQuote => {
                out.push(ch);
                if ch == '\\' {
                    if let Some(next) = chars.next() {
                        out.push(next);
                    }
                } else if ch == '"' {
                    if chars.peek() == Some(&'"') {
                        if let Some(next) = chars.next() {
                            out.push(next);
                        }
                    } else {
                        state = ScanState::Normal;
                    }
                }
            }
            ScanState::Backtick => {
                out.push(ch);
                if ch == '`' {
                    if chars.peek() == Some(&'`') {
                        if let Some(next) = chars.next() {
                            out.push(next);
                        }
                    } else {
                        state = ScanState::Normal;
                    }
                }
            }
            ScanState::LineComment => {
                out.push(ch);
                if ch == '\n' {
                    state = ScanState::Normal;
                }
            }
            ScanState::BlockComment => {
                out.push(ch);
                if ch == '*' && chars.peek() == Some(&'/') {
                    if let Some(next) = chars.next() {
                        out.push(next);
                    }
                    state = ScanState::Normal;
                }
            }
        }
    }

    if param_index != params.len() {
        return Err(DbError::InvalidInput(format!(
            "SQL placeholder count mismatch: consumed {} params but got {}",
            param_index,
            params.len()
        )));
    }

    Ok(out)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ScanState {
    Normal,
    SingleQuote,
    DoubleQuote,
    Backtick,
    LineComment,
    BlockComment,
}

fn log_sql_ok(
    db: &str,
    method: &str,
    sql: &str,
    duration_ms: u128,
    rows: usize,
    params_count: usize,
) {
    if duration_ms > 1000 {
        error!(
            db = %db,
            method = %method,
            sql = %sql,
            duration_ms,
            rows,
            params = params_count,
            "mysql sql slow"
        );
    } else if duration_ms > 100 {
        warn!(
            db = %db,
            method = %method,
            sql = %sql,
            duration_ms,
            rows,
            params = params_count,
            "mysql sql slow"
        );
    } else {
        info!(
            db = %db,
            method = %method,
            sql = %sql,
            duration_ms,
            rows,
            params = params_count,
            "mysql sql ok"
        );
    }
}

fn log_sql_error(db: &str, method: &str, sql: &str, duration_ms: u128, err: &str) {
    error!(
        db = %db,
        method = %method,
        sql = %sql,
        duration_ms,
        error = %err,
        "mysql sql error"
    );
}

fn log_tx_ok(db: &str, duration_ms: u128, statements: u32) {
    if duration_ms > 200 {
        warn!(
            db = %db,
            duration_ms,
            statements,
            "mysql transaction slow"
        );
    } else {
        info!(
            db = %db,
            duration_ms,
            statements,
            "mysql transaction ok"
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn must<T>(result: DbResult<T>) -> T {
        match result {
            Ok(value) => value,
            Err(err) => panic!("expected Ok, got {}", err),
        }
    }

    #[test]
    fn render_sql_replaces_placeholders_with_escaped_literals() {
        let rendered = must(render_sql(
            "SELECT ? AS a, ? AS b, ? AS c, ? AS d",
            &[
                Value::from("O'Reilly"),
                Value::from(42_i64),
                Value::from(true),
                Value::NULL,
            ],
        ));

        assert!(rendered.contains("'O\\'Reilly'"));
        assert!(rendered.contains("42 AS b"));
        assert!(rendered.contains("true AS c") || rendered.contains("1 AS c"));
        assert!(rendered.contains("NULL AS d"));
    }

    #[test]
    fn render_sql_ignores_question_marks_inside_literals_and_comments() {
        let rendered = must(render_sql(
            "SELECT '?' AS a, `?` AS b, ? AS c -- ?\n/* ? */",
            &[Value::from("value")],
        ));

        assert!(rendered.contains("'?' AS a"));
        assert!(rendered.contains("`?` AS b"));
        assert!(rendered.contains("'value' AS c"));
        assert!(rendered.contains("-- ?"));
        assert!(rendered.contains("/* ? */"));
    }

    #[test]
    fn render_sql_rejects_placeholder_mismatch() {
        assert!(render_sql("SELECT ?, ?", &[Value::from(1)]).is_err());
        assert!(render_sql("SELECT ?", &[Value::from(1), Value::from(2)]).is_err());
    }

}
