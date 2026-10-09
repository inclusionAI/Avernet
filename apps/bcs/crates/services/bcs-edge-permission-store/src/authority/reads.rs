//! `BotAuthorityRepoPort` implementation over the Task 2 authority schema.
//!
//! Strictness rules implemented here (spec §5/§12.4):
//! - env is bound to the store instance; every query filters `env = ?`;
//! - `ownership` reads the live bot row (soft-deleted = missing) and the
//!   approved owner slot; version 0 → `OwnershipNotInitialized`, missing /
//!   duplicate owner → `CorruptAuthority`, an owner-less initialized bot is
//!   never reported through an implicit state;
//! - `role` / `roles_for` correlate by the FULL (from_id=human actor, to_id)
//!   pair; the batch builds ONE statement with explicit pair conjunctions —
//!   no two independent IN sets (authorization cartesian product) and no
//!   per-pair N+1; corrupted matching rows fail the whole read.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue};
use bcs_domain::{BotAccessRelation, OwnershipState, UNINITIALIZED_OWNERSHIP_VERSION};
use bcs_service_api::port::repo::bot_authority::human_actor_id;
pub use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::{ServiceError, ServiceResult};
use tracing::warn;

use super::codec::{
    ownership_state_from_owner_row, relation_from_row, ROLE_ROW_COLUMNS,
};
use crate::common::{required_string, required_u64, service_db_error};

/// DB-backed strict authority read store.
///
/// Holds an `Arc<dyn DbPlugin>` + flavor + the env bound at construction
/// (authority queries are env-isolated; env is a service-level concern and
/// never a per-request input). The fields are `pub(super)` so the sibling
/// mutation/list modules (`manager.rs`, `audit.rs`) share the SAME store
/// instance and its env binding.
pub struct DbBotAuthorityStore {
    pub(super) db: Arc<dyn DbPlugin>,
    pub(super) flavor: DbSqlFlavor,
    pub(super) env: String,
}

impl DbBotAuthorityStore {
    pub fn new(db: Arc<dyn DbPlugin>, flavor: DbSqlFlavor, env: String) -> Self {
        Self { db, flavor, env }
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self::new(db, DbSqlFlavor::Sqlite, env)
    }

    pub fn mysql(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self::new(db, DbSqlFlavor::Mysql, env)
    }

    pub fn env(&self) -> &str {
        &self.env
    }

    pub fn flavor(&self) -> DbSqlFlavor {
        self.flavor
    }

    async fn query(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<Vec<DbRow>> {
        self.db.query(statement).await.map_err(|err| {
            warn!(operation, error = %err, "db_bot_authority: query failed");
            service_db_error(operation, err)
        })
    }
}

impl DbBotAuthorityStore {
    /// Read the live (non-deleted) bot row's ownership_version.
    async fn bot_ownership_version(&self, bot_id: &str) -> ServiceResult<Option<u64>> {
        let rows = self
            .query(
                "authority_bot_row",
                DbStatement::with_params(
                    "SELECT bot_uuid, env, ownership_version FROM bcs_bots \
                     WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0 LIMIT 1",
                    vec![DbValue::from(bot_id), DbValue::from(self.env.as_str())],
                ),
            )
            .await?;
        let row = match rows.into_iter().next() {
            Some(row) => row,
            None => return Ok(None),
        };
        Ok(Some(required_u64(&row, "ownership_version")?))
    }

    pub(super) fn corrupt(&self, bot_id: &str, detail: impl Into<String>) -> ServiceError {
        ServiceError::Authority(AuthorityError::CorruptAuthority {
            bot_id: bot_id.to_string(),
            env: self.env.clone(),
            detail: detail.into(),
        })
    }
}

#[async_trait]
impl BotAuthorityRepoPort for DbBotAuthorityStore {
    async fn ownership(&self, bot_id: &str) -> ServiceResult<OwnershipState> {
        // Live bot row first: a missing or soft-deleted Bot has no authority
        // surface at all.
        let Some(version) = self.bot_ownership_version(bot_id).await? else {
            return Err(ServiceError::BotNotFound(bot_id.to_string()));
        };
        if version == UNINITIALIZED_OWNERSHIP_VERSION {
            return Err(ServiceError::Authority(AuthorityError::OwnershipNotInitialized {
                bot_id: bot_id.to_string(),
                env: self.env.clone(),
            }));
        }
        // The strict approved-owner slot: the partial unique index makes
        // >1 structurally impossible in a healthy database; the read still
        // fails closed on it and on 0 (initialized-but-ownerless).
        let rows = self
            .query(
                "authority_owner_rows",
                DbStatement::with_params(
                    &format!(
                        "SELECT {} FROM edge_grants \
                         WHERE env = ? AND to_id = ? AND grant_kind = 'owner' \
                           AND status = 'approved' ORDER BY id",
                        ROLE_ROW_COLUMNS
                    ),
                    vec![DbValue::from(self.env.as_str()), DbValue::from(bot_id)],
                ),
            )
            .await?;
        match rows.len() {
            0 => Err(self.corrupt(bot_id, "initialized bot has no approved owner edge")),
            1 => ownership_state_from_owner_row(&rows[0], bot_id, &self.env, version),
            count => Err(self.corrupt(
                bot_id,
                format!("initialized bot has {count} approved owner edges"),
            )),
        }
    }

    async fn role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> ServiceResult<Option<BotAccessRelation>> {
        let rows = self
            .query(
                "authority_role_rows",
                DbStatement::with_params(
                    &format!(
                        "SELECT {} FROM edge_grants \
                         WHERE env = ? AND from_id = ? AND to_id = ? AND status = 'approved' \
                           AND grant_kind IN ('owner', 'manager') ORDER BY id",
                        ROLE_ROW_COLUMNS
                    ),
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(human_actor_id(user_id)),
                        DbValue::from(bot_id),
                    ],
                ),
            )
            .await?;
        merge_role_rows(&rows, bot_id, &self.env)
    }

    async fn roles_for(
        &self,
        pairs: &[(String, String)],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
        // Empty batch in, empty (position-aligned) result out; a NON-empty
        // batch can never come back empty-success — see merge + fill below.
        if pairs.is_empty() {
            return Ok(Vec::new());
        }
        // One statement correlated by FULL pairs: each pair is its own
        // `(from_id = ? AND to_id = ?)` conjunction joined by OR. Two
        // independent IN lists would authorize the cartesian product of the
        // user/bot sets (spec: 批量 SQL 按完整 pair 关联) and per-pair
        // statements would be an N+1 lookup.
        let mut sql = format!(
            "SELECT {} FROM edge_grants \
             WHERE env = ? AND status = 'approved' \
               AND grant_kind IN ('owner', 'manager') \
               AND (",
            ROLE_ROW_COLUMNS
        );
        let mut params = Vec::with_capacity(1 + pairs.len() * 2);
        params.push(DbValue::from(self.env.as_str()));
        for (index, (user_id, bot_id)) in pairs.iter().enumerate() {
            if index > 0 {
                sql.push_str(" OR ");
            }
            sql.push_str("(from_id = ? AND to_id = ?)");
            params.push(DbValue::from(human_actor_id(user_id)));
            params.push(DbValue::from(bot_id.clone()));
        }
        sql.push_str(") ORDER BY id");

        let rows = self.query("authority_role_rows_batch", DbStatement::with_params(&sql, params)).await?;

        // Decode strictly (a corrupted matching row fails the WHOLE batch),
        // then answer per input position: owner first, any manager second.
        let mut by_pair: HashMap<(String, String), BotAccessRelation> = HashMap::new();
        for row in &rows {
            let from_id = required_string(row, "from_id")?;
            let to_id = required_string(row, "to_id")?;
            let relation = relation_from_row(row, &to_id, &self.env)?;
            let merged = merge_relation(by_pair.get(&(from_id.clone(), to_id.clone())), relation);
            by_pair.insert((from_id, to_id), merged);
        }

        let mut out = Vec::with_capacity(pairs.len());
        for (user_id, bot_id) in pairs {
            let from_id = human_actor_id(user_id);
            out.push(by_pair.get(&(from_id, bot_id.clone())).copied());
        }
        Ok(out)
    }

    // Task 4's mutation/listing methods: one-transaction validation +
    // edge change + audit, and the validated manager page. The full
    // contracts live in the trait docs; the SQL/engine implementation and
    // the shared statement builders live in `manager.rs`/`audit.rs`.
    async fn mutate_manager(
        &self,
        actor: bcs_domain::AuditActor,
        bot_id: &str,
        mutation: bcs_domain::ManagerMutation,
    ) -> ServiceResult<bcs_domain::ManagerMutationResult> {
        self.mutate_manager_inner(actor, bot_id, mutation).await
    }

    async fn list_managers(
        &self,
        bot_id: &str,
        offset: u64,
        limit: u64,
    ) -> ServiceResult<bcs_service_api::types::BotManagerList> {
        self.list_managers_inner(bot_id, offset, limit).await
    }

    // Task 7's team synchronization: the ONE-TRANSACTION lane owning every
    // `team/*` manager edge. The full contract (durable idempotency,
    // aggregate reconcile, service-actor audit, source isolation) lives
    // in the trait docs; the SQL engine is `team_sync.rs`.
    async fn sync_team(
        &self,
        command: bcs_service_api::types::team_manager_sync::TeamManagerSync,
    ) -> ServiceResult<bcs_service_api::types::team_manager_sync::TeamSyncReceipt> {
        self.sync_team_inner(command).await
    }

    // Task 8's ownership transfer lanes: create/decide (one Bot-serialized
    // transaction each) plus the party-visible read/list projections. The
    // full contracts live in the trait docs; the SQL engines live in
    // `transfer_create.rs` / `transfer_decide.rs` / `transfer_query.rs`.
    async fn create_transfer(
        &self,
        command: bcs_service_api::types::ownership_transfer::CreateOwnershipTransfer,
    ) -> ServiceResult<bcs_service_api::types::ownership_transfer::CreateTransferResult> {
        self.create_transfer_inner(command).await
    }

    async fn decide_transfer(
        &self,
        actor_user_id: &str,
        transfer_id: &str,
        action: bcs_domain::TransferAction,
    ) -> ServiceResult<bcs_service_api::types::ownership_transfer::CommittedTransferOutcome> {
        self.decide_transfer_inner(actor_user_id, transfer_id, action)
            .await
    }

    async fn get_transfer(
        &self,
        viewer_user_id: &str,
        transfer_id: &str,
    ) -> ServiceResult<bcs_service_api::types::ownership_transfer::OwnershipTransfer> {
        self.get_transfer_inner(viewer_user_id, transfer_id).await
    }

    async fn list_transfers(
        &self,
        query: bcs_service_api::types::ownership_transfer::ListOwnershipTransfers,
    ) -> ServiceResult<bcs_service_api::types::ownership_transfer::OwnershipTransferPage> {
        self.list_transfers_inner(query).await
    }
}

/// Owner-priority merge of two relations for one subject/bot pair.
fn merge_relation(current: Option<&BotAccessRelation>, next: BotAccessRelation) -> BotAccessRelation {
    match (current, next) {
        (Some(BotAccessRelation::Owner), _) | (_, BotAccessRelation::Owner) => BotAccessRelation::Owner,
        (Some(BotAccessRelation::Manager), BotAccessRelation::Manager) => BotAccessRelation::Manager,
        (None, relation) => relation,
    }
}

/// Decode the role rows of ONE pair strictly and merge them
/// (owner first, any manager second).
fn merge_role_rows(
    rows: &[DbRow],
    bot_id: &str,
    env: &str,
) -> ServiceResult<Option<BotAccessRelation>> {
    let mut merged: Option<BotAccessRelation> = None;
    for row in rows {
        let relation = relation_from_row(row, bot_id, env)?;
        merged = Some(merge_relation(merged.as_ref(), relation));
    }
    Ok(merged)
}