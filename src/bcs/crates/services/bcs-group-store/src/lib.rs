//! MySQL-based Group Session Storage.
//!
//! This module provides a persistent group session store backed by MySQL.
//!
//! # Architecture
//!
//! ```text
//! MySQL: Session metadata + Participants (persistent)
//! ```
//!
//! Messages and workspace are NOT persisted - they are lost on server restart.

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbPlugin, DbResult, DbRow, DbSqlFlavor, DbStatement, DbTransactionStep,
    DbTransactionStepResult, DbValue as Value, db_get_column, db_get_column_opt,
};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;
use tracing::{debug, error, info, warn};

use bcs_service_api::{
    ActorKind, Group, GroupMessage, GroupMetricCount, GroupMetricsSnapshotPort,
    GroupStatus, Participant, ParticipantKind, ParticipantMode, ParticipantRole, RoutingPolicy,
    ServiceError, ServiceResult, Workspace,GroupStrategy
};

pub mod memory;

pub use bcs_service_api::port::repo::GroupRepoPort;
pub use memory::{GroupBuilder, MemoryGroupRepo};

/// MySQL-backed group repository.
pub type MysqlGroupRepo = MySqlGroupStore;

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

    async fn query_with(
        &self,
        logical_db: &str,
        sql: &str,
        params: Vec<Value>,
    ) -> DbResult<Vec<DbRow>> {
        assert_empty_logical_db(logical_db)?;
        self.db.query(DbStatement::with_params(sql, params)).await
    }

    async fn execute_with(&self, logical_db: &str, sql: &str, params: Vec<Value>) -> DbResult<u64> {
        assert_empty_logical_db(logical_db)?;
        self.db
            .execute(DbStatement::with_params(sql, params))
            .await
            .map(|result| result.affected_rows)
    }
}

fn assert_empty_logical_db(logical_db: &str) -> DbResult<()> {
    if logical_db.is_empty() {
        Ok(())
    } else {
        Err(DbError::InvalidInput(
            "DbPlugin is bound to a single datasource by bootstrap; service code must not pass logical_db routing keys"
                .to_string(),
        ))
    }
}

/// MySQL-backed group session store.
///
/// Uses MySQL for persistent storage of session metadata and participants.
///
/// Messages and workspace are NEVER persisted - they are lost on server restart.
pub struct MySqlGroupStore {
    /// Database plugin selected by the composition root.
    db: DbPluginCompat,
    /// TODO: remove with DbPluginCompat once legacy helper signatures stop threading logical_db.
    /// Retained as an always-empty logical label for legacy helper signatures.
    logical_db: String,
    /// Environment for multi-tenancy.
    env: String,
    /// SQL dialect (MySQL vs SQLite).
    flavor: DbSqlFlavor,
    /// In-memory message counts per group (not persisted, lost on restart).
    message_counts: RwLock<HashMap<String, usize>>,
    /// In-memory group cache (master mode only, avoids repeated DB queries).
    cache: RwLock<HashMap<String, Group>>,
}

impl MySqlGroupStore {
    /// Create a new MySqlGroupStore.
    pub fn new(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self {
            db: DbPluginCompat::new(db),
            logical_db: String::new(),
            env,
            flavor: DbSqlFlavor::Mysql,
            message_counts: RwLock::new(HashMap::new()),
            cache: RwLock::new(HashMap::new()),
        }
    }

    /// Create a new MySqlGroupStore with SQLite dialect.
    pub fn sqlite(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self {
            db: DbPluginCompat::new(db),
            logical_db: String::new(),
            env,
            flavor: DbSqlFlavor::Sqlite,
            message_counts: RwLock::new(HashMap::new()),
            cache: RwLock::new(HashMap::new()),
        }
    }

    /// Convert GroupStatus to string.
    fn status_to_str(status: &GroupStatus) -> &'static str {
        match status {
            GroupStatus::Active => "active",
            GroupStatus::Completed => "completed",
            GroupStatus::Error => "error",
            GroupStatus::Closed => "closed",
            GroupStatus::Inactive => "inactive",
        }
    }

    /// Convert string to GroupStatus.
    fn str_to_status(s: &str) -> GroupStatus {
        match s {
            "active" => GroupStatus::Active,
            "completed" => GroupStatus::Completed,
            "error" => GroupStatus::Error,
            "closed" => GroupStatus::Closed,
            "inactive" => GroupStatus::Inactive,
            _ => GroupStatus::Active,
        }
    }

    /// Convert ParticipantRole to string.
    fn role_to_str(role: &ParticipantRole) -> &'static str {
        match role {
            ParticipantRole::Driver => "driver",
            ParticipantRole::Consultant => "consultant",
            ParticipantRole::Manager => "manager",
            ParticipantRole::Worker => "worker",
            ParticipantRole::Observer => "observer",
        }
    }

    /// Convert string to ParticipantRole.
    fn str_to_role(s: &str) -> ParticipantRole {
        match s {
            "driver" => ParticipantRole::Driver,
            "consultant" => ParticipantRole::Consultant,
            "manager" => ParticipantRole::Manager,
            "worker" => ParticipantRole::Worker,
            "observer" => ParticipantRole::Observer,
            _ => ParticipantRole::Driver,
        }
    }

    /// Convert UNIX_TIMESTAMP seconds (from MySQL) to milliseconds.
    fn seconds_to_millis(secs: Option<i64>) -> u64 {
        secs.unwrap_or(0) as u64 * 1000
    }

    /// Convert ActorKind to canonical string used in MySQL.
    fn actor_kind_to_str(kind: ActorKind) -> &'static str {
        match kind {
            ActorKind::Bot => "bot",
            ActorKind::Human => "human",
        }
    }

    /// Convert ParticipantMode to canonical string used in MySQL.
    fn mode_to_str(mode: ParticipantMode) -> &'static str {
        match mode {
            ParticipantMode::Auto => "auto",
            ParticipantMode::Muted => "muted",
            ParticipantMode::Present => "present",
            ParticipantMode::Absent => "absent",
        }
    }

    /// Convert `GroupKind` to the canonical string stored in
    /// `bcs_groups.group_kind` (Task G.2 / migration 005).
    fn group_kind_to_str(kind: bcs_service_api::GroupKind) -> &'static str {
        match kind {
            bcs_service_api::GroupKind::Normal => "normal",
            bcs_service_api::GroupKind::Dm => "dm",
        }
    }

    /// Parse `bcs_groups.group_kind` column. NULL / unknown values fall back
    /// to `Normal` for backward compatibility with rows that pre-date
    /// migration 005.
    fn parse_group_kind(s: Option<&str>) -> bcs_service_api::GroupKind {
        match s {
            Some("dm") => bcs_service_api::GroupKind::Dm,
            _ => bcs_service_api::GroupKind::Normal,
        }
    }

    /// Convert `GroupStrategy` to the canonical string stored in
    /// `bcs_groups.group_strategy`.
    fn group_strategy_to_str(strategy: GroupStrategy) -> &'static str {
        match strategy {
            GroupStrategy::Chat => "chat",
            GroupStrategy::ManagerWorker => "manager_worker",
            GroupStrategy::StateMachine => "state_machine",
        }
    }

    /// Parse `bcs_groups.group_strategy` column. NULL / unknown values fall back
    /// to `Chat` for backward compatibility with rows that pre-date this column.
    fn parse_group_strategy(s: Option<&str>) -> GroupStrategy {
        match s {
            Some("manager_worker") => GroupStrategy::ManagerWorker,
            Some("state_machine") => GroupStrategy::StateMachine,
            _ => GroupStrategy::Chat,
        }
    }

    /// Parse `actor_kind` column. Unknown / NULL values fall back to `Bot`
    /// (consistent with the DB-level DEFAULT and Requirement 3.16). The
    /// caller is responsible for emitting an `error!` log when this happens
    /// during the normalization step.
    #[allow(dead_code)]
    fn parse_actor_kind(s: Option<&str>) -> ActorKind {
        match s {
            Some("human") => ActorKind::Human,
            _ => ActorKind::Bot,
        }
    }

    /// Parse `mode` column without validating against `actor_kind`. Returns
    /// `None` for unknown values so the normalization step can detect them
    /// and apply `ParticipantMode::default_for(actor_kind)`.
    fn parse_participant_mode_opt(s: Option<&str>) -> Option<ParticipantMode> {
        match s {
            Some("auto") => Some(ParticipantMode::Auto),
            Some("muted") => Some(ParticipantMode::Muted),
            Some("present") => Some(ParticipantMode::Present),
            Some("absent") => Some(ParticipantMode::Absent),
            _ => None,
        }
    }

    /// Normalize an `(actor_kind_str, mode_str)` row pair read from
    /// `bcs_group_participants` into a valid `(ActorKind, ParticipantMode)`
    /// pair, in-memory only (Task M.6, Requirement 3.10#2 and 3.18#6).
    ///
    /// Behavior matrix:
    ///
    /// | actor_kind_str         | mode_str                         | result                                | log    |
    /// |------------------------|----------------------------------|---------------------------------------|--------|
    /// | NULL                   | (any)                            | actor_kind = Bot (compat path)        | none   |
    /// | "bot" / "human"        | NULL                             | mode = default_for(kind)              | none   |
    /// | "bot" / "human"        | valid + matches kind             | mode = parsed                         | none   |
    /// | "bot" / "human"        | valid but illegal for this kind  | mode = default_for(kind), normalized  | ERROR  |
    /// | "bot" / "human"        | unknown string ("supervised", …) | mode = default_for(kind), normalized  | ERROR  |
    /// | unknown string         | (any)                            | actor_kind = Bot, normalized          | ERROR  |
    ///
    /// NULL on either column is a normal compatibility path (existing rows
    /// pre-dating this migration) and MUST NOT spam ERROR logs. Only truly
    /// invalid data — illegal combinations, unrecognized strings — is
    /// surfaced as ERROR with the full set of triage fields:
    /// `group_id, actor_id, actor_kind, mode, env`.
    ///
    /// The offending DB row is NEVER rewritten; it is fixed in-memory so that
    /// downstream business logic always observes a valid combination.
    fn normalize_kind_mode(
        group_id: &str,
        actor_id: &str,
        env: &str,
        actor_kind_str: Option<&str>,
        mode_str: Option<&str>,
    ) -> (ActorKind, ParticipantMode) {
        let kind = match actor_kind_str {
            Some("bot") => ActorKind::Bot,
            Some("human") => ActorKind::Human,
            None => {
                // Compat path: column NULL / absent. Silently default per M.6 (a).
                ActorKind::Bot
            }
            Some(other) => {
                error!(
                    group_id = %group_id,
                    actor_id = %actor_id,
                    env = %env,
                    actor_kind = %other,
                    mode = ?mode_str,
                    "mysql_store: unknown actor_kind value loaded from DB; \
                     normalizing to 'bot' in-memory only"
                );
                ActorKind::Bot
            }
        };

        let mode = match mode_str {
            // (a) NULL / absent — silent compat path, derive default for kind.
            None => ParticipantMode::default_for(kind),
            Some(raw) => {
                match Self::parse_participant_mode_opt(Some(raw)) {
                    Some(m) if m.is_valid_for(kind) => m,
                    Some(m) => {
                        // (b) Recognized mode value but illegal for this kind.
                        let fallback = ParticipantMode::default_for(kind);
                        error!(
                            group_id = %group_id,
                            actor_id = %actor_id,
                            env = %env,
                            actor_kind = ?kind,
                            mode = ?m,
                            "mysql_store: invalid (actor_kind, mode) combination loaded from DB; \
                             normalizing in-memory only"
                        );
                        fallback
                    }
                    None => {
                        // (c) Unrecognized mode string (e.g. "supervised").
                        let fallback = ParticipantMode::default_for(kind);
                        error!(
                            group_id = %group_id,
                            actor_id = %actor_id,
                            env = %env,
                            actor_kind = ?kind,
                            mode = %raw,
                            "mysql_store: unrecognized mode value loaded from DB; \
                             normalizing in-memory only"
                        );
                        fallback
                    }
                }
            }
        };

        (kind, mode)
    }

    // ========== MySQL Operations ==========

    /// Deserialize routing_policy_json column into Option<RoutingPolicy>.
    fn deserialize_routing_policy(json_str: Option<String>) -> Option<RoutingPolicy> {
        json_str.and_then(|s| {
            if s.is_empty() {
                return None;
            }
            match serde_json::from_str::<RoutingPolicy>(&s) {
                Ok(policy) => Some(policy),
                Err(e) => {
                    warn!(error = %e, json = %s, "Failed to deserialize routing_policy_json, using None");
                    None
                }
            }
        })
    }

    /// Load session from MySQL.
    async fn load_group_from_mysql(&self, group_id: &str) -> Option<Group> {
        // Task G.2 / migration 005: read group_kind + dm_pair_key from DB so
        // dm groups round-trip through `get()` without losing their identity.
        let sql = format!(
            "SELECT group_id, label, status, driver_bot, originator, routing_policy_json, context, \
             service_group_uuid, service_mode, service_spec, version, record_status, \
             {} AS created_ts, {} AS updated_ts, \
             group_kind, dm_pair_key, group_strategy, visibility \
             FROM bcs_groups WHERE group_id = ? AND env = ?",
            self.flavor.unix_ts("gmt_create"),
            self.flavor.unix_ts("gmt_modified"),
        );

        let rows = self
            .db
            .query_with(
                &self.logical_db,
                &sql,
                vec![Value::from(group_id), Value::from(self.env.as_str())],
            )
            .await
            .ok()?;

        if let Some(row) = rows.first() {
            let id: String = db_get_column(row, "group_id").ok()?;
            let label: Option<String> = db_get_column_opt(row, "label").ok().flatten();
            let status_str: String = db_get_column(row, "status").unwrap_or_default();
            let driver_bot: String = db_get_column(row, "driver_bot").ok()?;
            let originator: Option<String> = db_get_column_opt(row, "originator").ok().flatten();
            let routing_policy_json: Option<String> =
                db_get_column_opt(row, "routing_policy_json").ok().flatten();
            let context: Option<String> = db_get_column_opt(row, "context").ok().flatten();
            let service_group_uuid: Option<String> =
                db_get_column_opt(row, "service_group_uuid").ok().flatten();
            let service_mode: Option<String> =
                db_get_column_opt(row, "service_mode").ok().flatten();
            let service_spec_json: Option<String> =
                db_get_column_opt(row, "service_spec").ok().flatten();
            let service_spec: Option<bcs_service_api::ServiceSpec> =
                match service_spec_json.as_deref() {
                    Some(s) if !s.is_empty() => serde_json::from_str(s).ok(),
                    _ => None,
                };
            let version: i32 =
                db_get_column_opt::<i64>(row, "version").ok().flatten().unwrap_or(1) as i32;
            let record_status: String = db_get_column_opt(row, "record_status")
                .ok()
                .flatten()
                .unwrap_or_else(|| "active".to_string());
            let created_ts: Option<i64> = db_get_column_opt(row, "created_ts").ok().flatten();
            let updated_ts: Option<i64> = db_get_column_opt(row, "updated_ts").ok().flatten();
            let group_kind_str: Option<String> =
                db_get_column_opt(row, "group_kind").ok().flatten();
            let dm_pair_key: Option<String> = db_get_column_opt(row, "dm_pair_key").ok().flatten();
            let group_strategy_str: Option<String> =
                db_get_column_opt(row, "group_strategy").ok().flatten();
            let group_strategy = Self::parse_group_strategy(group_strategy_str.as_deref());
            let visibility: String = db_get_column_opt(row, "visibility")
                .ok()
                .flatten()
                .unwrap_or_else(|| "private".to_string());

            let participants = self.load_participants_from_mysql(group_id).await;

            return Some(Group {
                id,
                label,
                status: Self::str_to_status(&status_str),
                driver_bot,
                originator,
                routing_policy: Self::deserialize_routing_policy(routing_policy_json),
                context,
                participants,
                messages: Vec::new(),            // Not persisted
                workspace: Workspace::default(), // Not persisted
                service_group_uuid,
                service_mode,
                created_at: Self::seconds_to_millis(created_ts),
                updated_at: Self::seconds_to_millis(updated_ts),
                group_kind: Self::parse_group_kind(group_kind_str.as_deref()),
                dm_pair_key,
                group_strategy,
                service_spec,
                version,
                record_status,
                visibility,
            });
        }

        None
    }

    /// Load participants from MySQL.
    async fn load_participants_from_mysql(&self, group_id: &str) -> Vec<Participant> {
        let sql = "SELECT bot_uuid, role, actor_kind, mode FROM bcs_group_participants \
             WHERE group_id = ? AND env = ?";

        let rows = match self
            .db
            .query_with(
                &self.logical_db,
                sql,
                vec![Value::from(group_id), Value::from(self.env.as_str())],
            )
            .await
        {
            Ok(r) => r,
            Err(e) => {
                warn!(group_id = %group_id, error = %e, "load_participants_from_mysql: query failed");
                return Vec::new();
            }
        };

        rows.iter()
            .filter_map(|row| {
                let bot_uuid: String = db_get_column(row, "bot_uuid").ok()?;
                let role_str: String = db_get_column(row, "role").ok()?;
                let actor_kind_str: Option<String> =
                    db_get_column_opt(row, "actor_kind").ok().flatten();
                let mode_str: Option<String> = db_get_column_opt(row, "mode").ok().flatten();

                let (actor_kind, mode) = Self::normalize_kind_mode(
                    group_id,
                    &bot_uuid,
                    self.env.as_str(),
                    actor_kind_str.as_deref(),
                    mode_str.as_deref(),
                );

                Some(Participant {
                    bot_uuid,
                    bot_name: None,
                    kind: Some(ParticipantKind::Bot),
                    role: Self::str_to_role(&role_str),
                    actor_kind,
                    mode: Some(mode),
                })
            })
            .collect()
    }

    /// Load all sessions from MySQL.
    async fn load_all_groups_from_mysql(&self) -> Vec<Group> {
        // Fixed-shape JOIN: one prepared statement regardless of data volume.
        // Task G.2: also project gs.group_kind / gs.dm_pair_key so the `list()`
        // result reflects the persisted dm identity (otherwise dm groups would
        // collapse to GroupKind::Normal in memory after a server restart).
        let _start = std::time::Instant::now();
        let sql = format!(
            "SELECT gs.group_id, gs.label, gs.status, gs.driver_bot, gs.originator, \
                    gp.bot_uuid, gp.role, gs.routing_policy_json, gs.context, \
                    gs.service_group_uuid, gs.service_mode, gs.service_spec, gs.version, gs.record_status, \
                    {} AS created_ts, {} AS updated_ts, \
                    gp.actor_kind, gp.mode, gs.group_kind, gs.dm_pair_key, gs.group_strategy, gs.visibility \
             FROM bcs_groups gs \
             LEFT JOIN bcs_group_participants gp ON gs.group_id = gp.group_id AND gp.env = ? \
             WHERE gs.env = ?",
            self.flavor.unix_ts("gs.gmt_create"),
            self.flavor.unix_ts("gs.gmt_modified"),
        );
        let rows = match self.db.query_with(
            &self.logical_db,
            &sql,
            vec![Value::from(self.env.as_str()), Value::from(self.env.as_str())],
        ).await
        {
            Ok(r) => {
                let elapsed = _start.elapsed();
                if elapsed.as_millis() > 100 {
                    warn!(duration_ms = %elapsed.as_millis(), rows = r.len(), "slow load_all_groups_from_mysql");
                } else {
                    info!(duration_ms = %elapsed.as_millis(), rows = r.len(), "load_all_groups_from_mysql");
                }
                r
            }
            Err(e) => {
                warn!(duration_ms = %_start.elapsed().as_millis(), error = %e, "load_all_groups_from_mysql failed");
                return Vec::new();
            }
        };

        let mut groups_map: HashMap<String, Group> = HashMap::new();
        for row in &rows {
            let group_id: String = match db_get_column(row, "group_id") {
                Ok(v) => v,
                Err(_) => continue,
            };
            let entry = groups_map.entry(group_id.clone()).or_insert_with(|| {
                let label: Option<String> = db_get_column_opt(row, "label").ok().flatten();
                let status_str: String = db_get_column(row, "status").unwrap_or_default();
                let driver_bot: String = db_get_column(row, "driver_bot").unwrap_or_default();
                let originator: Option<String> =
                    db_get_column_opt(row, "originator").ok().flatten();
                let routing_policy_json: Option<String> =
                    db_get_column_opt(row, "routing_policy_json").ok().flatten();
                let context: Option<String> = db_get_column_opt(row, "context").ok().flatten();
                let service_group_uuid: Option<String> =
                    db_get_column_opt(row, "service_group_uuid").ok().flatten();
                let service_mode: Option<String> =
                    db_get_column_opt(row, "service_mode").ok().flatten();
                let service_spec_json: Option<String> =
                    db_get_column_opt(row, "service_spec").ok().flatten();
                let service_spec: Option<bcs_service_api::ServiceSpec> =
                    match service_spec_json.as_deref() {
                        Some(s) if !s.is_empty() => serde_json::from_str(s).ok(),
                        _ => None,
                    };
                let version: i32 = db_get_column_opt::<i64>(row, "version")
                    .ok()
                    .flatten()
                    .unwrap_or(1) as i32;
                let record_status: String = db_get_column_opt(row, "record_status")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "active".to_string());
                let created_ts: Option<i64> = db_get_column_opt(row, "created_ts").ok().flatten();
                let updated_ts: Option<i64> = db_get_column_opt(row, "updated_ts").ok().flatten();
                let group_kind_str: Option<String> =
                    db_get_column_opt(row, "group_kind").ok().flatten();
                let dm_pair_key: Option<String> =
                    db_get_column_opt(row, "dm_pair_key").ok().flatten();
                let group_strategy_str: Option<String> =
                    db_get_column_opt(row, "group_strategy").ok().flatten();
                let group_strategy = Self::parse_group_strategy(group_strategy_str.as_deref());
                let visibility: String = db_get_column_opt(row, "visibility")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "private".to_string());

                Group {
                    id: group_id,
                    label,
                    status: Self::str_to_status(&status_str),
                    driver_bot,
                    originator,
                    routing_policy: Self::deserialize_routing_policy(routing_policy_json),
                    context,
                    participants: Vec::new(),
                    messages: Vec::new(),
                    workspace: Workspace::default(),
                    service_group_uuid,
                    service_mode,
                    created_at: Self::seconds_to_millis(created_ts),
                    updated_at: Self::seconds_to_millis(updated_ts),
                    group_kind: Self::parse_group_kind(group_kind_str.as_deref()),
                    dm_pair_key,
                    group_strategy,
                    service_spec,
                    version,
                    record_status,
                    visibility,
                }
            });
            if let (Ok(bot_uuid), Ok(role_str)) = (
                db_get_column::<String>(row, "bot_uuid"),
                db_get_column::<String>(row, "role"),
            ) {
                if !entry.participants.iter().any(|p| p.bot_uuid == bot_uuid) {
                    let actor_kind_str: Option<String> =
                        db_get_column_opt(row, "actor_kind").ok().flatten();
                    let mode_str: Option<String> = db_get_column_opt(row, "mode").ok().flatten();
                    let (actor_kind, mode) = Self::normalize_kind_mode(
                        &entry.id,
                        &bot_uuid,
                        self.env.as_str(),
                        actor_kind_str.as_deref(),
                        mode_str.as_deref(),
                    );
                    entry.participants.push(Participant {
                        bot_uuid,
                        bot_name: None,
                        kind: Some(ParticipantKind::Bot),
                        role: Self::str_to_role(&role_str),
                        actor_kind,
                        mode: Some(mode),
                    });
                }
            }
        }
        let mut groups = groups_map.into_values().collect::<Vec<_>>();
        Group::sort_by_updated_at_desc(&mut groups);
        groups
    }

    /// Delete session from MySQL.
    async fn delete_group_from_mysql(&self, group_id: &str) -> ServiceResult<bool> {
        // Delete participants first
        self.db.execute_with(
            &self.logical_db,
            "DELETE FROM bcs_group_participants WHERE group_id = ? AND env = ?",
            vec![Value::from(group_id), Value::from(self.env.as_str())],
        ).await
            .map_err(|e| {
                warn!(group_id = %group_id, error = %e, "Failed to delete group participants from MySQL");
                ServiceError::InternalError(format!("Failed to delete group participants: {}", e))
            })?;

        // Delete group
        let deleted = self
            .db
            .execute_with(
                &self.logical_db,
                "DELETE FROM bcs_groups WHERE group_id = ? AND env = ?",
                vec![Value::from(group_id), Value::from(self.env.as_str())],
            )
            .await
            .map(|n| n > 0)
            .map_err(|e| {
                warn!(group_id = %group_id, error = %e, "Failed to delete group from MySQL");
                ServiceError::InternalError(format!("Failed to delete group: {}", e))
            })?;

        Ok(deleted)
    }
}

fn sql_metric_service_mode_to_option(raw: &str) -> Option<String> {
    match raw {
        "none" => None,
        "master_slave" => Some("master_slave".to_string()),
        _ => Some("other".to_string()),
    }
}

#[async_trait]
impl GroupMetricsSnapshotPort for MySqlGroupStore {
    async fn group_counts(&self) -> ServiceResult<Vec<GroupMetricCount>> {
        let rows = self.db.query_with(
            &self.logical_db,
            "SELECT status, group_kind, group_strategy, service_mode, COUNT(*) AS group_count \
             FROM ( \
                 SELECT status, \
                        COALESCE(group_kind, 'normal') AS group_kind, \
                        CASE \
                            WHEN group_strategy = 'manager_worker' THEN 'manager_worker' \
                            WHEN group_strategy = 'state_machine' THEN 'state_machine' \
                            ELSE 'chat' \
                        END AS group_strategy, \
                        CASE \
                            WHEN service_mode IS NULL OR TRIM(service_mode) = '' THEN 'none' \
                            WHEN service_mode = 'master_slave' THEN 'master_slave' \
                            ELSE 'other' \
                        END AS service_mode \
                 FROM bcs_groups \
                 WHERE env = ? \
             ) metric_groups \
             GROUP BY status, group_kind, group_strategy, service_mode",
            vec![Value::from(self.env.as_str())],
        ).await.map_err(|e| {
            warn!(env = %self.env, error = %e, "group metrics snapshot query failed");
            ServiceError::InternalError(format!("group metrics snapshot query failed: {}", e))
        })?;

        let mut counts = Vec::with_capacity(rows.len());
        for row in rows {
            let status_raw: String = db_get_column(&row, "status").map_err(|e| {
                ServiceError::InternalError(format!("group metrics status conversion failed: {}", e))
            })?;
            let group_kind_raw: String = db_get_column(&row, "group_kind").map_err(|e| {
                ServiceError::InternalError(format!("group metrics kind conversion failed: {}", e))
            })?;
            let service_mode_raw: String = db_get_column(&row, "service_mode").map_err(|e| {
                ServiceError::InternalError(format!(
                    "group metrics service_mode conversion failed: {}",
                    e
                ))
            })?;
            let group_strategy_raw: String = db_get_column(&row, "group_strategy").map_err(|e| {
                ServiceError::InternalError(format!(
                    "group metrics group_strategy conversion failed: {}",
                    e
                ))
            })?;
            let group_count: i64 = db_get_column(&row, "group_count").map_err(|e| {
                ServiceError::InternalError(format!("group metrics count conversion failed: {}", e))
            })?;
            let count = u64::try_from(group_count).map_err(|e| {
                ServiceError::InternalError(format!("group metrics count is invalid: {}", e))
            })?;
            if count == 0 {
                continue;
            }

            counts.push(GroupMetricCount {
                status: Self::str_to_status(&status_raw),
                kind: Self::parse_group_kind(Some(group_kind_raw.as_str())),
                group_strategy: Self::parse_group_strategy(Some(group_strategy_raw.as_str())),
                service_mode: sql_metric_service_mode_to_option(&service_mode_raw),
                count,
            });
        }

        Ok(counts)
    }
}

#[async_trait]
impl GroupRepoPort for MySqlGroupStore {
    /// Create or update a session.
    async fn upsert(&self, group: Group) -> ServiceResult<()> {
        let group_id = group.id.clone();
        let env = self.env.clone();
        let _start = std::time::Instant::now();

        let status_str = Self::status_to_str(&group.status);
        let routing_policy_json: Option<String> = group
            .routing_policy
            .as_ref()
            .and_then(|rp| serde_json::to_string(rp).ok());
        // Task G.2 / migration 005: persist `group_kind` + `dm_pair_key`.
        // - `group_kind` is always written (defaults to "normal" via the
        //   in-memory enum default, but we still write the explicit value
        //   so DB column reflects intent).
        // - `dm_pair_key` is `NULL` for normal groups; for dm groups, the
        //   `(env, dm_pair_key)` unique index (migration 005) guards
        //   against concurrent duplicate creation.
        // - We DO NOT update `group_kind` / `dm_pair_key` on conflict —
        //   these are immutable per-group identity attributes set at
        //   creation; allowing UPDATE would let a normal group silently
        //   become a dm or change pair, breaking F.7 / G.5 invariants.
        let group_kind_str = Self::group_kind_to_str(group.group_kind);

        // Pre-extract all values from `group` so the closure captures only
        // owned data (no partial moves of `group`).
        let g_id = group.id.clone();
        let g_label: Option<String> = group.label.clone();
        let g_driver_bot = group.driver_bot.clone();
        let g_originator: Option<String> = group.originator.clone();
        let g_context: Option<String> = group.context.clone();
        let g_dm_pair_key: Option<String> = group.dm_pair_key.clone();
        let g_group_strategy_str = Self::group_strategy_to_str(group.group_strategy);
        let g_service_group_uuid: Option<String> = group.service_group_uuid.clone();
        let g_service_mode: Option<String> = group.service_mode.clone();
        let g_service_spec_json: Option<String> = match group.service_spec {
            Some(ref spec) => Some(
                serde_json::to_string(spec)
                    .map_err(|e| ServiceError::InternalError(format!("service_spec: {e}")))?,
            ),
            None => None,
        };
        let g_version: i64 = group.version as i64;
        let g_record_status = group.record_status.clone();
        // Build participant tuples: (bot_uuid, role_str, actor_kind_str, mode_str)
        let g_participants: Vec<(String, &'static str, &'static str, &'static str)> = group
            .participants
            .iter()
            .map(|p| {
                (
                    p.bot_uuid.clone(),
                    Self::role_to_str(&p.role),
                    Self::actor_kind_to_str(p.actor_kind),
                    Self::mode_to_str(p.effective_mode()),
                )
            })
            .collect();

        let g_visibility = group.visibility.clone();

        let upsert_clause = self.flavor.on_conflict_update(
            &["group_id", "env"],
            &["label", "status", "driver_bot", "originator", "routing_policy_json",
              "context", "service_spec", "version", "record_status", "visibility"],
            &[("gmt_modified", self.flavor.now())],
        );
        let upsert_sql = format!(
            "INSERT INTO bcs_groups (group_id, label, status, driver_bot, originator, env, routing_policy_json, context, group_kind, dm_pair_key, group_strategy, service_group_uuid, service_mode, service_spec, version, record_status, visibility, gmt_create, gmt_modified) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {now}, {now}) \
             {upsert}",
            now = self.flavor.now(),
            upsert = upsert_clause,
        );

        let mut steps = Vec::with_capacity(2 + g_participants.len());
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            // 1. Upsert session metadata
                &upsert_sql,
                vec![
                    Value::from(g_id.as_str()),
                    Value::from(g_label.as_deref()),
                    Value::from(status_str),
                    Value::from(g_driver_bot.as_str()),
                    Value::from(g_originator.as_deref()),
                    Value::from(env.as_str()),
                    Value::from(routing_policy_json.as_deref()),
                    Value::from(g_context.as_deref()),
                    Value::from(group_kind_str),
                    Value::from(g_dm_pair_key.as_deref()),
                    Value::from(g_group_strategy_str),
                    Value::from(g_service_group_uuid.as_deref()),
                    Value::from(g_service_mode.as_deref()),
                    Value::from(g_service_spec_json.as_deref()),
                    Value::from(g_version),
                    Value::from(g_record_status.as_str()),
                    Value::from(g_visibility.as_str()),
                ],
        )));

        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            // 2. Delete existing participants
            "DELETE FROM bcs_group_participants WHERE group_id = ? AND env = ?",
            vec![Value::from(g_id.as_str()), Value::from(env.as_str())],
        )));

        // 3. Insert new participants.
        // Always populate actor_kind + mode explicitly per Requirement 3.10#2 / 3.18#6.
        for (bot_uuid, role_str, actor_kind_str, mode_str) in &g_participants {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                    "INSERT INTO bcs_group_participants (group_id, bot_uuid, role, env, actor_kind, mode) \
                     VALUES (?, ?, ?, ?, ?, ?)",
                    vec![
                        Value::from(g_id.as_str()),
                        Value::from(bot_uuid.as_str()),
                        Value::from(*role_str),
                        Value::from(env.as_str()),
                        Value::from(*actor_kind_str),
                        Value::from(*mode_str),
                    ],
            )));
        }

        self.db.plugin().transaction(steps).await.map_err(|e| {
            warn!(group_id = %group_id, error = %e, "upsert transaction failed");
            ServiceError::InternalError(e.to_string())
        })?;

        let elapsed = _start.elapsed();
        if elapsed.as_millis() > 100 {
            warn!(group_id = %group_id, duration_ms = %elapsed.as_millis(), "slow upsert");
        } else {
            info!(group_id = %group_id, duration_ms = %elapsed.as_millis(), "upsert");
        }
        // Update cache
        {
            let mut cache = self.cache.write().await;
            cache.insert(group_id, group);
        }
        Ok(())
    }

    /// Get a session by ID (cache-first, fallback to DB).
    async fn get(&self, id: &str) -> Option<Group> {
        // Check cache first
        {
            let cache = self.cache.read().await;
            if let Some(group) = cache.get(id) {
                return Some(group.clone());
            }
        }
        // Cache miss — load from DB and populate cache
        let group = self.load_group_from_mysql(id).await?;
        {
            let mut cache = self.cache.write().await;
            cache.insert(id.to_string(), group.clone());
        }
        Some(group)
    }

    /// Add a message to a session - NOT PERSISTED (memory only, lost on restart).
    async fn add_message(&self, id: &str, _message: GroupMessage) -> ServiceResult<()> {
        debug!(group_id = %id, "Message added to group (not persisted)");
        Ok(())
    }

    /// Add a participant to a session.
    async fn add_participant(&self, id: &str, participant: Participant) -> ServiceResult<()> {
        // Verify group exists
        if self.get(id).await.is_none() {
            return Err(ServiceError::GroupNotFound(id.to_string()));
        }

        // Check if already exists
        let check_sql =
            "SELECT 1 FROM bcs_group_participants WHERE group_id = ? AND bot_uuid = ? AND env = ?";

        let rows = self
            .db
            .query_with(
                &self.logical_db,
                check_sql,
                vec![
                    Value::from(id),
                    Value::from(participant.bot_uuid.as_str()),
                    Value::from(self.env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                ServiceError::InternalError(format!("Failed to check participant existence: {}", e))
            })?;

        if !rows.is_empty() {
            return Ok(()); // Already a participant, no-op
        }

        // Add to MySQL. Always populate actor_kind + mode explicitly
        // per Requirement 3.10#2 / 3.18#6.
        let role_str = Self::role_to_str(&participant.role);
        let actor_kind_str = Self::actor_kind_to_str(participant.actor_kind);
        let mode_str = Self::mode_to_str(participant.effective_mode());
        self.db.execute_with(
            &self.logical_db,
            "INSERT INTO bcs_group_participants (group_id, bot_uuid, role, env, actor_kind, mode) \
             VALUES (?, ?, ?, ?, ?, ?)",
            vec![
                Value::from(id),
                Value::from(participant.bot_uuid.as_str()),
                Value::from(role_str),
                Value::from(self.env.as_str()),
                Value::from(actor_kind_str),
                Value::from(mode_str),
            ],
        ).await
            .map_err(|e| {
                warn!(group_id = %id, bot_uuid = %participant.bot_uuid, error = %e, "Failed to add participant to MySQL");
                ServiceError::InternalError(e.to_string())
            })?;

        debug!(group_id = %id, bot_uuid = %participant.bot_uuid, "Participant added to group");
        // Invalidate cache
        self.cache.write().await.remove(id);
        Ok(())
    }

    async fn remove_participant(&self, group_id: &str, bot_uuid: &str) -> ServiceResult<()> {
        // Verify group exists
        if self.get(group_id).await.is_none() {
            return Err(ServiceError::GroupNotFound(group_id.to_string()));
        }

        let delete_sql =
            "DELETE FROM bcs_group_participants WHERE group_id = ? AND bot_uuid = ? AND env = ?";

        let affected = self
            .db
            .execute_with(
                &self.logical_db,
                delete_sql,
                vec![
                    Value::from(group_id),
                    Value::from(bot_uuid),
                    Value::from(self.env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(group_id = %group_id, bot_uuid = %bot_uuid, error = %e, "Failed to remove participant from MySQL");
                ServiceError::InternalError(e.to_string())
            })?;

        if affected == 0 {
            return Err(ServiceError::ParticipantNotFound(bot_uuid.to_string()));
        }

        debug!(group_id = %group_id, bot_uuid = %bot_uuid, "Participant removed from group");
        // Invalidate cache
        self.cache.write().await.remove(group_id);
        Ok(())
    }

    /// Update an existing participant's `mode` (Human Actor V1, Task P.1).
    async fn update_participant_mode(
        &self,
        id: &str,
        actor_id: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<()> {
        // Verify group exists.
        let group = self
            .get(id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;

        // Verify participant exists and capture current mode for idempotency check.
        let current_mode = group
            .participants
            .iter()
            .find(|p| p.bot_uuid == actor_id)
            .map(|p| p.effective_mode())
            .ok_or_else(|| ServiceError::BotNotFound(actor_id.to_string()))?;

        if current_mode == mode {
            debug!(group_id = %id, actor_id = %actor_id, ?mode, "Participant mode unchanged, skipping DB write");
            return Ok(());
        }

        let mode_str = Self::mode_to_str(mode);
        self.db.execute_with(
            &self.logical_db,
            "UPDATE bcs_group_participants SET mode = ? \
             WHERE group_id = ? AND bot_uuid = ? AND env = ?",
            vec![
                Value::from(mode_str),
                Value::from(id),
                Value::from(actor_id),
                Value::from(self.env.as_str()),
            ],
        ).await
            .map_err(|e| {
                warn!(group_id = %id, actor_id = %actor_id, error = %e, "Failed to update participant mode");
                ServiceError::InternalError(e.to_string())
            })?;

        debug!(group_id = %id, actor_id = %actor_id, ?mode, "Participant mode updated");
        // Invalidate cache so the next get() reloads with the new mode.
        self.cache.write().await.remove(id);
        Ok(())
    }

    /// Update workspace - NOT PERSISTED (memory only, lost on restart).
    async fn update_workspace(&self, id: &str, _workspace: Workspace) -> ServiceResult<()> {
        // Verify group exists
        if self.get(id).await.is_none() {
            return Err(ServiceError::GroupNotFound(id.to_string()));
        }

        debug!(group_id = %id, "Group workspace update ignored (not persisted)");
        Ok(())
    }

    /// Update session label.
    async fn update_label(&self, id: &str, label: Option<String>) -> ServiceResult<()> {
        // Verify group exists
        if self.get(id).await.is_none() {
            return Err(ServiceError::GroupNotFound(id.to_string()));
        }

        // Persist to MySQL using parameter binding
        self.db
            .execute_with(
                &self.logical_db,
                "UPDATE bcs_groups SET label = ? WHERE group_id = ? AND env = ?",
                vec![
                    Value::from(label.as_deref()),
                    Value::from(id),
                    Value::from(self.env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(group_id = %id, error = %e, "Failed to update group label");
                ServiceError::InternalError(e.to_string())
            })?;

        debug!(group_id = %id, "Group label updated");
        // Update cache
        {
            let mut cache = self.cache.write().await;
            if let Some(group) = cache.get_mut(id) {
                group.label = label;
            }
        }
        Ok(())
    }

    /// Update session status.
    async fn update_status(&self, id: &str, status: GroupStatus) -> ServiceResult<()> {
        // Verify group exists
        if self.get(id).await.is_none() {
            return Err(ServiceError::GroupNotFound(id.to_string()));
        }

        // Persist to MySQL using parameter binding
        let status_str = Self::status_to_str(&status);
        self.db.execute_with(
            &self.logical_db,
            "UPDATE bcs_groups SET status = ? WHERE group_id = ? AND env = ?",
            vec![
                Value::from(status_str),
                Value::from(id),
                Value::from(self.env.as_str()),
            ],
        ).await
            .map_err(|e| {
                warn!(group_id = %id, status = ?status, error = %e, "Failed to update group status");
                ServiceError::InternalError(e.to_string())
            })?;

        debug!(group_id = %id, status = ?status, "Group status updated");
        // Update cache
        {
            let mut cache = self.cache.write().await;
            if let Some(group) = cache.get_mut(id) {
                group.status = status;
            }
        }
        Ok(())
    }

    /// Persist a `service_spec` patch to MySQL. `Some(spec)` writes a JSON
    /// blob into the `service_spec` column; `None` clears the column. Caller
    /// is responsible for validation (route-field lock, callback_config
    /// immutability) — this method only writes.
    async fn update_service_spec(
        &self,
        id: &str,
        service_spec: Option<bcs_service_api::ServiceSpec>,
    ) -> ServiceResult<()> {
        // Verify group exists
        if self.get(id).await.is_none() {
            return Err(ServiceError::GroupNotFound(id.to_string()));
        }

        let spec_json = match service_spec.as_ref() {
            Some(s) => serde_json::to_string(s)
                .map_err(|e| ServiceError::InternalError(e.to_string()))?,
            None => String::new(),
        };
        let spec_value: Value = if service_spec.is_some() {
            Value::from(spec_json.as_str())
        } else {
            Value::Null
        };

        self.db
            .execute_with(
                &self.logical_db,
                "UPDATE bcs_groups SET service_spec = ? WHERE group_id = ? AND env = ?",
                vec![
                    spec_value,
                    Value::from(id),
                    Value::from(self.env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(group_id = %id, error = %e, "Failed to update group service_spec");
                ServiceError::InternalError(e.to_string())
            })?;

        debug!(group_id = %id, "Group service_spec updated");
        {
            let mut cache = self.cache.write().await;
            if let Some(group) = cache.get_mut(id) {
                group.service_spec = service_spec;
            }
        }
        Ok(())
    }

    /// Delete a session.
    async fn delete(&self, id: &str) -> ServiceResult<Option<Group>> {
        // Get group before deleting
        let group = self.get(id).await;

        // Delete from MySQL
        self.delete_group_from_mysql(id).await?;

        debug!(group_id = %id, "Group deleted");
        // Remove from cache
        self.cache.write().await.remove(id);

        Ok(group)
    }

    /// List all sessions.
    async fn list(&self) -> Vec<Group> {
        self.load_all_groups_from_mysql().await
    }

    /// List groups with pagination.
    async fn list_paginated(&self, offset: u64, limit: u64) -> Vec<Group> {
        // Subquery paginates groups first, then JOIN fetches participants.
        // LIMIT/OFFSET on the outer JOIN would paginate rows (not groups) due to fan-out.
        // Task G.2: project group_kind / dm_pair_key from both inner and outer
        // SELECT so dm groups stay tagged after pagination.
        let _start = std::time::Instant::now();
        let paginated_sql = format!(
            "SELECT gs.group_id, gs.label, gs.status, gs.driver_bot, gs.originator, \
                    gp.bot_uuid, gp.role, gs.routing_policy_json, gs.context, \
                    gs.service_group_uuid, gs.service_mode, gs.service_spec, gs.version, gs.record_status, \
                    gs.created_ts, gs.updated_ts, \
                    gp.actor_kind, gp.mode, gs.group_kind, gs.dm_pair_key, gs.group_strategy, gs.visibility \
             FROM (SELECT group_id, label, status, driver_bot, originator, routing_policy_json, context, \
                          service_group_uuid, service_mode, service_spec, version, record_status, \
                          {} AS created_ts, {} AS updated_ts, \
                          group_kind, dm_pair_key, group_strategy, visibility \
                   FROM bcs_groups WHERE env = ? LIMIT ? OFFSET ?) gs \
             LEFT JOIN bcs_group_participants gp ON gs.group_id = gp.group_id AND gp.env = ?",
            self.flavor.unix_ts("gmt_create"),
            self.flavor.unix_ts("gmt_modified"),
        );
        let rows = match self.db.query_with(
            &self.logical_db,
            &paginated_sql,
            vec![
                Value::from(self.env.as_str()),
                Value::from(limit as i64),
                Value::from(offset as i64),
                Value::from(self.env.as_str()),
            ],
        ).await
        {
            Ok(r) => {
                let elapsed = _start.elapsed();
                if elapsed.as_millis() > 100 {
                    warn!(duration_ms = %elapsed.as_millis(), rows = r.len(), offset = offset, limit = limit, "slow list_paginated");
                } else {
                    info!(duration_ms = %elapsed.as_millis(), rows = r.len(), offset = offset, limit = limit, "list_paginated");
                }
                r
            }
            Err(e) => {
                warn!(duration_ms = %_start.elapsed().as_millis(), error = %e, "list_paginated: query failed");
                return Vec::new();
            }
        };

        let mut groups_map: HashMap<String, Group> = HashMap::new();
        for row in &rows {
            let group_id: String = match db_get_column(row, "group_id") {
                Ok(v) => v,
                Err(_) => continue,
            };
            let entry = groups_map.entry(group_id.clone()).or_insert_with(|| {
                let label: Option<String> = db_get_column_opt(row, "label").ok().flatten();
                let status_str: String = db_get_column(row, "status").unwrap_or_default();
                let driver_bot: String = db_get_column(row, "driver_bot").unwrap_or_default();
                let originator: Option<String> =
                    db_get_column_opt(row, "originator").ok().flatten();
                let routing_policy_json: Option<String> =
                    db_get_column_opt(row, "routing_policy_json").ok().flatten();
                let context: Option<String> = db_get_column_opt(row, "context").ok().flatten();
                let service_group_uuid: Option<String> =
                    db_get_column_opt(row, "service_group_uuid").ok().flatten();
                let service_mode: Option<String> =
                    db_get_column_opt(row, "service_mode").ok().flatten();
                let service_spec_json: Option<String> =
                    db_get_column_opt(row, "service_spec").ok().flatten();
                let service_spec: Option<bcs_service_api::ServiceSpec> =
                    match service_spec_json.as_deref() {
                        Some(s) if !s.is_empty() => serde_json::from_str(s).ok(),
                        _ => None,
                    };
                let version: i32 = db_get_column_opt::<i64>(row, "version")
                    .ok()
                    .flatten()
                    .unwrap_or(1) as i32;
                let record_status: String = db_get_column_opt(row, "record_status")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "active".to_string());
                let created_ts: Option<i64> = db_get_column_opt(row, "created_ts").ok().flatten();
                let updated_ts: Option<i64> = db_get_column_opt(row, "updated_ts").ok().flatten();
                let group_kind_str: Option<String> =
                    db_get_column_opt(row, "group_kind").ok().flatten();
                let dm_pair_key: Option<String> =
                    db_get_column_opt(row, "dm_pair_key").ok().flatten();
                let group_strategy_str: Option<String> =
                    db_get_column_opt(row, "group_strategy").ok().flatten();
                let group_strategy = Self::parse_group_strategy(group_strategy_str.as_deref());
                let visibility: String = db_get_column_opt(row, "visibility")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "private".to_string());

                Group {
                    id: group_id,
                    label,
                    status: Self::str_to_status(&status_str),
                    driver_bot,
                    originator,
                    routing_policy: Self::deserialize_routing_policy(routing_policy_json),
                    context,
                    participants: Vec::new(),
                    messages: Vec::new(),
                    workspace: Workspace::default(),
                    service_group_uuid,
                    service_mode,
                    created_at: Self::seconds_to_millis(created_ts),
                    updated_at: Self::seconds_to_millis(updated_ts),
                    group_kind: Self::parse_group_kind(group_kind_str.as_deref()),
                    dm_pair_key,
                    group_strategy,
                    service_spec,
                    version,
                    record_status,
                    visibility,
                }
            });
            if let (Ok(bot_uuid), Ok(role_str)) = (
                db_get_column::<String>(row, "bot_uuid"),
                db_get_column::<String>(row, "role"),
            ) {
                if !entry.participants.iter().any(|p| p.bot_uuid == bot_uuid) {
                    let actor_kind_str: Option<String> =
                        db_get_column_opt(row, "actor_kind").ok().flatten();
                    let mode_str: Option<String> = db_get_column_opt(row, "mode").ok().flatten();
                    let (actor_kind, mode) = Self::normalize_kind_mode(
                        &entry.id,
                        &bot_uuid,
                        self.env.as_str(),
                        actor_kind_str.as_deref(),
                        mode_str.as_deref(),
                    );
                    entry.participants.push(Participant {
                        bot_uuid,
                        bot_name: None,
                        kind: Some(ParticipantKind::Bot),
                        role: Self::str_to_role(&role_str),
                        actor_kind,
                        mode: Some(mode),
                    });
                }
            }
        }
        let mut groups = groups_map.into_values().collect::<Vec<_>>();
        Group::sort_by_updated_at_desc(&mut groups);
        groups
    }

    /// Find all groups where the given bot is a participant.
    /// Uses a single JOIN query instead of 3 serial queries to reduce cursor usage.
    async fn find_by_participant(&self, bot_uuid: &str) -> Vec<Group> {
        info!(
            bot_uuid = %bot_uuid,
            env = %self.env,
            logical_db = %self.logical_db,
            "find_by_participant: starting query"
        );

        // Single JOIN query: find groups + details + all participants in one shot.
        // Task G.2: also project group_kind / dm_pair_key for dm group tagging.
        let sql = format!(
            "SELECT \
                gs.group_id, gs.label, gs.status, gs.driver_bot, gs.originator, \
                gp2.bot_uuid AS p_bot_uuid, gp2.role AS p_role, gs.routing_policy_json, gs.context, \
                gs.service_group_uuid, gs.service_mode, gs.service_spec, gs.version, gs.record_status, \
                {} AS created_ts, {} AS updated_ts, \
                gp2.actor_kind AS p_actor_kind, gp2.mode AS p_mode, \
                gs.group_kind AS g_group_kind, gs.dm_pair_key AS g_dm_pair_key, gs.group_strategy, gs.visibility \
             FROM bcs_group_participants gp \
             JOIN bcs_groups gs ON gp.group_id = gs.group_id AND gs.env = ? \
             JOIN bcs_group_participants gp2 ON gs.group_id = gp2.group_id AND gp2.env = ? \
             WHERE gp.bot_uuid = ? AND gp.env = ?",
            self.flavor.unix_ts("gs.gmt_create"),
            self.flavor.unix_ts("gs.gmt_modified"),
        );

        info!(
            bot_uuid = %bot_uuid,
            "find_by_participant: executing query"
        );

        let env = self.env.as_str();
        let rows = match self
            .db
            .query_with(
                &self.logical_db,
                &sql,
                vec![
                    Value::from(env),
                    Value::from(env),
                    Value::from(bot_uuid),
                    Value::from(env),
                ],
            )
            .await
        {
            Ok(r) => {
                info!(
                    row_count = r.len(),
                    "find_by_participant: query returned rows"
                );
                r
            }
            Err(e) => {
                warn!(error = %e, "find_by_participant: query failed");
                return Vec::new();
            }
        };

        // Aggregate flat rows into Groups by group_id
        let mut groups_map: HashMap<String, Group> = HashMap::new();

        for row in &rows {
            let group_id: String = match db_get_column(row, "group_id") {
                Ok(v) => v,
                Err(_) => continue,
            };

            let entry = groups_map.entry(group_id.clone()).or_insert_with(|| {
                let label: Option<String> = db_get_column_opt(row, "label").ok().flatten();
                let status_str: String = db_get_column(row, "status").unwrap_or_default();
                let driver_bot: String = db_get_column(row, "driver_bot").unwrap_or_default();
                let originator: Option<String> =
                    db_get_column_opt(row, "originator").ok().flatten();
                let routing_policy_json: Option<String> =
                    db_get_column_opt(row, "routing_policy_json").ok().flatten();
                let context: Option<String> = db_get_column_opt(row, "context").ok().flatten();
                let service_group_uuid: Option<String> =
                    db_get_column_opt(row, "service_group_uuid").ok().flatten();
                let service_mode: Option<String> =
                    db_get_column_opt(row, "service_mode").ok().flatten();
                let service_spec_json: Option<String> =
                    db_get_column_opt(row, "service_spec").ok().flatten();
                let service_spec: Option<bcs_service_api::ServiceSpec> =
                    match service_spec_json.as_deref() {
                        Some(s) if !s.is_empty() => serde_json::from_str(s).ok(),
                        _ => None,
                    };
                let version: i32 = db_get_column_opt::<i64>(row, "version")
                    .ok()
                    .flatten()
                    .unwrap_or(1) as i32;
                let record_status: String = db_get_column_opt(row, "record_status")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "active".to_string());
                let created_ts: Option<i64> = db_get_column_opt(row, "created_ts").ok().flatten();
                let updated_ts: Option<i64> = db_get_column_opt(row, "updated_ts").ok().flatten();
                let group_kind_str: Option<String> =
                    db_get_column_opt(row, "g_group_kind").ok().flatten();
                let dm_pair_key: Option<String> =
                    db_get_column_opt(row, "g_dm_pair_key").ok().flatten();
                let group_strategy_str: Option<String> =
                    db_get_column_opt(row, "group_strategy").ok().flatten();
                let group_strategy = Self::parse_group_strategy(group_strategy_str.as_deref());
                let visibility: String = db_get_column_opt(row, "visibility")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "private".to_string());

                Group {
                    id: group_id,
                    label,
                    status: Self::str_to_status(&status_str),
                    driver_bot,
                    originator,
                    routing_policy: Self::deserialize_routing_policy(routing_policy_json),
                    context,
                    participants: Vec::new(),
                    messages: Vec::new(),
                    workspace: Workspace::default(),
                    service_group_uuid,
                    service_mode,
                    created_at: Self::seconds_to_millis(created_ts),
                    updated_at: Self::seconds_to_millis(updated_ts),
                    group_kind: Self::parse_group_kind(group_kind_str.as_deref()),
                    dm_pair_key,
                    group_strategy,
                    service_spec,
                    version,
                    record_status,
                    visibility,
                }
            });

            // Add participant (deduplicate by bot_uuid)
            if let (Ok(p_bot_uuid), Ok(p_role)) = (
                db_get_column::<String>(row, "p_bot_uuid"),
                db_get_column::<String>(row, "p_role"),
            ) {
                if !entry.participants.iter().any(|p| p.bot_uuid == p_bot_uuid) {
                    let actor_kind_str: Option<String> =
                        db_get_column_opt(row, "p_actor_kind").ok().flatten();
                    let mode_str: Option<String> = db_get_column_opt(row, "p_mode").ok().flatten();
                    let (actor_kind, mode) = Self::normalize_kind_mode(
                        &entry.id,
                        &p_bot_uuid,
                        self.env.as_str(),
                        actor_kind_str.as_deref(),
                        mode_str.as_deref(),
                    );
                    entry.participants.push(Participant {
                        bot_uuid: p_bot_uuid,
                        bot_name: None,
                        kind: Some(ParticipantKind::Bot),
                        role: Self::str_to_role(&p_role),
                        actor_kind,
                        mode: Some(mode),
                    });
                }
            }
        }

        let mut result = groups_map.into_values().collect::<Vec<_>>();
        Group::sort_by_updated_at_desc(&mut result);

        info!(
            bot_uuid = %bot_uuid,
            result_count = result.len(),
            "find_by_participant: completed"
        );

        result
    }

    async fn find_by_participant_filtered(
        &self,
        bot_uuid: &str,
        kind: Option<bcs_service_api::GroupKind>,
        label_query: Option<&str>,
    ) -> Vec<Group> {
        info!(
            bot_uuid = %bot_uuid,
            env = %self.env,
            has_group_kind = kind.is_some(),
            has_label_query = label_query.map(str::trim).is_some_and(|q| !q.is_empty()),
            "find_by_participant_filtered: starting query"
        );

        let mut sql = format!(
            "SELECT \
                gs.group_id, gs.label, gs.status, gs.driver_bot, gs.originator, \
                gp2.bot_uuid AS p_bot_uuid, gp2.role AS p_role, gs.routing_policy_json, gs.context, \
                gs.service_group_uuid, gs.service_mode, gs.service_spec, gs.version, gs.record_status, \
                {} AS created_ts, {} AS updated_ts, \
                gp2.actor_kind AS p_actor_kind, gp2.mode AS p_mode, \
                gs.group_kind AS g_group_kind, gs.dm_pair_key AS g_dm_pair_key, gs.group_strategy, gs.visibility \
             FROM bcs_group_participants gp \
             JOIN bcs_groups gs ON gp.group_id = gs.group_id AND gs.env = ? \
             JOIN bcs_group_participants gp2 ON gs.group_id = gp2.group_id AND gp2.env = ? \
             WHERE gp.bot_uuid = ? AND gp.env = ?",
            self.flavor.unix_ts("gs.gmt_create"),
            self.flavor.unix_ts("gs.gmt_modified"),
        );

        let env = self.env.as_str();
        let mut params = vec![
            Value::from(env),
            Value::from(env),
            Value::from(bot_uuid),
            Value::from(env),
        ];
        if let Some(kind) = kind {
            sql.push_str(" AND gs.group_kind = ?");
            params.push(Value::from(Self::group_kind_to_str(kind)));
        }
        if let Some(query) = label_query.map(str::trim).filter(|q| !q.is_empty()) {
            sql.push_str(" AND LOWER(COALESCE(gs.label, '')) LIKE ?");
            params.push(Value::from(format!("%{}%", query.to_lowercase())));
        }

        let rows = match self.db.query_with(&self.logical_db, &sql, params).await {
            Ok(r) => {
                info!(
                    row_count = r.len(),
                    "find_by_participant_filtered: query returned rows"
                );
                r
            }
            Err(e) => {
                warn!(error = %e, "find_by_participant_filtered: query failed");
                return Vec::new();
            }
        };

        let mut groups_map: HashMap<String, Group> = HashMap::new();

        for row in &rows {
            let group_id: String = match db_get_column(row, "group_id") {
                Ok(v) => v,
                Err(_) => continue,
            };

            let entry = groups_map.entry(group_id.clone()).or_insert_with(|| {
                let label: Option<String> = db_get_column_opt(row, "label").ok().flatten();
                let status_str: String = db_get_column(row, "status").unwrap_or_default();
                let driver_bot: String = db_get_column(row, "driver_bot").unwrap_or_default();
                let originator: Option<String> =
                    db_get_column_opt(row, "originator").ok().flatten();
                let routing_policy_json: Option<String> =
                    db_get_column_opt(row, "routing_policy_json").ok().flatten();
                let context: Option<String> = db_get_column_opt(row, "context").ok().flatten();
                let service_group_uuid: Option<String> =
                    db_get_column_opt(row, "service_group_uuid").ok().flatten();
                let service_mode: Option<String> =
                    db_get_column_opt(row, "service_mode").ok().flatten();
                let service_spec_json: Option<String> =
                    db_get_column_opt(row, "service_spec").ok().flatten();
                let service_spec: Option<bcs_service_api::ServiceSpec> =
                    match service_spec_json.as_deref() {
                        Some(s) if !s.is_empty() => serde_json::from_str(s).ok(),
                        _ => None,
                    };
                let version: i32 = db_get_column_opt::<i64>(row, "version")
                    .ok()
                    .flatten()
                    .unwrap_or(1) as i32;
                let record_status: String = db_get_column_opt(row, "record_status")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "active".to_string());
                let created_ts: Option<i64> = db_get_column_opt(row, "created_ts").ok().flatten();
                let updated_ts: Option<i64> = db_get_column_opt(row, "updated_ts").ok().flatten();
                let group_kind_str: Option<String> =
                    db_get_column_opt(row, "g_group_kind").ok().flatten();
                let dm_pair_key: Option<String> =
                    db_get_column_opt(row, "g_dm_pair_key").ok().flatten();
                let group_strategy_str: Option<String> =
                    db_get_column_opt(row, "group_strategy").ok().flatten();
                let group_strategy = Self::parse_group_strategy(group_strategy_str.as_deref());
                let visibility: String = db_get_column_opt(row, "visibility")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "private".to_string());

                Group {
                    id: group_id,
                    label,
                    status: Self::str_to_status(&status_str),
                    driver_bot,
                    originator,
                    routing_policy: Self::deserialize_routing_policy(routing_policy_json),
                    context,
                    participants: Vec::new(),
                    messages: Vec::new(),
                    workspace: Workspace::default(),
                    service_group_uuid,
                    service_mode,
                    created_at: Self::seconds_to_millis(created_ts),
                    updated_at: Self::seconds_to_millis(updated_ts),
                    group_kind: Self::parse_group_kind(group_kind_str.as_deref()),
                    dm_pair_key,
                    group_strategy,
                    service_spec,
                    version,
                    record_status,
                    visibility,
                }
            });

            if let (Ok(p_bot_uuid), Ok(p_role)) = (
                db_get_column::<String>(row, "p_bot_uuid"),
                db_get_column::<String>(row, "p_role"),
            ) {
                if !entry.participants.iter().any(|p| p.bot_uuid == p_bot_uuid) {
                    let actor_kind_str: Option<String> =
                        db_get_column_opt(row, "p_actor_kind").ok().flatten();
                    let mode_str: Option<String> = db_get_column_opt(row, "p_mode").ok().flatten();
                    let (actor_kind, mode) = Self::normalize_kind_mode(
                        &entry.id,
                        &p_bot_uuid,
                        self.env.as_str(),
                        actor_kind_str.as_deref(),
                        mode_str.as_deref(),
                    );
                    entry.participants.push(Participant {
                        bot_uuid: p_bot_uuid,
                        bot_name: None,
                        kind: Some(ParticipantKind::Bot),
                        role: Self::str_to_role(&p_role),
                        actor_kind,
                        mode: Some(mode),
                    });
                }
            }
        }

        let mut result = groups_map.into_values().collect::<Vec<_>>();
        Group::sort_by_updated_at_desc(&mut result);

        info!(
            bot_uuid = %bot_uuid,
            result_count = result.len(),
            "find_by_participant_filtered: completed"
        );

        result
    }

    /// Count all groups.
    async fn count(&self) -> u64 {
        let rows = self
            .db
            .query_with(
                &self.logical_db,
                "SELECT COUNT(*) as cnt FROM bcs_groups WHERE env = ?",
                vec![Value::from(self.env.as_str())],
            )
            .await
            .unwrap_or_default();
        rows.first()
            .and_then(|row| db_get_column::<i64>(row, "cnt").ok())
            .unwrap_or(0) as u64
    }

    /// CR-4: count groups optionally filtered by `group_kind`.
    ///
    /// Pushes the filter down to a `SELECT COUNT(*)` so callers paging
    /// through `kind=dm` see a `total` consistent with their page contents
    /// (the previous default in-memory filter returned the all-kinds total
    /// which made the X-of-Y display lie for filtered queries).
    async fn count_by_kind(&self, kind: Option<bcs_service_api::GroupKind>) -> u64 {
        let rows =
            match kind {
                None => self
                    .db
                    .query_with(
                        &self.logical_db,
                        "SELECT COUNT(*) as cnt FROM bcs_groups WHERE env = ?",
                        vec![Value::from(self.env.as_str())],
                    )
                    .await
                    .unwrap_or_default(),
                Some(k) => {
                    let kind_str = Self::group_kind_to_str(k);
                    self.db.query_with(
                    &self.logical_db,
                    "SELECT COUNT(*) as cnt FROM bcs_groups WHERE env = ? AND group_kind = ?",
                    vec![Value::from(self.env.as_str()), Value::from(kind_str)],
                ).await.unwrap_or_default()
                }
            };
        rows.first()
            .and_then(|row| db_get_column::<i64>(row, "cnt").ok())
            .unwrap_or(0) as u64
    }

    /// CR-4: paginate groups optionally filtered by `group_kind`.
    ///
    /// Pushes the filter into the inner subquery (which is what `LIMIT` /
    /// `OFFSET` apply to), so callers paging through `kind=dm` get a stable
    /// page of dm groups regardless of how many normal groups precede them
    /// in scan order. The legacy in-memory post-filter could return a
    /// short or empty page even when more matching rows existed further
    /// in the table.
    async fn list_paginated_by_kind(
        &self,
        kind: Option<bcs_service_api::GroupKind>,
        offset: u64,
        limit: u64,
    ) -> Vec<Group> {
        // We share the same SELECT shape as `list_paginated` (subquery
        // paginates groups → outer JOIN fetches participants) but conditionally
        // append `AND group_kind = ?` to the inner WHERE. This keeps the
        // same row → Group reduction logic below.
        let created_ts_expr = self.flavor.unix_ts("gmt_create");
        let updated_ts_expr = self.flavor.unix_ts("gmt_modified");
        let rows_result = match kind {
            None => {
                let sql = format!(
                    "SELECT gs.group_id, gs.label, gs.status, gs.driver_bot, gs.originator, \
                            gp.bot_uuid, gp.role, gs.routing_policy_json, gs.context, \
                            gs.service_group_uuid, gs.service_mode, gs.service_spec, gs.version, gs.record_status, \
                            gs.created_ts, gs.updated_ts, \
                            gp.actor_kind, gp.mode, gs.group_kind, gs.dm_pair_key, gs.group_strategy, gs.visibility \
                     FROM (SELECT group_id, label, status, driver_bot, originator, routing_policy_json, context, \
                                  service_group_uuid, service_mode, service_spec, version, record_status, \
                                  {} AS created_ts, {} AS updated_ts, \
                                  group_kind, dm_pair_key, group_strategy, visibility \
                           FROM bcs_groups WHERE env = ? LIMIT ? OFFSET ?) gs \
                     LEFT JOIN bcs_group_participants gp ON gs.group_id = gp.group_id AND gp.env = ?",
                    created_ts_expr, updated_ts_expr,
                );
                self.db.query_with(
                    &self.logical_db,
                    &sql,
                    vec![
                        Value::from(self.env.as_str()),
                        Value::from(limit as i64),
                        Value::from(offset as i64),
                        Value::from(self.env.as_str()),
                    ],
                ).await
            }
            Some(k) => {
                let kind_str = Self::group_kind_to_str(k);
                let sql = format!(
                    "SELECT gs.group_id, gs.label, gs.status, gs.driver_bot, gs.originator, \
                            gp.bot_uuid, gp.role, gs.routing_policy_json, gs.context, \
                            gs.service_group_uuid, gs.service_mode, gs.service_spec, gs.version, gs.record_status, \
                            gs.created_ts, gs.updated_ts, \
                            gp.actor_kind, gp.mode, gs.group_kind, gs.dm_pair_key, gs.group_strategy, gs.visibility \
                     FROM (SELECT group_id, label, status, driver_bot, originator, routing_policy_json, context, \
                                  service_group_uuid, service_mode, service_spec, version, record_status, \
                                  {} AS created_ts, {} AS updated_ts, \
                                  group_kind, dm_pair_key, group_strategy, visibility \
                           FROM bcs_groups WHERE env = ? AND group_kind = ? LIMIT ? OFFSET ?) gs \
                     LEFT JOIN bcs_group_participants gp ON gs.group_id = gp.group_id AND gp.env = ?",
                    created_ts_expr, updated_ts_expr,
                );
                self.db.query_with(
                    &self.logical_db,
                    &sql,
                    vec![
                        Value::from(self.env.as_str()),
                        Value::from(kind_str),
                        Value::from(limit as i64),
                        Value::from(offset as i64),
                        Value::from(self.env.as_str()),
                    ],
                ).await
            }
        };

        let rows = match rows_result {
            Ok(r) => r,
            Err(e) => {
                warn!(?kind, error = %e, "list_paginated_by_kind: query failed");
                return Vec::new();
            }
        };

        // Identical row → Group aggregation as `list_paginated`.
        let mut groups_map: HashMap<String, Group> = HashMap::new();
        for row in &rows {
            let group_id: String = match db_get_column(row, "group_id") {
                Ok(v) => v,
                Err(_) => continue,
            };
            let entry = groups_map.entry(group_id.clone()).or_insert_with(|| {
                let label: Option<String> = db_get_column_opt(row, "label").ok().flatten();
                let status_str: String = db_get_column(row, "status").unwrap_or_default();
                let driver_bot: String = db_get_column(row, "driver_bot").unwrap_or_default();
                let originator: Option<String> =
                    db_get_column_opt(row, "originator").ok().flatten();
                let routing_policy_json: Option<String> =
                    db_get_column_opt(row, "routing_policy_json").ok().flatten();
                let context: Option<String> = db_get_column_opt(row, "context").ok().flatten();
                let service_group_uuid: Option<String> =
                    db_get_column_opt(row, "service_group_uuid").ok().flatten();
                let service_mode: Option<String> =
                    db_get_column_opt(row, "service_mode").ok().flatten();
                let service_spec_json: Option<String> =
                    db_get_column_opt(row, "service_spec").ok().flatten();
                let service_spec: Option<bcs_service_api::ServiceSpec> =
                    match service_spec_json.as_deref() {
                        Some(s) if !s.is_empty() => serde_json::from_str(s).ok(),
                        _ => None,
                    };
                let version: i32 = db_get_column_opt::<i64>(row, "version")
                    .ok()
                    .flatten()
                    .unwrap_or(1) as i32;
                let record_status: String = db_get_column_opt(row, "record_status")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "active".to_string());
                let created_ts: Option<i64> = db_get_column_opt(row, "created_ts").ok().flatten();
                let updated_ts: Option<i64> = db_get_column_opt(row, "updated_ts").ok().flatten();
                let group_kind_str: Option<String> =
                    db_get_column_opt(row, "group_kind").ok().flatten();
                let dm_pair_key: Option<String> =
                    db_get_column_opt(row, "dm_pair_key").ok().flatten();
                let group_strategy_str: Option<String> =
                    db_get_column_opt(row, "group_strategy").ok().flatten();
                let group_strategy = Self::parse_group_strategy(group_strategy_str.as_deref());
                let visibility: String = db_get_column_opt(row, "visibility")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "private".to_string());

                Group {
                    id: group_id,
                    label,
                    status: Self::str_to_status(&status_str),
                    driver_bot,
                    originator,
                    routing_policy: Self::deserialize_routing_policy(routing_policy_json),
                    context,
                    participants: Vec::new(),
                    messages: Vec::new(),
                    workspace: Workspace::default(),
                    service_group_uuid,
                    service_mode,
                    created_at: Self::seconds_to_millis(created_ts),
                    updated_at: Self::seconds_to_millis(updated_ts),
                    group_kind: Self::parse_group_kind(group_kind_str.as_deref()),
                    dm_pair_key,
                    group_strategy,
                    service_spec,
                    version,
                    record_status,
                    visibility,
                }
            });
            if let (Ok(bot_uuid), Ok(role_str)) = (
                db_get_column::<String>(row, "bot_uuid"),
                db_get_column::<String>(row, "role"),
            ) {
                if !entry.participants.iter().any(|p| p.bot_uuid == bot_uuid) {
                    let actor_kind_str: Option<String> =
                        db_get_column_opt(row, "actor_kind").ok().flatten();
                    let mode_str: Option<String> = db_get_column_opt(row, "mode").ok().flatten();
                    let (actor_kind, mode) = Self::normalize_kind_mode(
                        &entry.id,
                        &bot_uuid,
                        self.env.as_str(),
                        actor_kind_str.as_deref(),
                        mode_str.as_deref(),
                    );
                    entry.participants.push(Participant {
                        bot_uuid,
                        bot_name: None,
                        kind: Some(ParticipantKind::Bot),
                        role: Self::str_to_role(&role_str),
                        actor_kind,
                        mode: Some(mode),
                    });
                }
            }
        }
        let mut groups = groups_map.into_values().collect::<Vec<_>>();
        Group::sort_by_updated_at_desc(&mut groups);
        groups
    }

    /// Count groups where the given bot is a participant.
    async fn count_by_participant(&self, bot_uuid: &str) -> u64 {
        let rows = self
            .db
            .query_with(
                &self.logical_db,
                "SELECT COUNT(DISTINCT gs.group_id) as cnt \
             FROM bcs_groups gs \
             JOIN bcs_group_participants gp ON gs.group_id = gp.group_id AND gp.env = ? \
             WHERE gp.bot_uuid = ? AND gs.env = ?",
                vec![
                    Value::from(self.env.as_str()),
                    Value::from(bot_uuid),
                    Value::from(self.env.as_str()),
                ],
            )
            .await
            .unwrap_or_default();
        rows.first()
            .and_then(|row| db_get_column::<i64>(row, "cnt").ok())
            .unwrap_or(0) as u64
    }

    /// Find groups by participant with pagination.
    async fn find_by_participant_paginated(
        &self,
        bot_uuid: &str,
        offset: u64,
        limit: u64,
    ) -> Vec<Group> {
        debug!(
            "find_by_participant_paginated: bot_uuid={} limit={} offset={}",
            bot_uuid, limit, offset
        );

        // Subquery paginates groups first, then JOIN fetches all participants.
        // LIMIT/OFFSET on the outer JOIN would paginate rows (not groups) due to fan-out.
        // Task G.2: project group_kind / dm_pair_key from both inner DISTINCT
        // and outer SELECT so dm groups remain tagged through pagination.
        let participant_paginated_sql = format!(
            "SELECT gs.group_id, gs.label, gs.status, gs.driver_bot, gs.originator, \
                    gp2.bot_uuid, gp2.role, gs.routing_policy_json, gs.context, \
                    gs.service_group_uuid, gs.service_mode, gs.service_spec, gs.version, gs.record_status, \
                    gs.created_ts, gs.updated_ts, \
                    gp2.actor_kind, gp2.mode, gs.group_kind, gs.dm_pair_key, gs.group_strategy, gs.visibility \
             FROM (SELECT DISTINCT g.group_id, g.label, g.status, g.driver_bot, g.originator, g.routing_policy_json, g.context, \
                          g.service_group_uuid, g.service_mode, g.service_spec, g.version, g.record_status, \
                          {} AS created_ts, {} AS updated_ts, \
                          g.group_kind, g.dm_pair_key, g.group_strategy, g.visibility \
                   FROM bcs_groups g \
                   JOIN bcs_group_participants gp ON g.group_id = gp.group_id AND gp.env = ? \
                   WHERE gp.bot_uuid = ? AND g.env = ? \
                   ORDER BY updated_ts DESC, group_id ASC LIMIT ? OFFSET ?) gs \
             LEFT JOIN bcs_group_participants gp2 ON gs.group_id = gp2.group_id AND gp2.env = ?",
            self.flavor.unix_ts("g.gmt_create"),
            self.flavor.unix_ts("g.gmt_modified"),
        );
        let detail_rows = match self.db.query_with(
            &self.logical_db,
            &participant_paginated_sql,
            vec![
                Value::from(self.env.as_str()),
                Value::from(bot_uuid),
                Value::from(self.env.as_str()),
                Value::from(limit as i64),
                Value::from(offset as i64),
                Value::from(self.env.as_str()),
            ],
        ).await
        {
            Ok(r) => r,
            Err(e) => {
                error!("find_by_participant_paginated: failed to load group details for bot_uuid={}: {:?}", bot_uuid, e);
                return Vec::new();
            }
        };

        // Aggregate flat rows into Groups by group_id
        let mut groups_map: HashMap<String, Group> = HashMap::new();
        for row in &detail_rows {
            let group_id: String = match db_get_column(row, "group_id") {
                Ok(v) => v,
                Err(_) => continue,
            };
            let entry = groups_map.entry(group_id.clone()).or_insert_with(|| {
                let label: Option<String> = db_get_column_opt(row, "label").ok().flatten();
                let status_str: String = db_get_column(row, "status").unwrap_or_default();
                let driver_bot: String = db_get_column(row, "driver_bot").unwrap_or_default();
                let originator: Option<String> =
                    db_get_column_opt(row, "originator").ok().flatten();
                let routing_policy_json: Option<String> =
                    db_get_column_opt(row, "routing_policy_json").ok().flatten();
                let context: Option<String> = db_get_column_opt(row, "context").ok().flatten();
                let service_group_uuid: Option<String> =
                    db_get_column_opt(row, "service_group_uuid").ok().flatten();
                let service_mode: Option<String> =
                    db_get_column_opt(row, "service_mode").ok().flatten();
                let service_spec_json: Option<String> =
                    db_get_column_opt(row, "service_spec").ok().flatten();
                let service_spec: Option<bcs_service_api::ServiceSpec> =
                    match service_spec_json.as_deref() {
                        Some(s) if !s.is_empty() => serde_json::from_str(s).ok(),
                        _ => None,
                    };
                let version: i32 = db_get_column_opt::<i64>(row, "version")
                    .ok()
                    .flatten()
                    .unwrap_or(1) as i32;
                let record_status: String = db_get_column_opt(row, "record_status")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "active".to_string());
                let created_ts: Option<i64> = db_get_column_opt(row, "created_ts").ok().flatten();
                let updated_ts: Option<i64> = db_get_column_opt(row, "updated_ts").ok().flatten();
                let group_kind_str: Option<String> =
                    db_get_column_opt(row, "group_kind").ok().flatten();
                let dm_pair_key: Option<String> =
                    db_get_column_opt(row, "dm_pair_key").ok().flatten();
                let group_strategy_str: Option<String> =
                    db_get_column_opt(row, "group_strategy").ok().flatten();
                let group_strategy = Self::parse_group_strategy(group_strategy_str.as_deref());
                let visibility: String = db_get_column_opt(row, "visibility")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "private".to_string());

                Group {
                    id: group_id,
                    label,
                    status: Self::str_to_status(&status_str),
                    driver_bot,
                    originator,
                    routing_policy: Self::deserialize_routing_policy(routing_policy_json),
                    context,
                    participants: Vec::new(),
                    messages: Vec::new(),
                    workspace: Workspace::default(),
                    service_group_uuid,
                    service_mode,
                    created_at: Self::seconds_to_millis(created_ts),
                    updated_at: Self::seconds_to_millis(updated_ts),
                    group_kind: Self::parse_group_kind(group_kind_str.as_deref()),
                    dm_pair_key,
                    group_strategy,
                    service_spec,
                    version,
                    record_status,
                    visibility,
                }
            });
            if let (Ok(p_bot_uuid), Ok(p_role)) = (
                db_get_column::<String>(row, "bot_uuid"),
                db_get_column::<String>(row, "role"),
            ) {
                if !entry.participants.iter().any(|p| p.bot_uuid == p_bot_uuid) {
                    let actor_kind_str: Option<String> =
                        db_get_column_opt(row, "actor_kind").ok().flatten();
                    let mode_str: Option<String> = db_get_column_opt(row, "mode").ok().flatten();
                    let (actor_kind, mode) = Self::normalize_kind_mode(
                        &entry.id,
                        &p_bot_uuid,
                        self.env.as_str(),
                        actor_kind_str.as_deref(),
                        mode_str.as_deref(),
                    );
                    entry.participants.push(Participant {
                        bot_uuid: p_bot_uuid,
                        bot_name: None,
                        kind: Some(ParticipantKind::Bot),
                        role: Self::str_to_role(&p_role),
                        actor_kind,
                        mode: Some(mode),
                    });
                }
            }
        }
        let mut groups = groups_map.into_values().collect::<Vec<_>>();
        Group::sort_by_updated_at_desc(&mut groups);
        groups
    }

    /// Messages are not persisted in MySQL; count is tracked in memory.
    async fn message_count(&self, id: &str) -> ServiceResult<usize> {
        let counts = self.message_counts.read().await;
        Ok(counts.get(id).copied().unwrap_or(0))
    }

    async fn increment_message_count(&self, id: &str) -> ServiceResult<()> {
        let mut counts = self.message_counts.write().await;
        *counts.entry(id.to_string()).or_insert(0) += 1;
        Ok(())
    }

    async fn reset_message_count(&self, id: &str) -> ServiceResult<()> {
        let mut counts = self.message_counts.write().await;
        counts.insert(id.to_string(), 0);
        Ok(())
    }

    /// Task G.2: precise indexed lookup for dm groups by canonical pair key.
    ///
    /// Backed by the `(env, dm_pair_key)` UNIQUE index on `bcs_groups`
    /// (migration 005). This overrides the default trait impl which would
    /// otherwise scan `list()` — a non-starter at production scale.
    ///
    /// Returns `None` on:
    /// - no row matching the key in this env
    /// - row exists but participants fail to load (we don't want to surface a
    ///   half-loaded group to the caller; logged via `warn!` upstream)
    async fn find_dm_by_pair_key(&self, dm_pair_key: &str) -> Option<Group> {
        // Hit cache first to avoid repeated DB round-trips for hot dm pairs.
        // Cache key is `group_id`, not `dm_pair_key`, so we still need the
        // initial DB lookup to translate pair_key → group_id; afterwards
        // `load_group_from_mysql` (which `get` uses) benefits from the cache.
        let sql = "SELECT group_id FROM bcs_groups \
                   WHERE env = ? AND group_kind = 'dm' AND dm_pair_key = ? LIMIT 1";

        let rows = match self
            .db
            .query_with(
                &self.logical_db,
                sql,
                vec![Value::from(self.env.as_str()), Value::from(dm_pair_key)],
            )
            .await
        {
            Ok(r) => r,
            Err(e) => {
                warn!(
                    dm_pair_key = %dm_pair_key,
                    env = %self.env,
                    error = %e,
                    "find_dm_by_pair_key: query failed"
                );
                return None;
            }
        };

        let row = rows.first()?;
        let group_id: String = db_get_column(row, "group_id").ok()?;
        // Reuse the standard load path (cache + participants normalization).
        self.get(&group_id).await
    }

    /// Try to insert a DM group without mutating an existing row with the same pair key.
    ///
    /// Returns `true` only when this call created the canonical group row.
    /// Returns `false` when the `(env, dm_pair_key)` unique index already has
    /// a winner; the caller owns refetching that row via `find_dm_by_pair_key`.
    ///
    /// Participant inserts are guarded by the newly inserted group row. This
    /// matters because `bcs_group_participants` has no FK to `bcs_groups`: a
    /// loser in a DM pair-key race must not create participant rows for its
    /// caller-supplied, non-canonical `group_id`.
    async fn insert_dm_group_if_absent(&self, group: Group) -> ServiceResult<bool> {
        let pair_key = group.dm_pair_key.clone().ok_or_else(|| {
            ServiceError::InternalError(
                "insert_dm_group_if_absent requires group.dm_pair_key".to_string(),
            )
        })?;

        // ------------------------------------------------------------------
        // Step 1: Pre-flight find. The unique index makes this fast.
        //         If hit -> report no insert, NEVER mutate.
        // ------------------------------------------------------------------
        if self.find_dm_by_pair_key(&pair_key).await.is_some() {
            debug!(
                pair_key = %pair_key,
                requested_id = %group.id,
                "insert_dm_group_if_absent: reuse via pre-flight pair_key lookup"
            );
            return Ok(false);
        }

        // ------------------------------------------------------------------
        // Step 2: Race-safe create via the database plugin transaction API.
        //
        // The first statement reports whether the INSERT happened (we won the
        // race) or the unique key already existed (affected_rows == 0).
        // Participant inserts are idempotent for the winner and guarded for
        // the loser: when the caller's `group_id` is not the canonical row,
        // the INSERT ... SELECT matches zero rows and writes no participants.
        // ------------------------------------------------------------------
        let group_kind_str = Self::group_kind_to_str(group.group_kind);
        let status_str = Self::status_to_str(&group.status);
        let env = self.env.clone();

        // Pre-extract values so the closure captures only owned data and
        // `group` remains available after the closure for cache/logging.
        let g_id = group.id.clone();
        let g_label = group.label.clone();
        let g_driver_bot = group.driver_bot.clone();
        let g_originator: Option<String> = group.originator.clone();
        let g_dm_pair_key = group.dm_pair_key.clone();
        let g_group_strategy_str = Self::group_strategy_to_str(group.group_strategy);
        // Build participant tuples: (bot_uuid, role_str, actor_kind_str, mode_str)
        let g_participants: Vec<(String, &'static str, &'static str, &'static str)> = group
            .participants
            .iter()
            .map(|p| {
                (
                    p.bot_uuid.clone(),
                    Self::role_to_str(&p.role),
                    Self::actor_kind_to_str(p.actor_kind),
                    Self::mode_to_str(p.effective_mode()),
                )
            })
            .collect();

        let on_conflict_nothing = self.flavor.on_conflict_nothing(&["group_id", "env"]);
        let dm_insert_sql = format!(
            "INSERT INTO bcs_groups \
                     (group_id, label, status, driver_bot, originator, env, \
                      routing_policy_json, context, group_kind, dm_pair_key, group_strategy, \
                      gmt_create, gmt_modified) \
                 VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, {now}, {now}) \
                 {on_conflict}",
            now = self.flavor.now(),
            on_conflict = on_conflict_nothing,
        );

        let mut steps = Vec::with_capacity(1 + g_participants.len());
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            // 3.1 Race-safe insert into `bcs_groups`.
            //
            // The on-conflict-nothing clause converts the unique-index
            // violation into a clean 0-affected-rows signal. The first
            // transaction result decides whether this caller created the row.
            &dm_insert_sql,
            vec![
                Value::from(g_id.as_str()),
                Value::from(g_label.as_deref()),
                Value::from(status_str),
                Value::from(g_driver_bot.as_str()),
                Value::from(g_originator.as_deref()),
                Value::from(env.as_str()),
                Value::from(group_kind_str),
                Value::from(g_dm_pair_key.as_deref()),
                Value::from(g_group_strategy_str),
            ],
        )));

        // 3.2 Insert the two Bot participants in the same transaction, but
        // only if this caller's group row exists with the requested pair key.
        let insert_ignore_prefix = self.flavor.insert_or_ignore();
        let participant_insert_sql = format!(
            "{} INTO bcs_group_participants \
                         (group_id, bot_uuid, role, env, actor_kind, mode) \
                     SELECT ?, ?, ?, ?, ?, ? \
                     FROM bcs_groups \
                     WHERE group_id = ? AND env = ? AND dm_pair_key = ?",
            insert_ignore_prefix,
        );
        for (bot_uuid, role_str, actor_kind_str, mode_str) in &g_participants {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                &participant_insert_sql,
                vec![
                    Value::from(g_id.as_str()),
                    Value::from(bot_uuid.as_str()),
                    Value::from(*role_str),
                    Value::from(env.as_str()),
                    Value::from(*actor_kind_str),
                    Value::from(*mode_str),
                    Value::from(g_id.as_str()),
                    Value::from(env.as_str()),
                    Value::from(pair_key.as_str()),
                ],
            )));
        }

        let tx_result = self.db.plugin().transaction(steps).await;

        match tx_result {
            Ok(results) => {
                let group_insert_affected_rows = match results.first() {
                    Some(DbTransactionStepResult::Executed(result)) => result.affected_rows,
                    _ => {
                        return Err(ServiceError::InternalError(
                            "insert_dm_group_if_absent: transaction did not return insert result"
                                .to_string(),
                        ));
                    }
                };
                let mut participant_inserted_rows = 0;
                for result in results.iter().skip(1) {
                    match result {
                        DbTransactionStepResult::Executed(result) => {
                            participant_inserted_rows += result.affected_rows;
                        }
                        DbTransactionStepResult::Rows(_) => {
                            return Err(ServiceError::InternalError(
                                "insert_dm_group_if_absent: transaction returned query rows for participant insert"
                                    .to_string(),
                            ));
                        }
                    }
                }

                let expected_participant_rows = g_participants.len() as u64;
                if participant_inserted_rows > 0
                    && participant_inserted_rows != expected_participant_rows
                {
                    return Err(ServiceError::InternalError(format!(
                        "insert_dm_group_if_absent: inserted {} participant rows, expected {}",
                        participant_inserted_rows, expected_participant_rows
                    )));
                }

                let created = group_insert_affected_rows == 1
                    && participant_inserted_rows == expected_participant_rows;

                if created {
                    info!(
                        group_id = %group.id,
                        pair_key = %pair_key,
                        driver_bot = %group.driver_bot,
                        "insert_dm_group_if_absent: created new dm group"
                    );

                    // Populate the cache so the next `get(group_id)` skips a roundtrip.
                    {
                        let mut cache = self.cache.write().await;
                        cache.insert(group.id.clone(), group.clone());
                    }

                    return Ok(true);
                }

                // Lost the race — the no-op upsert committed without changing
                // group business columns.
                warn!(
                    pair_key = %pair_key,
                    requested_id = %group.id,
                    "insert_dm_group_if_absent: lost race on dm_pair_key unique index"
                );
                Ok(false)
            }
            Err(e) => {
                // Genuine transaction failure.
                warn!(
                    pair_key = %pair_key,
                    requested_id = %group.id,
                    error = %e,
                    "insert_dm_group_if_absent: transaction failed"
                );
                Err(ServiceError::InternalError(e.to_string()))
            }
        }
    }

    async fn update_visibility(&self, id: &str, visibility: &str) -> ServiceResult<()> {
        // Verify group exists
        if self.get(id).await.is_none() {
            return Err(ServiceError::GroupNotFound(id.to_string()));
        }

        let update_sql = format!(
            "UPDATE bcs_groups SET visibility = ?, {} WHERE group_id = ? AND env = ?",
            self.flavor.set_modified_now(),
        );
        self.db
            .execute_with(
                &self.logical_db,
                &update_sql,
                vec![
                    Value::from(visibility),
                    Value::from(id),
                    Value::from(self.env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(group_id = %id, error = %e, "Failed to update group visibility");
                ServiceError::InternalError(e.to_string())
            })?;

        debug!(group_id = %id, visibility = %visibility, "Group visibility updated");
        // Update cache
        {
            let mut cache = self.cache.write().await;
            if let Some(group) = cache.get_mut(id) {
                group.visibility = visibility.to_string();
            }
        }
        Ok(())
    }

    async fn count_filtered(
        &self,
        kind: Option<bcs_service_api::GroupKind>,
        visibility: Option<&str>,
        label: Option<&str>,
    ) -> u64 {
        let mut sql = "SELECT COUNT(*) as cnt FROM bcs_groups WHERE env = ?".to_string();
        let mut params: Vec<Value> = vec![Value::from(self.env.as_str())];

        if let Some(k) = kind {
            sql.push_str(" AND group_kind = ?");
            params.push(Value::from(Self::group_kind_to_str(k)));
        }
        if let Some(v) = visibility {
            sql.push_str(" AND visibility = ?");
            params.push(Value::from(v));
        }
        if let Some(l) = label.map(str::trim).filter(|l| !l.is_empty()) {
            sql.push_str(" AND LOWER(label) LIKE ?");
            let escaped = l.to_lowercase()
                .replace('\\', "\\\\")
                .replace('%', "\\%")
                .replace('_', "\\_");
            params.push(Value::from(format!("%{}%", escaped)));
        }

        let rows = self.db.query_with(&self.logical_db, &sql, params).await.unwrap_or_default();
        rows.first()
            .and_then(|row| db_get_column::<i64>(row, "cnt").ok())
            .unwrap_or(0) as u64
    }

    async fn list_paginated_filtered(
        &self,
        offset: u64,
        limit: u64,
        kind: Option<bcs_service_api::GroupKind>,
        visibility: Option<&str>,
        label: Option<&str>,
    ) -> Vec<Group> {
        let mut inner_sql = format!(
            "SELECT group_id, label, status, driver_bot, originator, routing_policy_json, context, \
                              service_group_uuid, service_mode, service_spec, version, record_status, \
                              {} AS created_ts, {} AS updated_ts, \
                              group_kind, dm_pair_key, group_strategy, visibility \
                       FROM bcs_groups WHERE env = ?",
            self.flavor.unix_ts("gmt_create"),
            self.flavor.unix_ts("gmt_modified"),
        );
        let mut params: Vec<Value> = vec![Value::from(self.env.as_str())];

        if let Some(k) = kind {
            inner_sql.push_str(" AND group_kind = ?");
            params.push(Value::from(Self::group_kind_to_str(k)));
        }
        if let Some(v) = visibility {
            inner_sql.push_str(" AND visibility = ?");
            params.push(Value::from(v));
        }
        if let Some(l) = label.map(str::trim).filter(|l| !l.is_empty()) {
            inner_sql.push_str(" AND LOWER(label) LIKE ?");
            let escaped = l.to_lowercase()
                .replace('\\', "\\\\")
                .replace('%', "\\%")
                .replace('_', "\\_");
            params.push(Value::from(format!("%{}%", escaped)));
        }

        inner_sql.push_str(" ORDER BY gmt_modified DESC LIMIT ? OFFSET ?");
        params.push(Value::from(limit as i64));
        params.push(Value::from(offset as i64));

        let sql = format!(
            "SELECT gs.group_id, gs.label, gs.status, gs.driver_bot, gs.originator, \
                    gp.bot_uuid, gp.role, gs.routing_policy_json, gs.context, \
                    gs.service_group_uuid, gs.service_mode, gs.service_spec, gs.version, gs.record_status, \
                    gs.created_ts, gs.updated_ts, \
                    gp.actor_kind, gp.mode, gs.group_kind, gs.dm_pair_key, gs.group_strategy, gs.visibility \
             FROM ({}) gs \
             LEFT JOIN bcs_group_participants gp ON gs.group_id = gp.group_id AND gp.env = ?",
            inner_sql
        );
        params.push(Value::from(self.env.as_str()));

        let rows = match self.db.query_with(&self.logical_db, &sql, params).await {
            Ok(r) => r,
            Err(e) => {
                warn!(error = %e, "list_paginated_filtered: query failed");
                return Vec::new();
            }
        };

        let mut groups_map: HashMap<String, Group> = HashMap::new();
        for row in &rows {
            let group_id: String = match db_get_column(row, "group_id") {
                Ok(v) => v,
                Err(_) => continue,
            };
            let entry = groups_map.entry(group_id.clone()).or_insert_with(|| {
                let label: Option<String> = db_get_column_opt(row, "label").ok().flatten();
                let status_str: String = db_get_column(row, "status").unwrap_or_default();
                let driver_bot: String = db_get_column(row, "driver_bot").unwrap_or_default();
                let originator: Option<String> =
                    db_get_column_opt(row, "originator").ok().flatten();
                let routing_policy_json: Option<String> =
                    db_get_column_opt(row, "routing_policy_json").ok().flatten();
                let context: Option<String> = db_get_column_opt(row, "context").ok().flatten();
                let service_group_uuid: Option<String> =
                    db_get_column_opt(row, "service_group_uuid").ok().flatten();
                let service_mode: Option<String> =
                    db_get_column_opt(row, "service_mode").ok().flatten();
                let service_spec_json: Option<String> =
                    db_get_column_opt(row, "service_spec").ok().flatten();
                let service_spec: Option<bcs_service_api::ServiceSpec> =
                    match service_spec_json.as_deref() {
                        Some(s) if !s.is_empty() => serde_json::from_str(s).ok(),
                        _ => None,
                    };
                let version: i32 = db_get_column_opt::<i64>(row, "version")
                    .ok()
                    .flatten()
                    .unwrap_or(1) as i32;
                let record_status: String = db_get_column_opt(row, "record_status")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "active".to_string());
                let created_ts: Option<i64> = db_get_column_opt(row, "created_ts").ok().flatten();
                let updated_ts: Option<i64> = db_get_column_opt(row, "updated_ts").ok().flatten();
                let group_kind_str: Option<String> =
                    db_get_column_opt(row, "group_kind").ok().flatten();
                let dm_pair_key: Option<String> =
                    db_get_column_opt(row, "dm_pair_key").ok().flatten();
                let group_strategy_str: Option<String> =
                    db_get_column_opt(row, "group_strategy").ok().flatten();
                let group_strategy = Self::parse_group_strategy(group_strategy_str.as_deref());
                let visibility: String = db_get_column_opt(row, "visibility")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "private".to_string());

                Group {
                    id: group_id,
                    label,
                    status: Self::str_to_status(&status_str),
                    driver_bot,
                    originator,
                    routing_policy: Self::deserialize_routing_policy(routing_policy_json),
                    context,
                    participants: Vec::new(),
                    messages: Vec::new(),
                    workspace: Workspace::default(),
                    service_group_uuid,
                    service_mode,
                    created_at: Self::seconds_to_millis(created_ts),
                    updated_at: Self::seconds_to_millis(updated_ts),
                    group_kind: Self::parse_group_kind(group_kind_str.as_deref()),
                    dm_pair_key,
                    group_strategy,
                    service_spec,
                    version,
                    record_status,
                    visibility,
                }
            });
            if let (Ok(bot_uuid), Ok(role_str)) = (
                db_get_column::<String>(row, "bot_uuid"),
                db_get_column::<String>(row, "role"),
            ) {
                if !entry.participants.iter().any(|p| p.bot_uuid == bot_uuid) {
                    let actor_kind_str: Option<String> =
                        db_get_column_opt(row, "actor_kind").ok().flatten();
                    let mode_str: Option<String> = db_get_column_opt(row, "mode").ok().flatten();
                    let (actor_kind, mode) = Self::normalize_kind_mode(
                        &entry.id,
                        &bot_uuid,
                        self.env.as_str(),
                        actor_kind_str.as_deref(),
                        mode_str.as_deref(),
                    );
                    entry.participants.push(Participant {
                        bot_uuid,
                        bot_name: None,
                        kind: Some(ParticipantKind::Bot),
                        role: Self::str_to_role(&role_str),
                        actor_kind,
                        mode: Some(mode),
                    });
                }
            }
        }
        let mut groups = groups_map.into_values().collect::<Vec<_>>();
        Group::sort_by_updated_at_desc(&mut groups);
        groups
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_db_api::{DbExecuteResult, DbHealth};
    use std::sync::Mutex as StdMutex;

    #[derive(Default)]
    struct RecordingDbPlugin {
        transaction_sql: StdMutex<Vec<String>>,
        first_execute_affected_rows: u64,
    }

    impl RecordingDbPlugin {
        fn with_first_execute_affected_rows(first_execute_affected_rows: u64) -> Self {
            Self {
                first_execute_affected_rows,
                ..Self::default()
            }
        }
    }

    #[async_trait]
    impl DbPlugin for RecordingDbPlugin {
        async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
            Ok(Vec::new())
        }

        async fn execute(&self, _statement: DbStatement) -> DbResult<DbExecuteResult> {
            Ok(DbExecuteResult::default())
        }

        async fn transaction(
            &self,
            steps: Vec<DbTransactionStep>,
        ) -> DbResult<Vec<DbTransactionStepResult>> {
            let mut results = Vec::with_capacity(steps.len());
            let mut sql = self.transaction_sql.lock().expect("transaction sql");
            let mut execute_index = 0;

            for step in steps {
                match step {
                    DbTransactionStep::Query(statement) => {
                        sql.push(statement.sql().to_string());
                        results.push(DbTransactionStepResult::Rows(Vec::new()));
                    }
                    DbTransactionStep::Execute(statement) => {
                        sql.push(statement.sql().to_string());
                        let affected_rows = if execute_index == 0 {
                            self.first_execute_affected_rows
                        } else {
                            0
                        };
                        execute_index += 1;
                        results.push(DbTransactionStepResult::Executed(DbExecuteResult {
                            affected_rows,
                            last_insert_id: None,
                        }));
                    }
                }
            }

            Ok(results)
        }

        async fn health_check(&self) -> DbResult<DbHealth> {
            Ok(DbHealth::healthy())
        }
    }

    #[test]
    fn test_logical_db_must_stay_empty() {
        assert!(assert_empty_logical_db("").is_ok());
        assert!(matches!(
            assert_empty_logical_db("legacy-db"),
            Err(DbError::InvalidInput(_))
        ));
    }

    #[tokio::test]
    async fn dm_insert_loser_participant_steps_are_guarded_by_group_row() {
        let db = Arc::new(RecordingDbPlugin::with_first_execute_affected_rows(1));
        let repo = MySqlGroupStore::new(db.clone(), "race".to_string());
        let pair_key = Group::compute_dm_pair_key("alice", "bob");
        let participants = vec![
            Participant {
                bot_uuid: "alice".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: ActorKind::Bot,
                mode: None,
            },
            Participant {
                bot_uuid: "bob".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: ActorKind::Bot,
                mode: None,
            },
        ];
        let mut group = Group::new("loser-group", "alice", participants);
        group.group_kind = bcs_domain::GroupKind::Dm;
        group.dm_pair_key = Some(pair_key);

        let created = repo
            .insert_dm_group_if_absent(group)
            .await
            .expect("insert dm group");

        assert!(!created);
        let sql = db.transaction_sql.lock().expect("transaction sql");
        let participant_sql: Vec<_> = sql
            .iter()
            .filter(|statement| statement.contains("bcs_group_participants"))
            .collect();

        assert_eq!(participant_sql.len(), 2);
        assert!(participant_sql.iter().all(|statement| {
            statement.contains("SELECT")
                && statement.contains("FROM bcs_groups")
                && statement.contains("group_id = ?")
                && statement.contains("dm_pair_key = ?")
        }));
    }

    #[test]
    fn test_status_conversion() {
        assert_eq!(
            MySqlGroupStore::status_to_str(&GroupStatus::Active),
            "active"
        );
        assert_eq!(
            MySqlGroupStore::status_to_str(&GroupStatus::Completed),
            "completed"
        );
        assert_eq!(
            MySqlGroupStore::status_to_str(&GroupStatus::Closed),
            "closed"
        );
        assert_eq!(
            MySqlGroupStore::status_to_str(&GroupStatus::Inactive),
            "inactive"
        );

        assert!(matches!(
            MySqlGroupStore::str_to_status("active"),
            GroupStatus::Active
        ));
        assert!(matches!(
            MySqlGroupStore::str_to_status("completed"),
            GroupStatus::Completed
        ));
        assert!(matches!(
            MySqlGroupStore::str_to_status("unknown"),
            GroupStatus::Active
        ));
    }

    #[test]
    fn test_role_conversion() {
        assert_eq!(
            MySqlGroupStore::role_to_str(&ParticipantRole::Driver),
            "driver"
        );
        assert_eq!(
            MySqlGroupStore::role_to_str(&ParticipantRole::Consultant),
            "consultant"
        );
        assert_eq!(
            MySqlGroupStore::role_to_str(&ParticipantRole::Observer),
            "observer"
        );

        assert!(matches!(
            MySqlGroupStore::str_to_role("driver"),
            ParticipantRole::Driver
        ));
        assert!(matches!(
            MySqlGroupStore::str_to_role("consultant"),
            ParticipantRole::Consultant
        ));
        assert!(matches!(
            MySqlGroupStore::str_to_role("unknown"),
            ParticipantRole::Driver
        ));
    }

    // ======================================================================
    // M.6 Human Actor V1 — actor_kind / mode parsing & normalization tests
    // ======================================================================

    #[test]
    fn test_actor_kind_to_str_and_back() {
        assert_eq!(MySqlGroupStore::actor_kind_to_str(ActorKind::Bot), "bot");
        assert_eq!(
            MySqlGroupStore::actor_kind_to_str(ActorKind::Human),
            "human"
        );

        assert!(matches!(
            MySqlGroupStore::parse_actor_kind(Some("bot")),
            ActorKind::Bot
        ));
        assert!(matches!(
            MySqlGroupStore::parse_actor_kind(Some("human")),
            ActorKind::Human
        ));
        // Unknown / NULL → falls back to Bot
        assert!(matches!(
            MySqlGroupStore::parse_actor_kind(Some("alien")),
            ActorKind::Bot
        ));
        assert!(matches!(
            MySqlGroupStore::parse_actor_kind(None),
            ActorKind::Bot
        ));
    }

    #[test]
    fn test_mode_to_str_and_back() {
        assert_eq!(MySqlGroupStore::mode_to_str(ParticipantMode::Auto), "auto");
        assert_eq!(
            MySqlGroupStore::mode_to_str(ParticipantMode::Muted),
            "muted"
        );
        assert_eq!(
            MySqlGroupStore::mode_to_str(ParticipantMode::Present),
            "present"
        );
        assert_eq!(
            MySqlGroupStore::mode_to_str(ParticipantMode::Absent),
            "absent"
        );

        assert_eq!(
            MySqlGroupStore::parse_participant_mode_opt(Some("auto")),
            Some(ParticipantMode::Auto)
        );
        assert_eq!(
            MySqlGroupStore::parse_participant_mode_opt(Some("muted")),
            Some(ParticipantMode::Muted)
        );
        assert_eq!(
            MySqlGroupStore::parse_participant_mode_opt(Some("present")),
            Some(ParticipantMode::Present)
        );
        assert_eq!(
            MySqlGroupStore::parse_participant_mode_opt(Some("absent")),
            Some(ParticipantMode::Absent)
        );
        assert_eq!(
            MySqlGroupStore::parse_participant_mode_opt(Some("supervised")),
            None
        );
        assert_eq!(MySqlGroupStore::parse_participant_mode_opt(None), None);
    }

    #[test]
    fn test_normalize_kind_mode_legal_combinations_passthrough() {
        let cases = [
            ("bot", "auto", ActorKind::Bot, ParticipantMode::Auto),
            ("bot", "muted", ActorKind::Bot, ParticipantMode::Muted),
            (
                "human",
                "present",
                ActorKind::Human,
                ParticipantMode::Present,
            ),
            ("human", "absent", ActorKind::Human, ParticipantMode::Absent),
        ];
        for (kind_str, mode_str, expect_kind, expect_mode) in cases {
            let (k, m) = MySqlGroupStore::normalize_kind_mode(
                "g1",
                "a1",
                "dev",
                Some(kind_str),
                Some(mode_str),
            );
            assert_eq!(k, expect_kind, "kind for ({}, {})", kind_str, mode_str);
            assert_eq!(m, expect_mode, "mode for ({}, {})", kind_str, mode_str);
        }
    }

    #[test]
    fn test_normalize_kind_mode_illegal_pair_falls_back_to_default_for_kind() {
        // Bot + Present is illegal → fallback to ParticipantMode::Auto
        let (k, m) =
            MySqlGroupStore::normalize_kind_mode("g1", "b1", "dev", Some("bot"), Some("present"));
        assert_eq!(k, ActorKind::Bot);
        assert_eq!(m, ParticipantMode::Auto);

        // Human + Auto is illegal → fallback to ParticipantMode::Absent
        let (k, m) =
            MySqlGroupStore::normalize_kind_mode("g1", "h1", "dev", Some("human"), Some("auto"));
        assert_eq!(k, ActorKind::Human);
        assert_eq!(m, ParticipantMode::Absent);
    }

    #[test]
    fn test_normalize_kind_mode_unknown_inputs_default() {
        // Unknown actor_kind → Bot, unknown mode → default_for(Bot) = Auto
        let (k, m) = MySqlGroupStore::normalize_kind_mode(
            "g1",
            "b1",
            "dev",
            Some("alien"),
            Some("supervised"),
        );
        assert_eq!(k, ActorKind::Bot);
        assert_eq!(m, ParticipantMode::Auto);
    }

    /// Regression test for review Finding #4.
    ///
    /// NULL / absent `mode` is the normal compatibility path (rows that
    /// pre-date Migration 003) and MUST NOT emit ERROR logs. The result
    /// must be the kind-aware default per Requirement 3.18#3.
    #[test]
    fn test_normalize_kind_mode_null_mode_is_silent_compat_path() {
        // (bot, NULL) → auto
        let (k, m) = MySqlGroupStore::normalize_kind_mode("g1", "b1", "dev", Some("bot"), None);
        assert_eq!(k, ActorKind::Bot);
        assert_eq!(m, ParticipantMode::Auto);

        // (human, NULL) → absent
        let (k, m) = MySqlGroupStore::normalize_kind_mode("g1", "h1", "dev", Some("human"), None);
        assert_eq!(k, ActorKind::Human);
        assert_eq!(m, ParticipantMode::Absent);

        // (NULL, NULL) → bot/auto (full compat for legacy rows)
        let (k, m) = MySqlGroupStore::normalize_kind_mode("g1", "b1", "dev", None, None);
        assert_eq!(k, ActorKind::Bot);
        assert_eq!(m, ParticipantMode::Auto);

        // (NULL, "auto") → kind defaults to Bot (compat); mode passes through
        let (k, m) = MySqlGroupStore::normalize_kind_mode("g1", "b1", "dev", None, Some("auto"));
        assert_eq!(k, ActorKind::Bot);
        assert_eq!(m, ParticipantMode::Auto);
    }
}
