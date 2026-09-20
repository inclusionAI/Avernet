//! Group Context 持久化实现（Repo Port 实现）。
//!
//! 实现 `bcs_service_api::port::repo::GroupContextRepoPort`，对应 `store-plan.md`
//! §4：依赖 `bcs-db-api` 的 `DbPlugin` + `DbSqlFlavor`，首期交付 SQLite。
//!
//! ╔══════════════════════════════════════════════════════════════════════════╗
//! ║ 给 Java 出身读者：本文件怎么读                                          ║
//! ╠══════════════════════════════════════════════════════════════════════════╣
//! ║ 1. `GroupContextStore` struct = 一个带 DB 连接的 repository              ║
//! ║    （`Arc<dyn DbPlugin>` ≈ 注入的 DataSource）。                            ║
//! ║ 2. `#[async_trait] impl GroupContextRepoPort` = 接口实现，一个方法一个      ║
//! ║    SQL 操作。每个方法上方都有注释讲「执行过程 + SQL」。                       ║
//! ║ 3. `supersede` 是本模块最复杂的：它把「回填旧 valid_to + 写新 entry +        ║
//! ║    写新 policy」拼成**一个 transaction** 交给 DbPlugin 原子执行。           ║
//! ║    仔细看那段注释，它复刻了 plan.md updateContent step 5-10。               ║
//! ║ 4. 方言差异：SQLite / MySQL 在 SQL 字符串里用 match 分支，和              ║
//! ║    `MySqlGroupStore` 一个写法（参考 bcs-group-store/src/lib.rs:1354）。      ║
//! ╚══════════════════════════════════════════════════════════════════════════╝
//!
//! 本轮交付**重点是逻辑可 review，不保证整 crate 直接编译通过**——service-api
//! 侧的再导出、容器接线留给下个 PR。同 crate 内的 SQL/transaction 逻辑会尽量写对。

use std::sync::Arc;

use async_trait::async_trait;
use chrono::Utc;
use serde_json::Value as JsonValue;

use bcs_db_api::{
    db_get_column, db_get_column_opt, DbError, DbPlugin, DbResult, DbRow, DbSqlFlavor,
    DbStatement, DbTransactionParam, DbTransactionStep, DbTransactionStepResult, DbValue as Value,
};
use bcs_domain::{
    CollectFrom, Consistency, ContextEntry, ContextTime, Flow, FreshnessClass, Granularity,
    Lineage, Origin, PolicySnapshot, VisibleTo,
};
use bcs_service_api::port::repo::{
    AuditEntry, GroupContextRepoPort, InsertContextRequest, ScopeKey, StoredEntry,
    SupersedeOutcome, SupersedeRequest,
};
use bcs_service_api::types::{ServiceError, ServiceResult};

pub mod memory;
pub use memory::MemoryGroupContextRepo;

pub use bcs_service_api::port::repo::GroupContextRepoPort;

/// content 单条字节数上限（plan.md §2.4，默认 1 KB）。
pub const CONTENT_MAX_BYTES: usize = 1024;

/// SQL 里的活跃版本判定：valid_to IS NULL。
const ACTIVE_FILTER: &str = "valid_to IS NULL";

// ─────────────────────────────────────────────────────────────────────────
// Store struct + 构造（套用 bcs-group-store 的 DbPluginCompat / flavor 模式）
// ─────────────────────────────────────────────────────────────────────────

#[derive(Clone)]
struct DbPluginCompat {
    db: Arc<dyn DbPlugin>,
}

impl DbPluginCompat {
    fn new(db: Arc<dyn DbPlugin>) -> Self {
        Self { db }
    }

    fn plugin(&self) -> Arc<dyn DbPlugin> {
        self.db.clone()
    }

    async fn query_with(&self, sql: &str, params: Vec<Value>) -> DbResult<Vec<DbRow>> {
        self.db.query(DbStatement::with_params(sql, params)).await
    }

    async fn execute_with(&self, sql: &str, params: Vec<Value>) -> DbResult<u64> {
        self.db
            .execute(DbStatement::with_params(sql, params))
            .await
            .map(|result| result.affected_rows)
    }
}

pub struct GroupContextStore {
    db: DbPluginCompat,
    env: String,
    flavor: DbSqlFlavor,
}

impl GroupContextStore {
    /// MySQL 构造。
    pub fn new(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self {
            db: DbPluginCompat::new(db),
            env,
            flavor: DbSqlFlavor::Mysql,
        }
    }

    /// SQLite 构造（首期交付）。
    pub fn sqlite(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self {
            db: DbPluginCompat::new(db),
            env,
            flavor: DbSqlFlavor::Sqlite,
        }
    }

    fn now_millis(&self) -> ServiceResult<i64> {
        Utc::now()
            .timestamp_millis()
            .try_into()
            .map_err(|_| ServiceError::InternalError("system time out of range".to_string()))
    }

    /// 把 Unix 毫秒落库成 `YYYY-MM-DD HH:MM:SS.mmm`（MySQL `timestamp` /
    /// SQLite 兼容，参考 bcs-group-store db_timestamp_from_millis）。
    fn ts_from_millis(&self, millis: i64) -> ServiceResult<String> {
        use chrono::TimeZone;
        Utc.timestamp_millis_opt(millis)
            .single()
            .map(|ts| ts.format("%Y-%m-%d %H:%M:%S%.3f").to_string())
            .ok_or_else(|| ServiceError::InternalError("invalid timestamp".to_string()))
    }

    /// store-plan §2.2 policy_version 自动维护：
    /// - 旧条目不存在 → 首版本 "v0.1"
    /// - 旧 policy 与新 policy 五字段（collect_from/visible_to/freshness_class/
    ///   revalidate_due/obligations）全相同 → 复用旧 version（不递增、不新增 policy 行）
    /// - 有差异 → 整数位递增（v0.1 → v0.2；v1.0 → v1.1）
    ///
    /// 该方法只算版本号字符串；是否复用旧 policy 行（不插新行）由 supersede
    /// 内联决定。为 review 简洁，这里「有差异即递增」用小数最后一位 +1。
    async fn resolve_next_policy_version(
        &self,
        unique_id: &str,
        request: &SupersedeRequest,
    ) -> ServiceResult<String> {
        let sql = format!(
            "SELECT e.policy_version AS v, p.collect_from_json, p.visible_to_json,
                    p.freshness_class, p.revalidate_due, p.obligations
             FROM bcs_group_context_entry e
             JOIN bcs_group_context_policy p
               ON p.env = e.env AND p.context_unique_id = e.unique_id
             WHERE e.env = ? AND e.unique_id = ? AND e.{ACTIVE_FILTER} LIMIT 1"
        );
        let rows = self
            .db
            .query_with(&sql, vec![Value::from(self.env.as_str()), Value::from(unique_id)])
            .await
            .map_err(to_internal)?;
        let Some(row) = rows.into_iter().next() else {
            return Ok("v0.1".to_string());
        };

        let old_version: String = db_get_column(&row, "v").map_err(to_internal)?;
        let old_collect: String =
            db_get_column(&row, "collect_from_json").map_err(to_internal)?;
        let old_visible: String =
            db_get_column(&row, "visible_to_json").map_err(to_internal)?;
        let old_freshness: String = db_get_column(&row, "freshness_class").map_err(to_internal)?;
        let old_revalidate: Option<String> =
            db_get_column_opt(&row, "revalidate_due").ok().flatten();
        let old_obligations: Option<String> =
            db_get_column_opt(&row, "obligations").ok().flatten();

        // 新值归一化（与旧值同尺度比较）。
        let new_collect = serde_json::to_string(&request.new_flow.collect_from)
            .map_err(internal_json("collect_from"))?;
        let new_visible = serde_json::to_string(&request.new_flow.visible_to)
            .map_err(internal_json("visible_to"))?;
        let new_freshness = request.new_consistency.freshness_class.as_str();
        let new_revalidate_ts = match request.new_consistency.revalidate_due {
            Some(ms) => Some(self.ts_from_millis(ms)?),
            None => None,
        };

        let same = old_collect == new_collect
            && old_visible == new_visible
            && old_freshness == new_freshness
            && old_revalidate == new_revalidate_ts
            && old_obligations.as_deref() == request.policy_obligations.as_deref();

        if same {
            Ok(old_version)
        } else {
            Ok(bump_version(&old_version))
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────
// 行 → 领域 映射（参考 MySqlGroupStore::load_group_from_mysql 的取列风格）
// ─────────────────────────────────────────────────────────────────────────

fn str_granularity(row: &DbRow) -> ServiceResult<Granularity> {
    let s: String = db_get_column(row, "granularity").map_err(to_internal)?;
    Granularity::from_str(&s)
        .ok_or_else(|| ServiceError::InternalError(format!("unknown granularity '{s}'")))
}

fn str_freshness(row: &DbRow) -> ServiceResult<FreshnessClass> {
    let s: String = db_get_column(row, "freshness_class").map_err(to_internal)?;
    FreshnessClass::from_str(&s)
        .ok_or_else(|| ServiceError::InternalError(format!("unknown freshness '{s}'")))
}

fn parse_origin(row: &DbRow) -> ServiceResult<Origin> {
    Ok(Origin {
        tenant_id: db_get_column(row, "tenant_id").map_err(to_internal)?,
        group_id: db_get_column_opt(row, "group_id").ok().flatten(),
        session_id: db_get_column_opt(row, "session_id").ok().flatten(),
        run_id: db_get_column_opt(row, "run_id").ok().flatten(),
        actor_id: db_get_column(row, "actor_id").map_err(to_internal)?,
    })
}

fn parse_visible_to(json: Option<String>) -> ServiceResult<VisibleTo> {
    match json {
        Some(s) if !s.is_empty() => serde_json::from_str(&s).map_err(internal_json("visible_to")),
        _ => Ok(VisibleTo::default()),
    }
}

fn parse_collect_from(json: Option<String>) -> ServiceResult<CollectFrom> {
    match json {
        Some(s) if !s.is_empty() => serde_json::from_str(&s).map_err(internal_json("collect_from")),
        _ => Ok(CollectFrom::default()),
    }
}

fn parse_time(row: &DbRow, flavor: &DbSqlFlavor) -> ServiceResult<ContextTime> {
    // gmt 时间列是文本（YYYY-MM-DD HH:MM:SS.mmm）；用 flavor.unix_ts 读成秒再转毫秒。
    let valid_from_secs: Option<i64> = get_unix(row, "valid_from", flavor);
    let valid_to_secs: Option<i64> = get_unix_opt(row, "valid_to", flavor);
    let tx_time_secs: Option<i64> = get_unix(row, "tx_time", flavor);
    Ok(ContextTime {
        valid_from: valid_from_secs.unwrap_or(0) * 1000,
        valid_to: valid_to_secs.map(|s| s * 1000),
        tx_time: tx_time_secs.unwrap_or(0) * 1000,
    })
}

fn get_unix(row: &DbRow, col: &str, flavor: &DbSqlFlavor) -> Option<i64> {
    // 实际映射会 SELECT flavor.unix_ts(col) AS col_ts；这里按约定列名收。
    db_get_column_opt::<i64>(row, &format!("{col}_ts"))
        .ok()
        .flatten()
        .or_else(|| {
            let _ = flavor;
            None
        })
}

fn get_unix_opt(row: &DbRow, col: &str, flavor: &DbSqlFlavor) -> Option<i64> {
    get_unix(row, col, flavor)
}

/// row → ContextEntry + 关联 PolicySnapshot（StoredEntry）。
///
/// 说明：实际 SELECT 会 join 两张表的列；这里假设列在同一行里（以 entry_ 前缀
/// 取 policy 列）。为 review 简洁，列名约定见各方法 SQL。
fn row_to_stored_entry(row: &DbRow, flavor: &DbSqlFlavor) -> ServiceResult<StoredEntry> {
    let context_id: String = db_get_column(row, "context_id").map_err(to_internal)?;
    let content: String = db_get_column(row, "content").map_err(to_internal)?;
    let origin = parse_origin(row)?;
    let time = parse_time(row, flavor)?;
    let domain: String = db_get_column(row, "_domain").map_err(to_internal)?;
    let granularity = str_granularity(row)?;
    let freshness = str_freshness(row)?;
    let revalidate_due_secs: Option<i64> = get_unix_opt(row, "revalidate_due", flavor);
    let visible_to = parse_visible_to(db_get_column_opt(row, "visible_to_json").ok().flatten())?;
    let collect_from = parse_collect_from(db_get_column_opt(row, "collect_from_json").ok().flatten())?;
    let supersedes: Option<String> = db_get_column_opt(row, "supersedes").ok().flatten();
    let superseded_reason: Option<String> = db_get_column_opt(row, "superseded_reason").ok().flatten();
    let policy_version: String = db_get_column(row, "policy_version").map_err(to_internal)?;

    let entry = ContextEntry {
        context_id,
        content,
        origin,
        time,
        flow: Flow {
            visible_to,
            collect_from,
        },
        consistency: Consistency {
            domain,
            granularity,
            freshness_class: freshness,
            revalidate_due: revalidate_due_secs.map(|s| s * 1000),
        },
        lineage: Lineage {
            supersedes,
            superseded_reason,
        },
        policy_version,
        audit_ref: None,
        // TODO(spec §2.2): 推断注解首期未落库。
    };

    // PolicySnapshot 取同行的 policy 列（context_unique_id 用 entry 的 unique_id）。
    let policy = PolicySnapshot {
        context_id: entry.context_id.clone(),
        version: db_get_column(row, "policy_version").map_err(to_internal)?,
        domain: entry.consistency.domain.clone(),
        granularity: entry.consistency.granularity,
        collect_from_json: db_get_column_opt(row, "collect_from_json")
            .ok()
            .flatten()
            .unwrap_or_default(),
        visible_to_json: db_get_column_opt(row, "visible_to_json")
            .ok()
            .flatten()
            .unwrap_or_default(),
        freshness_class: entry.consistency.freshness_class,
        revalidate_due: entry.consistency.revalidate_due.map(|s| s * 1000),
        obligations: db_get_column_opt(row, "obligations").ok().flatten(),
    };

    Ok(StoredEntry { entry, policy })
}

fn to_internal<E: std::fmt::Display>(e: E) -> ServiceError {
    ServiceError::InternalError(e.to_string())
}

fn internal_json(field: &'static str) -> impl Fn(serde_json::Error) -> ServiceError {
    move |e| ServiceError::InternalError(format!("deserialize {field}: {e}"))
}

// ─────────────────────────────────────────────────────────────────────────
// tmpl 模板行映射
// ─────────────────────────────────────────────────────────────────────────

fn row_to_flow_template_fields(row: &DbRow) -> ServiceResult<(Flow, Consistency)> {
    let visible_to = parse_visible_to(db_get_column_opt(row, "visible_to_json").ok().flatten())?;
    let collect_from = parse_collect_from(db_get_column_opt(row, "collect_from_json").ok().flatten())?;
    let domain: String = db_get_column(row, "domain").map_err(to_internal)?;
    let granularity = str_granularity(row)?;
    let freshness = str_freshness(row)?;
    let revalidate_due_secs: Option<i64> = get_unix_opt(row, "revalidate_due", &DbSqlFlavor::Sqlite);
    Ok((
        Flow {
            visible_to,
            collect_from,
        },
        Consistency {
            domain,
            granularity,
            freshness_class: freshness,
            revalidate_due: revalidate_due_secs.map(|s| s * 1000),
        },
    ))
}

// ═════════════════════════════════════════════════════════════════════════
// RepoPort 实现
// ═════════════════════════════════════════════════════════════════════════
//
// 每个方法都先写「执行过程」，再写 SQL。SQL 用 `?` 位置占位符（SQLite/MySQL 都
// 用位置占位，见 bcs-db-api 测试），方言差异（如 FOR UPDATE）用 match 分支。
// env 是多租户分区键，每条 WHERE 都带 env = ?，沿仓库惯例。

#[async_trait]
impl GroupContextRepoPort for GroupContextStore {
    // ── find_active_in_scope：createByTemplate 幂等检查 ──────────────────
    //
    // 执行过程（plan.md createByTemplate step 7）：
    //   按 (granularity, scope_key) 查同作用域是否已有 valid_to IS NULL 的活跃版本。
    //   作用域过滤由 granularity 决定，全部带 env。
    //   返回命中条目的 context_id，供 core 判定冲突。
    //
    // SQL 关键点：
    //   - tenant_id / group_id / session_id / run_id 按 granularity 决定是否加 WHERE
    //     （模板未声明的字段不参与过滤——但这里查的是 entry 表本身已经实例化落库，
    //      故按 scope_key 的实际值过滤）。
    //   - 用 unique_id 作为版本链主键直接等值匹配最直观（见 store-plan §2.1）。
    async fn find_active_in_scope(
        &self,
        granularity: Granularity,
        scope_key: &ScopeKey,
    ) -> ServiceResult<Option<String>> {
        let unique_id = scope_key.unique_id(granularity);
        let sql = format!(
            "SELECT context_id FROM bcs_group_context_entry
             WHERE env = ? AND unique_id = ? AND {ACTIVE_FILTER} LIMIT 1"
        );
        let rows = self
            .db
            .query_with(&sql, vec![
                Value::from(self.env.as_str()),
                Value::from(unique_id.as_str()),
            ])
            .await
            .map_err(to_internal)?;
        match rows.into_iter().next() {
            Some(row) => {
                let id: String = db_get_column(&row, "context_id").map_err(to_internal)?;
                Ok(Some(id))
            }
            None => Ok(None),
        }
    }

    // ── insert_context：createByTemplate 首次写入 ──────────────────────
    //
    // 执行过程（plan.md createByTemplate step 8 + store-plan §2）：
    //   一个 transaction 内写 entry 一行 + policy 快照一行。
    //   context_id 由 application 层已生成（UUID）传入。
    //   unique_id 由 Consistency 算出，落库供后续版本链定位。
    //
    // 事务步骤：
    //   step 0 Execute: INSERT entry
    //   step 1 Execute: INSERT policy
    async fn insert_context(&self, request: InsertContextRequest) -> ServiceResult<()> {
        let now = self.now_millis()?;
        let now_ts = self.ts_from_millis(now)?;
        let unique_id = ScopeKey {
            tenant_id: request.entry.origin.tenant_id.clone(),
            group_id: request.entry.origin.group_id.clone(),
            session_id: request.entry.origin.session_id.clone(),
            run_id: request.entry.origin.run_id.clone(),
            domain: request.entry.consistency.domain.clone(),
        }
        .unique_id(request.entry.consistency.granularity);

        let visible_to_json = serde_json::to_string(&request.entry.flow.visible_to)
            .map_err(internal_json("visible_to"))?;
        let collect_from_json = serde_json::to_string(&request.entry.flow.collect_from)
            .map_err(internal_json("collect_from"))?;
        let valid_from_ts = self.ts_from_millis(request.entry.time.valid_from)?;
        let revalidate_due_ts = match request.entry.consistency.revalidate_due {
            Some(ms) => Some(self.ts_from_millis(ms)?),
            None => None,
        };

        let entry_sql = format!(
            "INSERT INTO bcs_group_context_entry
               (env, context_id, unique_id, content,
                tenant_id, group_id, session_id, run_id, actor_id,
                valid_from, valid_to, tx_time,
                supersedes, superseded_reason, policy_version)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?)"
        );
        let entry_params = vec![
            Value::from(self.env.as_str()),
            Value::from(request.entry.context_id.as_str()),
            Value::from(unique_id.as_str()),
            Value::from(request.entry.content.as_str()),
            Value::from(request.entry.origin.tenant_id.as_str()),
            opt_str(request.entry.origin.group_id.as_ref()),
            opt_str(request.entry.origin.session_id.as_ref()),
            opt_str(request.entry.origin.run_id.as_ref()),
            Value::from(request.entry.origin.actor_id.as_str()),
            Value::from(valid_from_ts.as_str()),
            Value::from(now_ts.as_str()),
            Value::from(request.entry.policy_version.as_str()),
        ];

        let policy_sql = format!(
            "INSERT INTO bcs_group_context_policy
               (env, context_unique_id, version, domain, granularity,
                collect_from_json, visible_to_json, freshness_class, revalidate_due, obligations)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        );
        let policy_params = vec![
            Value::from(self.env.as_str()),
            Value::from(unique_id.as_str()),
            Value::from(request.policy.version.as_str()),
            Value::from(request.policy.domain.as_str()),
            Value::from(request.policy.granularity.as_str()),
            Value::from(request.policy.collect_from_json.as_str()),
            Value::from(request.policy.visible_to_json.as_str()),
            Value::from(request.policy.freshness_class.as_str()),
            opt_str(revalidate_due_ts.as_ref()),
            opt_str(request.policy.obligations.as_ref()),
        ];

        let steps = vec![
            DbTransactionStep::Execute(DbStatement::with_params(&entry_sql, entry_params)),
            DbTransactionStep::Execute(DbStatement::with_params(&policy_sql, policy_params)),
        ];
        self.db
            .plugin()
            .transaction(steps)
            .await
            // duplicate 唯一键 → upper 翻 Conflict；其余 → InternalError。
            .map_err(|e| {
                if e.is_duplicate_key() {
                    ServiceError::Conflict(format!("context unique_id '{unique_id}' already exists"))
                } else {
                    to_internal(e)
                }
            })?;
        Ok(())
    }

    // ── supersede：取代写入（核心 + 原子）────────────────────────────
    //
    // ★ 这是本模块最复杂的存储操作，复刻 plan.md updateContent step 5-10。
    //
    // 执行过程：
    //   0. Query（按 flavor 决定是否加 FOR UPDATE 锁行）
    //        在目标 scope 下查活跃版本（valid_to IS NULL），取旧条目的
    //        context_id、policy_version（供版本比对）、policy 各列（供比对
    //        policy_version 是否需要递增）。
    //        ─ 查不到 → 该作用域无活跃版本可取代 → 返回 NotFound 语义。
    //   1. ExecuteChecked（expected_affected_rows = 1）：回填旧条目 valid_to = now。
    //        用 ExecuteChecked 而非 Execute：若并发两次取代抢同一行，第二个
    //        这一 UPDATE 影响 0 行 → DbError::ConditionFailed → 整个 transaction
    //        回滚，杜绝出现两条 valid_to 全为 NULL（或两条都指向同一旧条目）。
    //   2. Execute：写新 entry。
    //        supersedes = 旧条目 **context_id**（UUID，用 query_result 从 step 0 绑定），
    //        superseded_reason = 请求的 change_reason。
    //   3. Execute：写新 policy 快照。
    //        policy_version 比对结果在 core 层算好传入（v0.1 或递增）。
    //   四步拼成一个 transaction 交给 DbPlugin::transaction 原子执行：
    //   任一步失败整体回滚（store-plan §3）。
    //
    // 旧条目「数据面不变」是对 content/origin；生命周期 valid_to 在 step 1 回填。
    async fn supersede(&self, request: SupersedeRequest) -> ServiceResult<SupersedeOutcome> {
        let now = self.now_millis()?;
        let now_ts = self.ts_from_millis(now)?;
        let unique_id = request.scope_key.unique_id(request.granularity);

        // policy_version 比对需要读旧 policy 全字段（store-plan §2.2）：
        // 新旧 collect_from/visible_to/freshness/revalidate_due/obligations
        // 全一致 → 复用旧 version；有差异 → 递增（v0.1 → v0.2）。
        // 比对在事务外先查一次旧 policy（supersede 是低频写，多一次读可接受；
        // 真正的并发安全靠下方 transaction 的 ExecuteChecked 那步保证）。
        let new_policy_version = self.resolve_next_policy_version(&unique_id, &request).await?;

        // ── step 0：锁定并读旧活跃版本 + 旧 policy 快照 ─────────────────
        // SQLite 无 FOR UPDATE（靠事务内顺序）；MySQL 加行锁防并发抢取代。
        // 一次 join 把旧条目的 context_id、旧 policy 各列都取回，
        // 供：(a) step 2 的 supersedes 绑定；(b) policy_version 比对。
        let lock_sql = match self.flavor {
            DbSqlFlavor::Mysql => format!(
                "SELECT e.context_id, e.policy_version AS old_version,
                        p.collect_from_json AS old_collect_from,
                        p.visible_to_json AS old_visible_to,
                        p.freshness_class AS old_freshness,
                        p.revalidate_due AS old_revalidate_due,
                        p.obligations AS old_obligations
                 FROM bcs_group_context_entry e
                 JOIN bcs_group_context_policy p
                   ON p.env = e.env AND p.context_unique_id = e.unique_id
                 WHERE e.env = ? AND e.unique_id = ? AND e.{ACTIVE_FILTER} FOR UPDATE"
            ),
            DbSqlFlavor::Sqlite => format!(
                "SELECT e.context_id, e.policy_version AS old_version,
                        p.collect_from_json AS old_collect_from,
                        p.visible_to_json AS old_visible_to,
                        p.freshness_class AS old_freshness,
                        p.revalidate_due AS old_revalidate_due,
                        p.obligations AS old_obligations
                 FROM bcs_group_context_entry e
                 JOIN bcs_group_context_policy p
                   ON p.env = e.env AND p.context_unique_id = e.unique_id
                 WHERE e.env = ? AND e.unique_id = ? AND e.{ACTIVE_FILTER} LIMIT 1"
            ),
        };
        let step0 = DbTransactionStep::Query(DbStatement::with_params(
            &lock_sql,
            vec![Value::from(self.env.as_str()), Value::from(unique_id.as_str())],
        ));

        // ── step 1：回填旧条目 valid_to（ExecuteChecked 防并发）──────────
        // 用 ExecuteChecked 期望影响 1 行。两种情况触发回滚：
        //   (a) step 0 没查到活跃版本 → 这里 0 行 → ConditionFailed → 表达 NotFound；
        //   (b) 并发两次取代抢同一行 → 第二个这里 0 行（被第一个已回填）→ 回滚，
        //       杜绝出现两条 valid_to 全为 NULL 的活跃版本。
        let step1 = DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                &format!(
                    "UPDATE bcs_group_context_entry SET valid_to = ?, gmt_modified = {} \
                     WHERE env = ? AND unique_id = ? AND {ACTIVE_FILTER}",
                    self.flavor.now()
                ),
                vec![
                    Value::from(now_ts.as_str()),
                    Value::from(self.env.as_str()),
                    Value::from(unique_id.as_str()),
                ],
            ),
            expected_affected_rows: 1,
        };

        // policy_version 已在上方 resolve_next_policy_version 算出。

        // ── step 2：写新 entry ─────────────────────────────────────────
        let visible_to_json = serde_json::to_string(&request.new_flow.visible_to)
            .map_err(internal_json("visible_to"))?;
        let collect_from_json = serde_json::to_string(&request.new_flow.collect_from)
            .map_err(internal_json("collect_from"))?;
        let valid_from_ts = self.ts_from_millis(now)?;
        let revalidate_due_ts = match request.new_consistency.revalidate_due {
            Some(ms) => Some(self.ts_from_millis(ms)?),
            None => None,
        };
        let step2 = DbTransactionStep::Execute(DbStatement::with_transaction_params(
            &format!(
                "INSERT INTO bcs_group_context_entry
                   (env, context_id, unique_id, content,
                    tenant_id, group_id, session_id, run_id, actor_id,
                    valid_from, valid_to, tx_time,
                    supersedes, superseded_reason, policy_version)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)"
            ),
            vec![
                DbTransactionParam::value(self.env.as_str()),
                DbTransactionParam::value(request.new_context_id.as_str()),
                DbTransactionParam::value(unique_id.as_str()),
                DbTransactionParam::value(request.new_content.as_str()),
                DbTransactionParam::value(request.new_origin.tenant_id.as_str()),
                opt_param(request.new_origin.group_id.as_ref()),
                opt_param(request.new_origin.session_id.as_ref()),
                opt_param(request.new_origin.run_id.as_ref()),
                DbTransactionParam::value(request.new_origin.actor_id.as_str()),
                DbTransactionParam::value(valid_from_ts.as_str()),
                DbTransactionParam::value(now_ts.as_str()),
                // supersedes：绑定 step 0 查到的旧条目 context_id（query_result）。
                DbTransactionParam::query_result(0, 0, "context_id"),
                opt_param(request.change_reason.as_ref()),
                DbTransactionParam::value(new_policy_version.as_str()),
            ],
        ));
        let revalidate_due_param = opt_param(revalidate_due_ts.as_ref());

        // ── step 3：写新 policy 快照 ────────────────────────────────────
        let step3 = DbTransactionStep::Execute(DbStatement::with_transaction_params(
            &format!(
                "INSERT INTO bcs_group_context_policy
                   (env, context_unique_id, version, domain, granularity,
                    collect_from_json, visible_to_json, freshness_class, revalidate_due, obligations)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            vec![
                DbTransactionParam::value(self.env.as_str()),
                DbTransactionParam::value(unique_id.as_str()),
                DbTransactionParam::value(new_policy_version.as_str()),
                DbTransactionParam::value(request.new_consistency.domain.as_str()),
                DbTransactionParam::value(request.new_consistency.granularity.as_str()),
                DbTransactionParam::value(collect_from_json.as_str()),
                DbTransactionParam::value(visible_to_json.as_str()),
                DbTransactionParam::value(request.new_consistency.freshness_class.as_str()),
                revalidate_due_param,
                opt_param(request.policy_obligations.as_deref()),
            ],
        ));

        let results = self
            .db
            .plugin()
            .transaction(vec![step0, step1, step2, step3])
            .await
            .map_err(|e| match e {
                // step1 ExecuteChecked 0 行：(a) 无活跃版本可取代 (b) 并发抢取代。
                // 两者都回滚到一致状态；这里统一表达为 NotFound 语义，core 翻译给上层。
                DbError::ConditionFailed { .. } => ServiceError::InvalidOperation {
                    message: format!("no active version to supersede for '{unique_id}'"),
                    request_id: None,
                },
                other if other.is_duplicate_key() => ServiceError::Conflict(format!(
                    "context unique_id '{unique_id}' already exists"
                )),
                other => to_internal(other),
            })?;

        // step 0 结果：查不到活跃版本 → NotFound。
        let old_id = match results.first() {
            Some(DbTransactionStepResult::Rows(rows)) if !rows.is_empty() => {
                let r = &rows[0];
                let id: String = db_get_column(r, "context_id").map_err(to_internal)?;
                id
            }
            _ => {
                return Err(ServiceError::InvalidOperation {
                    message: format!("no active version to supersede for '{unique_id}'"),
                    request_id: None,
                })
            }
        };

        Ok(SupersedeOutcome {
            new_context_id: request.new_context_id,
            superseded_id: old_id,
            policy_version: new_policy_version,
        })
    }

    // ── find_entries_by_domain：updateContent 候选集 ───────────────────
    //
    // 执行过程（plan.md updateContent step 2-3）：
    //   按 domain 取同 domain 名下全部条目（含历史），core 在内存按 collect_from
    //   过滤写权限，再按 (granularity, scope_key) 分组建版本链。
    //   只取每条最新的 unique_id/ctx 元数据即可，但为 review 直接取全字段。
    async fn find_entries_by_domain(&self, domain: &str) -> ServiceResult<Vec<StoredEntry>> {
        let sql = format!(
            "SELECT e.context_id, e.content, e.tenant_id, e.group_id, e.session_id,
                    e.run_id, e.actor_id, {vf} AS valid_from_ts, {vt} AS valid_to_ts,
                    {tx} AS tx_time_ts, e.supersedes, e.superseded_reason, e.policy_version,
                    p.domain AS _domain, p.granularity, p.freshness_class,
                    {rd} AS revalidate_due_ts, p.collect_from_json, p.visible_to_json,
                    p.obligations
             FROM bcs_group_context_entry e
             JOIN bcs_group_context_policy p
               ON p.env = e.env AND p.context_unique_id = e.unique_id
             WHERE e.env = ? AND p.domain = ?",
            vf = self.flavor.unix_ts("e.valid_from"),
            vt = self.flavor.unix_ts("e.valid_to"),
            tx = self.flavor.unix_ts("e.tx_time"),
            rd = self.flavor.unix_ts("p.revalidate_due"),
        );
        let rows = self
            .db
            .query_with(&sql, vec![Value::from(self.env.as_str()), Value::from(domain)])
            .await
            .map_err(to_internal)?;
        rows.iter().map(|r| row_to_stored_entry(r, &self.flavor)).collect()
    }

    // ── find_candidates_for_retrieve：retrieve 候选 ────────────────────
    //
    // 执行过程（plan.md retrieve step 1-2）：
    //   粗筛：按 domain 取全部条目（含历史），core 再按 visible_to 过滤、
    //   按 granularity 分组取 active 版本、按 volatile 过期再过滤。
    //   与 find_entries_by_domain 同 SQL，retrieve 的精筛在 core（visible_to
    //   判定依赖完整 flow，DB WHERE 不好精确表达 user_ids 包含）。
    async fn find_candidates_for_retrieve(
        &self,
        domain: &str,
        _scope: &Origin,
    ) -> ServiceResult<Vec<StoredEntry>> {
        // 复用同一 join 查询；scope 作为粗筛参数透传（精细 visible_to 在 core 判）。
        self.find_entries_by_domain(domain).await
    }

    // ── list_active_contexts_for_actor：status 已存在 contexts ─────────
    //
    // 执行过程（plan.md status）：
    //   取当前 actor 作用域下的活跃 entry，带 description（来自 policy 或模板）。
    //   首期 description 从 policy 无字段 → 暂用 domain 作占位描述。
    //   返回 ContextView（含权限标记，但权限（W/R/WR）判定在 core，store 只回原始可见集）。
    //
    // 注：本方法首期按 actor 作用域返回所有 active 条目；permission 由 core 算。
    async fn list_active_contexts_for_actor(
        &self,
        scope: &Origin,
    ) -> ServiceResult<Vec<bcs_domain::ContextView>> {
        let exists_clause = scope_exists_clause(scope);
        let sql = format!(
            "SELECT p.domain, p.granularity
             FROM bcs_group_context_entry e
             JOIN bcs_group_context_policy p
               ON p.env = e.env AND p.context_unique_id = e.unique_id
             WHERE e.env = ? AND {ACTIVE_FILTER} {exists_clause}",
            exists_clause = exists_clause.clause,
        );
        let mut params = vec![Value::from(self.env.as_str())];
        params.extend(exists_clause.params.into_iter().map(Value::from));
        let rows = self
            .db
            .query_with(&sql, params)
            .await
            .map_err(to_internal)?;
        rows.iter()
            .map(|r| {
                let domain: String = db_get_column(r, "domain").map_err(to_internal)?;
                let granularity = str_granularity(r)?;
                Ok(bcs_domain::ContextView {
                    domain,
                    granularity,
                    description: String::new(), // TODO: description 首期从域外补
                    permission: bcs_domain::Permission::R, // 占位；core 按可见性重算
                })
            })
            .collect()
    }

    // ── list_creatable_templates_for_actor：status 可创建模板 ──────────
    //
    // 执行过程（plan.md status）：
    //   取能被当前 actor 创建的模板（collect_from 匹配）。粗糙匹配：tenant 相同；
    //   group/session/run 若模板声明则需匹配。精细匹配在 core（含 collect_from.user_id）。
    async fn list_creatable_templates_for_actor(
        &self,
        scope: &Origin,
    ) -> ServiceResult<Vec<bcs_domain::TemplateView>> {
        let exists_clause = scope_exists_clause(scope);
        let sql = format!(
            "SELECT template_id, granularity, description, params
             FROM bcs_group_context_template
             WHERE env = ? {exists_clause}",
            exists_clause = exists_clause.clause,
        );
        let mut params = vec![Value::from(self.env.as_str())];
        params.extend(exists_clause.params.into_iter().map(Value::from));
        let rows = self.db.query_with(&sql, params).await.map_err(to_internal)?;
        rows.iter()
            .map(|r| {
                Ok(bcs_domain::TemplateView {
                    template_id: db_get_column(r, "template_id").map_err(to_internal)?,
                    granularity: str_granularity(r)?,
                    description: db_get_column(r, "description").map_err(to_internal)?,
                    params: parse_param_names(
                        db_get_column_opt(r, "params").ok().flatten().unwrap_or_default(),
                    )?,
                })
            })
            .collect()
    }

    async fn find_policy_template(
        &self,
        template_id: &str,
    ) -> ServiceResult<Option<bcs_domain::PolicyTemplate>> {
        let sql = "SELECT template_id, description, params, domain, granularity,
                          collect_from_json, visible_to_json, freshness_class,
                          revalidate_due FROM bcs_group_context_template
                   WHERE env = ? AND template_id = ? LIMIT 1";
        let rows = self
            .db
            .query_with(sql, vec![Value::from(self.env.as_str()), Value::from(template_id)])
            .await
            .map_err(to_internal)?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        let (flow, consistency) = row_to_flow_template_fields(&row)?;
        Ok(Some(bcs_domain::PolicyTemplate {
            template_id: db_get_column(&row, "template_id").map_err(to_internal)?,
            description: db_get_column(&row, "description").map_err(to_internal)?,
            params: parse_param_names(
                db_get_column_opt(&row, "params").ok().flatten().unwrap_or_default(),
            )?,
            flow,
            consistency,
        }))
    }

    async fn list_policy_templates(
        &self,
        scope: &Origin,
    ) -> ServiceResult<Vec<bcs_domain::PolicyTemplate>> {
        let exists_clause = scope_exists_clause(scope);
        let sql = format!(
            "SELECT template_id, description, params, domain, granularity,
                    collect_from_json, visible_to_json, freshness_class, revalidate_due
             FROM bcs_group_context_template
             WHERE env = ? {exists_clause}",
            exists_clause = exists_clause.clause,
        );
        let mut params = vec![Value::from(self.env.as_str())];
        params.extend(exists_clause.params.into_iter().map(Value::from));
        let rows = self.db.query_with(&sql, params).await.map_err(to_internal)?;
        rows.iter()
            .map(|r| {
                let (flow, consistency) = row_to_flow_template_fields(r)?;
                Ok(bcs_domain::PolicyTemplate {
                    template_id: db_get_column(r, "template_id").map_err(to_internal)?,
                    description: db_get_column(r, "description").map_err(to_internal)?,
                    params: parse_param_names(
                        db_get_column_opt(r, "params").ok().flatten().unwrap_or_default(),
                    )?,
                    flow,
                    consistency,
                })
            })
            .collect()
    }

    // ── write_retrieve_audit：retrieve 强制落审计 ─────────────────────
    //
    // 执行过程（plan.md retrieve step 5）：
    //   即使结果空集也写一条审计，记录「查询过但无可见内容」。
    //   独立审计表 bcs_group_context_audit（store-plan 第 5 项决定）。
    async fn write_retrieve_audit(&self, entry: AuditEntry) -> ServiceResult<()> {
        let now = self.now_millis()?;
        let now_ts = self.ts_from_millis(now)?;
        let hit_json = serde_json::to_string(&entry.hit_context_ids)
            .map_err(internal_json("hit_context_ids"))?;
        let sql =
            "INSERT INTO bcs_group_context_audit
               (env, actor_id, tenant_id, group_id, session_id, run_id,
                domain, hit_context_ids_json, tx_time)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)";
        self.db
            .execute_with(sql, vec![
                Value::from(self.env.as_str()),
                Value::from(entry.actor_id.as_str()),
                Value::from(entry.tenant_id.as_str()),
                opt_str(entry.group_id.as_ref()),
                opt_str(entry.session_id.as_ref()),
                opt_str(entry.run_id.as_ref()),
                Value::from(entry.domain.as_str()),
                Value::from(hit_json.as_str()),
                Value::from(now_ts.as_str()),
            ])
            .await
            .map_err(to_internal)?;
        let _ = entry.tx_time; // 用服务器 now 落库，忽略调用方传入的 tx_time。
        Ok(())
    }
}

// ─────────────────────────────────────────────────────────────────────────
// 小工具：Option<String> ↔ DbValue；scope 命中子句；模板 params 解析
// ─────────────────────────────────────────────────────────────────────────

fn opt_str(s: Option<&String>) -> Value {
    match s {
        Some(v) => Value::from(v.as_str()),
        None => Value::Null,
    }
}

/// version 形如 "v0.1" → "v0.2"；"v1.0" → "v1.1"。小数末位 +1。
/// 解析失败兜底成 "v0.1之上 +0.1"，即直接返回 "v0.2"，保证不炸。
fn bump_version(version: &str) -> String {
    let s = version.strip_prefix('v').unwrap_or(version);
    let parts: Vec<&str> = s.split('.').collect();
    if parts.len() == 2 {
        if let (Ok(major), Ok(minor)) = (parts[0].parse::<u32>(), parts[1].parse::<u32>()) {
            return format!("v{major}.{}", minor + 1);
        }
    }
    "v0.2".to_string()
}

fn opt_param(s: Option<&str>) -> DbTransactionParam {
    match s {
        Some(v) => DbTransactionParam::value(v.to_string()),
        None => DbTransactionParam::value(Value::Null),
    }
}

/// scope 命中子句：按模板/实例的声明过滤作用域。
/// 未声明的字段不额外限制（plan.md createByTemplate step 3）。
struct ScopeClause {
    clause: String,
    params: Vec<String>,
}

fn scope_exists_clause(scope: &Origin) -> ScopeClause {
    let mut clause = String::new();
    let mut params = Vec::new();
    clause.push_str(" AND tenant_id = ?");
    params.push(scope.tenant_id.clone());
    if let Some(g) = &scope.group_id {
        clause.push_str(" AND group_id = ?");
        params.push(g.clone());
    }
    if let Some(s) = &scope.session_id {
        clause.push_str(" AND session_id = ?");
        params.push(s.clone());
    }
    if let Some(r) = &scope.run_id {
        clause.push_str(" AND run_id = ?");
        params.push(r.clone());
    }
    ScopeClause { clause, params }
}

fn parse_param_names(json: String) -> ServiceResult<Vec<bcs_domain::TemplateParam>> {
    if json.is_empty() {
        return Ok(Vec::new());
    }
    let arr: Vec<JsonValue> = serde_json::from_str(&json).map_err(internal_json("params"))?;
    Ok(arr
        .into_iter()
        .filter_map(|v| v.get("name").and_then(|n| n.as_str()).map(|s| bcs_domain::TemplateParam {
            name: s.to_string(),
        }))
        .collect())
}
