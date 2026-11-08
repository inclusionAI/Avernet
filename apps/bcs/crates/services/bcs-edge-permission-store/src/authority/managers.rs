//! Manager write paths: single-item `edit_manager` and
//! trusted full-set `sync_managers` (bot-authority plan
//! Task 2, spec §5.1/§5.2).
//!
//! ## Transaction shape (Task-0 sketch, feasibility doc §2)
//!
//! One whole-set change is ONE
//! `DbPlugin::transaction_with_options` call carrying
//! the Task-0 between-step deadline; the
//! transaction-external pre-read is parameter
//! preparation only. Inside the transaction (lock
//! order: Bot row first, never Human-then-Bot; spec
//! §7):
//!
//! 1. bot row lock read — its `bot_uuid` is bound
//!    into the owner guard, so a vanished bot fails
//!    the transaction at binding resolution;
//! 2. owner guard (exactly one approved owner); the
//!    owner value feeds every owner-exclusion
//!    predicate — the same predicate on the
//!    pre-read, the guards, and the written audits
//!    (Task-0 review carry-over);
//! 3. count guard `count(current managers, owner
//!    excluded) = |S|`; an empty S verifies
//!    count = 0;
//! 4. one membership chunk guard per
//!    ≤100-sorted-id chunk of the pre-read set;
//! 5. one Human-existence guard per added-member
//!    chunk (the `bcs_bots` Human ACTOR registry,
//!    `actor_kind = 'human'`, env-bound, live);
//! 6. diff writes, each `ExecuteChecked` against
//!    its exact count: the revoke side writes
//!    audit (`INSERT..SELECT`) then flips status;
//!    the added side writes audit VALUES (≤99
//!    rows, F1) then insert/restore. Every write
//!    is genuinely changing, so no affected-rows
//!    semantics are relied upon for correctness
//!    (ruling T0-2), and the owner's own manager
//!    edge is never rewritten (owner precedence);
//! 7. a post-state count guard `= |input|` —
//!    replaces the sketch's sink UPDATE so a
//!    zero-diff transaction needs no write;
//! 8. one trailing `SELECT ? AS t0, ...` step
//!    binding every guard row: a missing guard
//!    row fails the transaction at binding
//!    resolution (Task-0 s1c — an ordinary
//!    zero-row query would NOT), and
//!    `stop_on_no_rows` is never used as a
//!    rollback (s2).
//!
//! Pre-read staleness (same-count replacement
//! between pre-read and guards) fails the
//! transaction, and the caller retries from a
//! fresh pre-read. Retries are bounded and
//! restricted to deterministic local rollbacks
//! (`ConditionFailed`, duplicate-key, guard
//! binding `InvalidInput`); `DbError::Timeout` is
//! terminal and never retried, and its rollback
//! is the plugin contract's guarantee (Task-0
//! 方案 A).
//!
//! `edit_manager` runs the same guard set for one
//! target: the owner target rejects with 409
//! `owner_not_manager` (spec §5.1), adds require a
//! valid existing Human (a live `bcs_bots` actor row),
//! and the Human operator's current owner/manager edge
//! is re-verified inside the transaction.
//!
//! `sync_managers` is best-effort per the frozen ruling:
//! unknown, deleted or non-Human-shaped members are
//! filtered out with a warn log and the final set equals
//! the valid remainder (spec §5.2); the strict
//! single-item edit keeps whole-request rejection.

use std::collections::{BTreeSet, HashMap};
use std::time::Duration;

use bcs_db_api::{
    DbError, DbStatement, DbTransactionOptions, DbTransactionParam,
    DbTransactionStep, DbValue,
};
use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityActorKind, AuthorityMutation,
    ManagerEdit, ManagerEditAction, ManagerSetChange, ManagerSync,
    human_actor_id, human_user_id_of_actor,
};
use bcs_service_api::{ServiceError, ServiceResult};
use tracing::warn;

use super::audit::{
    ACTION_MANAGER_GRANT, ACTION_MANAGER_RESTORE, ACTION_MANAGER_REVOKE,
    AUDIT_VALUES_ROWS, AuditRowDraft, audit_values_statement,
    revoke_audit_insert_select,
};
use super::{
    DbBotAuthorityStore, authority_db_error, human_unknown_error,
    input_limit_error, map_tx_error, not_initialized_error,
    operator_forbidden_error, stock_limit_error,
};

/// Max deduped users per sync request (spec §5.2).
pub(crate) const MANAGER_INPUT_CAP: usize = 1_000;

/// Frozen current-stock bound (feasibility doc §5.2). Not
/// a product manager cap; raising it requires
/// re-measured capacity evidence.
pub(crate) const MANAGER_STOCK_BOUND: u64 = 10_000;

/// Manager-collection statement chunk (spec §5.2: at
/// most 100 principals per SQL statement).
const MANAGER_CHUNK: usize = 100;

/// Whole-transaction deadline (Task-0 方案 A;
/// feasibility doc §5.1 targets ≤10 s; measured
/// manager transactions stay far below it).
const TX_DEADLINE: Duration = Duration::from_secs(10);

/// Deterministic-rollback retries (uniqueness races,
/// same-count replacement between pre-read and guards).
const MAX_RETRIES: u32 = 3;

/// Edge row state of one manager member.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum EdgeRowState {
    Approved,
    Revoked,
    Absent,
}

/// Write-state classification of the sync target bot
/// (the lenient-sync pre-read; spec §5.2 ruling).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SyncBotState {
    /// Absent or tombstoned: zeros with a warn log.
    Missing,
    /// Live but `ownership_version = 0`: hard error.
    Uninitialized,
    /// Live and initialized: the sync proceeds.
    Ready,
}

/// The planned write for one `edit_manager` call,
/// classified against the pre-read.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum EditPlan {
    NoOp,
    Insert,
    Restore,
    Remove,
}

/// Builds one manager-change transaction.
struct TxBuilder {
    steps: Vec<DbTransactionStep>,
    /// `(step, column)` references the trailing
    /// consumption step must bind.
    guard_tokens: Vec<DbTransactionParam>,
    bot_step: usize,
    owner_step: usize,
}

impl TxBuilder {
    fn bot_reference(&self) -> DbTransactionParam {
        DbTransactionParam::query_result(self.bot_step, 0, "bot_uuid")
    }

    fn owner_reference(&self) -> DbTransactionParam {
        DbTransactionParam::query_result(self.owner_step, 0, "owner_id")
    }

    /// Append the trailing consumption step
    /// (`SELECT ? AS t0, ...`) for every guard row
    /// collected so far.
    fn finish(mut self) -> Vec<DbTransactionStep> {
        let references: Vec<DbTransactionParam> =
            std::mem::take(&mut self.guard_tokens);
        if !references.is_empty() {
            let placeholders = references
                .iter()
                .enumerate()
                .map(|(index, _)| format!("? AS t{index}"))
                .collect::<Vec<_>>()
                .join(", ");
            self.steps.push(DbTransactionStep::Query(
                DbStatement::with_transaction_params(
                    format!("SELECT {placeholders}"),
                    references,
                ),
            ));
        }
        self.steps
    }
}

/// Identifies the audit rows of one mutation.
struct AuditHeader {
    mutation_id: String,
    actor_kind: String,
    actor_id: String,
}

impl AuditHeader {
    fn from_mutation(mutation: &AuthorityMutation) -> Self {
        Self {
            mutation_id: mutation.mutation_id.clone(),
            actor_kind: mutation.actor.kind().as_str().to_string(),
            actor_id: mutation.actor.actor_id(),
        }
    }
}

/// Deterministic placement of every diff row in the
/// `(env, mutation_id, sequence)` ordering: all removed
/// (sorted) first, then all added (sorted). The SQL
/// `INSERT..SELECT`s compute the same sequences with
/// `ROW_NUMBER()` over the same sorted fragments.
fn assign_sequences(
    removed: &[String],
    added: &[String],
) -> HashMap<String, u64> {
    let mut sequences =
        HashMap::with_capacity(removed.len() + added.len());
    for (index, member) in removed.iter().enumerate() {
        sequences.insert(member.clone(), index as u64);
    }
    for (offset, member) in added.iter().enumerate() {
        sequences.insert(member.clone(), (removed.len() + offset) as u64);
    }
    sequences
}

/// Deterministic local rollbacks that a fresh pre-read
/// may resolve (feasibility doc §5.1 retry exit).
/// `DbError::Timeout` is never among them: its outcome is
/// governed by the plugin contract and must not be
/// re-run.
fn is_retryable(err: &DbError) -> bool {
    matches!(
        err,
        DbError::ConditionFailed { .. } | DbError::InvalidInput(_)
    ) || err.is_duplicate_key()
}

/// Validate + normalize the sync input (spec §5.2, the
/// lenient-sync ruling): dedup FIRST over the WHOLE raw
/// input and count the 1,000 cap on that deduped original —
/// including members the filter below will skip, so a
/// garbage-filled request cannot wear the input boundary
/// down; then split it into the valid (`human_*` actor
/// shapes, sorted) and the skipped members (unknown shapes
/// that never fail the request any more).
fn normalize_input(
    user_ids: &[String],
) -> ServiceResult<(Vec<String>, Vec<String>)> {
    let deduped: BTreeSet<String> = user_ids.iter().cloned().collect();
    if deduped.len() > MANAGER_INPUT_CAP {
        return Err(input_limit_error());
    }
    let mut valid = Vec::with_capacity(deduped.len());
    let mut skipped = Vec::new();
    for user_id in deduped {
        if human_user_id_of_actor(&user_id).is_some() {
            valid.push(user_id);
        } else {
            skipped.push(user_id);
        }
    }
    Ok((valid, skipped))
}

impl DbBotAuthorityStore {
    // ============ dialect helpers ============

    /// `FOR UPDATE` suffix for MySQL guard reads
    /// (current/locking reads must not trust the RR
    /// snapshot; spec §7). The Human-registry guard reads
    /// take the row lock too: the registry is now part of
    /// `bcs_bots` (a soft-deleted Human must fail the
    /// in-transaction guard, not ride a stale snapshot),
    /// and the Bot row is always locked first — the spec
    /// §7 Bot → Human order is preserved.
    fn lock_suffix(&self) -> &'static str {
        match self.flavor() {
            bcs_db_api::DbSqlFlavor::Mysql => " FOR UPDATE",
            bcs_db_api::DbSqlFlavor::Sqlite => "",
        }
    }

    /// Byte-exact owner-exclusion predicate
    /// (`from_id`).
    fn from_id_neq(&self) -> &'static str {
        match self.flavor() {
            bcs_db_api::DbSqlFlavor::Mysql => "CAST(from_id AS BINARY) <> ?",
            bcs_db_api::DbSqlFlavor::Sqlite => "from_id <> ?",
        }
    }

    fn from_id_in(&self, count: usize) -> String {
        let placeholders = self.id_placeholders(count);
        match self.flavor() {
            bcs_db_api::DbSqlFlavor::Mysql => {
                format!("CAST(from_id AS BINARY) IN ({placeholders})")
            }
            bcs_db_api::DbSqlFlavor::Sqlite => {
                format!("from_id IN ({placeholders})")
            }
        }
    }

    fn gmt_modified_now(&self) -> &'static str {
        self.flavor().now()
    }

    // ============ preparation reads ============    // ============ preparation reads ============

    /// Live/initialized bot check (preparation read;
    /// the transaction re-locks and re-verifies). Strict:
    /// the single-item edit and the transfer paths keep
    /// hard errors here.
    async fn require_write_bot(
        &self,
        bot_id: &str,
    ) -> ServiceResult<()> {
        match self.bot_ownership_state(bot_id).await? {
            Some((_, version)) if version > 0 => Ok(()),
            Some(_) => Err(not_initialized_error(bot_id)),
            None => Err(ServiceError::BotNotFound(bot_id.to_string())),
        }
    }

    /// Soft write-state classification for the lenient
    /// sync (spec §5.2 ruling): a missing/tombstoned
    /// target returns zeros with a warn log, while an
    /// uninitialized live bot still carries the hard
    /// `bot_ownership_not_initialized` error.
    async fn sync_bot_state(
        &self,
        bot_id: &str,
    ) -> ServiceResult<SyncBotState> {
        match self.bot_ownership_state(bot_id).await? {
            Some((_, version)) if version > 0 => Ok(SyncBotState::Ready),
            Some(_) => Ok(SyncBotState::Uninitialized),
            None => Ok(SyncBotState::Missing),
        }
    }

    /// Pre-read owner identity. Final arbitration
    /// happens via the in-transaction owner guard.
    async fn pre_read_owner(&self, bot_id: &str) -> ServiceResult<String> {
        let mut owners = self.approved_owner_rows(bot_id).await?;
        match owners.len() {
            1 => Ok(owners.pop().expect("exactly one owner row")),
            0 => Err(super::owner_integrity_error(format!(
                "bot {bot_id} has no approved owner edge"
            ))),
            count => Err(super::owner_integrity_error(format!(
                "bot {bot_id} has {count} approved owner edges"
            ))),
        }
    }

    /// Pre-read the Human ACTOR registry (`bcs_bots` rows
    /// with `actor_kind = 'human'`, env-bound, live);
    /// returns the missing user ids (byte-exact). Input
    /// carries user ids; the registry is keyed by the
    /// `human_<user_id>` bot_uuid.
    async fn pre_read_missing_humans(
        &self,
        user_ids: &[&str],
    ) -> ServiceResult<Vec<String>> {
        if user_ids.is_empty() {
            return Ok(Vec::new());
        }
        let bot_uuids: Vec<String> =
            user_ids.iter().map(|user| human_actor_id(user)).collect();
        let mut params: Vec<DbValue> = bot_uuids
            .iter()
            .map(|uuid| DbValue::from(uuid.as_str()))
            .collect();
        params.push(self.env_param());
        let rows = self
            .query(
                "authority_humans_check",
                DbStatement::with_params(
                    format!(
                        "SELECT bot_uuid FROM bcs_bots \
                         WHERE {} \
                           AND actor_kind = 'human' \
                           AND env = ? \
                           AND COALESCE(is_deleted, 0) = 0",
                        self.bot_id_in(user_ids.len())
                    )
                    .as_str(),
                    params,
                ),
            )
            .await?;
        let mut found = BTreeSet::new();
        for row in &rows {
            let bot_uuid = crate::required_string(row, "bot_uuid")?;
            if let Some(user_id) = human_user_id_of_actor(&bot_uuid) {
                found.insert(user_id);
            }
        }
        Ok(user_ids
            .iter()
            .filter(|user| !found.contains(**user))
            .map(|user| (*user).to_string())
            .collect())
    }

    /// Members of one (already shape-normalized) input
    /// that carry no live Human ACTOR row, chunked at the
    /// statement bound (spec §5.2: ≤100 principals per
    /// SQL statement). The lenient sync filters them out
    /// of the applied remainder instead of failing.
    async fn missing_input_humans(
        &self,
        actor_ids: &[String],
    ) -> ServiceResult<Vec<String>> {
        let mut missing = Vec::new();
        for chunk in actor_ids.chunks(MANAGER_CHUNK) {
            let user_ids: Vec<String> = chunk
                .iter()
                .map(|actor| {
                    human_user_id_of_actor(actor)
                        .expect("normalized actor shape")
                })
                .collect();
            let refs: Vec<&str> =
                user_ids.iter().map(String::as_str).collect();
            missing.extend(self.pre_read_missing_humans(&refs).await?);
        }
        Ok(missing)
    }

    /// Owner-excluded current manager set, read with
    /// bounded pagination (≤100 rows/page; the
    /// capacity doc budgeted ≤101 pages at the frozen
    /// stock bound).
    async fn pre_read_current_set(
        &self,
        bot_id: &str,
        owner: &str,
    ) -> ServiceResult<Vec<String>> {
        let mut current = Vec::new();
        let mut offset: u64 = 0;
        loop {
            let rows = self
                .query(
                    "authority_managers_preread_page",
                    DbStatement::with_params(
                        format!(
                            "SELECT from_id FROM edge_grants \
                             WHERE env = ? \
                               AND grant_kind = 'manager' \
                               AND grant_ref_id = 0 AND rules IS NULL \
                               AND status = 'approved' AND {} \
                               AND {} \
                             ORDER BY {} LIMIT ? OFFSET ?",
                            self.to_id_eq(),
                            self.from_id_neq(),
                            self.from_id_order()
                        )
                        .as_str(),
                        vec![
                            self.env_param(),
                            DbValue::from(bot_id),
                            DbValue::from(owner),
                            DbValue::from(MANAGER_CHUNK as u64),
                            DbValue::from(offset),
                        ],
                    ),
                )
                .await?;
            let page: Vec<String> = rows
                .iter()
                .map(|row| crate::required_string(row, "from_id"))
                .collect::<ServiceResult<_>>()?;
            let page_len = page.len() as u64;
            current.extend(page);
            if current.len() as u64 > MANAGER_STOCK_BOUND {
                return Err(stock_limit_error(current.len() as u64));
            }
            if page_len < MANAGER_CHUNK as u64 {
                return Ok(current);
            }
            offset += page_len;
        }
    }

    /// Edge row states for member chunks (≤100 ids
    /// per statement).
    async fn pre_read_member_states(
        &self,
        bot_id: &str,
        members: &[String],
    ) -> ServiceResult<HashMap<String, EdgeRowState>> {
        let mut states = HashMap::new();
        for chunk in members.chunks(MANAGER_CHUNK) {
            let mut params = vec![self.env_param(), DbValue::from(bot_id)];
            params.extend(
                chunk.iter().map(|member| DbValue::from(member.as_str())),
            );
            let rows = self
                .query(
                    "authority_member_states_chunk",
                    DbStatement::with_params(
                        format!(
                            "SELECT from_id, status FROM edge_grants \
                             WHERE env = ? AND {} \
                               AND grant_kind = 'manager' \
                               AND grant_ref_id = 0 AND rules IS NULL \
                               AND {}",
                            self.to_id_eq(),
                            self.from_id_in(chunk.len())
                        )
                        .as_str(),
                        params,
                    ),
                )
                .await?;
            for row in &rows {
                let from_id = crate::required_string(row, "from_id")?;
                let status = crate::required_string(row, "status")?;
                let state = match status.as_str() {
                    "approved" => EdgeRowState::Approved,
                    "revoked" => EdgeRowState::Revoked,
                    other => {
                        return Err(ServiceError::InternalError(
                            format!(
                                "bot authority: unexpected manager edge status '{other}'"
                            ),
                        ));
                    }
                };
                states.insert(from_id, state);
            }
        }
        Ok(states)
    }

    /// Final manager count (owner excluded) for a
    /// committed `edit_manager` result.
    async fn manager_count_excluding_owner(
        &self,
        bot_id: &str,
    ) -> ServiceResult<u64> {
        let owner = self.pre_read_owner(bot_id).await?;
        self.query_u64(
            "authority_manager_count_total",
            format!(
                "SELECT COUNT(*) AS total FROM edge_grants \
                 WHERE env = ? AND grant_kind = 'manager' \
                   AND grant_ref_id = 0 AND rules IS NULL \
                   AND status = 'approved' AND {} AND {}",
                self.to_id_eq(),
                self.from_id_neq()
            )
            .as_str(),
            vec![
                self.env_param(),
                DbValue::from(bot_id),
                DbValue::from(owner),
            ],
        )
        .await
    }

    // ============ transaction steps ============

    /// Step 0: bot row lock read.
    fn bot_lock_step(&self, bot_id: &str) -> DbTransactionStep {
        DbTransactionStep::Query(DbStatement::with_params(
            format!(
                "SELECT bot_uuid FROM bcs_bots \
                 WHERE {} AND env = ? \
                   AND COALESCE(is_deleted, 0) = 0 \
                   AND ownership_version > 0{}",
                self.bot_id_eq(),
                self.lock_suffix()
            )
            .as_str(),
            vec![DbValue::from(bot_id), self.env_param()],
        ))
    }

    /// Step 1: owner guard, consuming the bot row's
    /// `bot_uuid`. Exactly one approved owner;
    /// exposes `owner_id` to later exclusions.
    fn owner_guard_step(&self, bot_step: usize) -> DbTransactionStep {
        DbTransactionStep::Query(DbStatement::with_transaction_params(
            format!(
                "SELECT t.owner_id, t.owner_n FROM \
                   (SELECT MIN(from_id) AS owner_id, COUNT(*) AS owner_n \
                    FROM edge_grants \
                    WHERE env = ? AND {} \
                      AND grant_kind = 'owner' AND grant_ref_id = 0 \
                      AND rules IS NULL AND status = 'approved') t \
                 WHERE t.owner_n = 1{}",
                self.to_id_eq(),
                self.lock_suffix()
            )
            .as_str(),
            vec![
                DbTransactionParam::value(self.env_param()),
                DbTransactionParam::query_result(
                    bot_step,
                    0,
                    "bot_uuid",
                ),
            ],
        ))
    }

    /// Step 2 (sync): count guard — the current
    /// owner-excluded manager count must equal the
    /// prepared результат `|S|` (0 verifies an empty
    /// set). Consumed later (chunks / trailing).
    fn count_guard_step(&self, builder: &mut TxBuilder, expected_len: u64) {
        builder.steps.push(DbTransactionStep::Query(
            DbStatement::with_transaction_params(
                format!(
                    "SELECT f.c AS ok FROM \
                       (SELECT COUNT(*) AS c FROM edge_grants \
                        WHERE env = ? AND {} \
                          AND grant_kind = 'manager' AND grant_ref_id = 0 \
                          AND rules IS NULL AND status = 'approved' \
                          AND {}) f WHERE f.c = ?{}",
                    self.to_id_eq(),
                    self.from_id_neq(),
                    self.lock_suffix()
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_reference(),
                    builder.owner_reference(),
                    DbTransactionParam::value(DbValue::from(expected_len)),
                ],
            ),
        ));
        let index = builder.steps.len() - 1;
        builder
            .guard_tokens
            .push(DbTransactionParam::query_result(index, 0, "ok"));
    }

    /// One membership chunk guard over a ≤100-sorted-id
    /// chunk of the prepared set.
    fn chunk_guard_step(
        &self,
        builder: &mut TxBuilder,
        chunk: &[String],
    ) {
        let mut params = vec![
            DbTransactionParam::value(self.env_param()),
            builder.bot_reference(),
            builder.owner_reference(),
        ];
        params.extend(
            chunk
                .iter()
                .map(|member| {
                    DbTransactionParam::value(DbValue::from(member.as_str()))
                }),
        );
        params.push(DbTransactionParam::value(DbValue::from(
            chunk.len() as u64,
        )));
        builder.steps.push(DbTransactionStep::Query(
            DbStatement::with_transaction_params(
                format!(
                    "SELECT f.c AS ok FROM \
                       (SELECT COUNT(*) AS c FROM edge_grants \
                        WHERE env = ? AND {} \
                          AND grant_kind = 'manager' AND grant_ref_id = 0 \
                          AND rules IS NULL AND status = 'approved' \
                          AND {} \
                          AND {}) f WHERE f.c = ?{}",
                    self.to_id_eq(),
                    self.from_id_neq(),
                    self.from_id_in(chunk.len()),
                    self.lock_suffix()
                )
                .as_str(),
                params,
            ),
        ));
        let index = builder.steps.len() - 1;
        builder
            .guard_tokens
            .push(DbTransactionParam::query_result(index, 0, "ok"));
    }

    /// Human-existence guard for one added-member
    /// chunk: every member's Human ACTOR row must be
    /// live in `bcs_bots` (`actor_kind = 'human'`,
    /// env-bound, not tombstoned). The registry lives in
    /// a real Bot row now, so the guard read takes the
    /// row lock like every other Bot-table guard — a
    /// raced soft delete fails the guard instead of
    /// riding the RR snapshot (the Bot row is locked
    /// first, spec §7 order).
    fn human_guard_step(&self, builder: &mut TxBuilder, users: &[String]) {
        let bot_uuids: Vec<String> =
            users.iter().map(|user| human_actor_id(user)).collect();
        let mut params: Vec<DbTransactionParam> = bot_uuids
            .iter()
            .map(|uuid| {
                DbTransactionParam::value(DbValue::from(uuid.as_str()))
            })
            .collect();
        params.push(DbTransactionParam::value(self.env_param()));
        params.push(DbTransactionParam::value(DbValue::from(
            users.len() as u64,
        )));
        builder.steps.push(DbTransactionStep::Query(
            DbStatement::with_transaction_params(
                format!(
                    "SELECT t.c AS ok FROM \
                       (SELECT COUNT(*) AS c FROM bcs_bots \
                        WHERE {} \
                          AND actor_kind = 'human' \
                          AND env = ? \
                          AND COALESCE(is_deleted, 0) = 0{}) t \
                     WHERE t.c = ?",
                    self.bot_id_in(users.len()),
                    self.lock_suffix()
                )
                .as_str(),
                params,
            ),
        ));
        let index = builder.steps.len() - 1;
        builder
            .guard_tokens
            .push(DbTransactionParam::query_result(index, 0, "ok"));
    }

    /// Post-state count guard: after the diff writes,
    /// the approved manager count (owner excluded)
    /// equals `|input|`. This replaces the sketch's
    /// sink UPDATE so a zero-diff transaction needs no
    /// write at all.
    fn post_state_guard_step(
        &self,
        builder: &mut TxBuilder,
        expected_input: u64,
    ) {
        builder.steps.push(DbTransactionStep::Query(
            DbStatement::with_transaction_params(
                format!(
                    "SELECT f.c AS ok FROM \
                       (SELECT COUNT(*) AS c FROM edge_grants \
                        WHERE env = ? AND {} \
                          AND grant_kind = 'manager' AND grant_ref_id = 0 \
                          AND rules IS NULL AND status = 'approved' \
                          AND {}) f WHERE f.c = ?{}",
                    self.to_id_eq(),
                    self.from_id_neq(),
                    self.lock_suffix()
                )
                .as_str(),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_reference(),
                    builder.owner_reference(),
                    DbTransactionParam::value(DbValue::from(expected_input)),
                ],
            ),
        ));
        let index = builder.steps.len() - 1;
        builder
            .guard_tokens
            .push(DbTransactionParam::query_result(index, 0, "ok"));
    }

    /// Revoke writes for one removed chunk: audit the
    /// pre-state (`INSERT..SELECT`, ROW_NUMBER
    /// sequences stable by `from_id`), then flip the
    /// status. Both are exactly `chunk.len()` row
    /// writes.
    fn revoke_writes(
        &self,
        builder: &mut TxBuilder,
        header: &AuditHeader,
        bot_id: &str,
        chunk: &[String],
        sequence_base: u64,
    ) {
        let count = chunk.len();
        let mut audit_params: Vec<DbTransactionParam> =
            Vec::with_capacity(9 + count);
        for value in [
            self.env_param(),
            DbValue::from(bot_id),
            DbValue::from(header.mutation_id.as_str()),
            DbValue::from(sequence_base),
            DbValue::from(ACTION_MANAGER_REVOKE),
            DbValue::from(header.actor_kind.as_str()),
            DbValue::from(header.actor_id.as_str()),
        ] {
            audit_params.push(DbTransactionParam::value(value));
        }
        audit_params.push(DbTransactionParam::value(self.env_param()));
        audit_params.push(builder.bot_reference());
        audit_params.push(builder.owner_reference());
        for member in chunk {
            audit_params.push(DbTransactionParam::value(DbValue::from(
                member.as_str(),
            )));
        }
        builder.steps.push(DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_transaction_params(
                revoke_audit_insert_select(self.flavor(), count).as_str(),
                audit_params,
            ),
            expected_affected_rows: count as u64,
        });

        let mut update_params = vec![
            DbTransactionParam::value(self.env_param()),
            builder.bot_reference(),
            builder.owner_reference(),
        ];
        update_params.extend(
            chunk
                .iter()
                .map(|member| {
                    DbTransactionParam::value(DbValue::from(member.as_str()))
                }),
        );
        builder.steps.push(DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_transaction_params(
                format!(
                    "UPDATE edge_grants \
                     SET status = 'revoked', gmt_modified = {} \
                     WHERE env = ? AND {} \
                       AND grant_kind = 'manager' AND grant_ref_id = 0 \
                       AND rules IS NULL AND status = 'approved' \
                       AND {} AND {}",
                    self.gmt_modified_now(),
                    self.to_id_eq(),
                    self.from_id_neq(),
                    self.from_id_in(count)
                )
                .as_str(),
                update_params,
            ),
            expected_affected_rows: count as u64,
        });
    }

    /// Added-side writes for one chunk: audit VALUES
    /// (≤99 rows per statement, F1) for the restores
    /// and inserts, then the restore flip and the
    /// multi-row edge insert. Sequences come from the
    /// sorted-diff assignment.
    #[allow(clippy::too_many_arguments)]
    fn add_writes(
        &self,
        builder: &mut TxBuilder,
        header: &AuditHeader,
        bot_id: &str,
        restores: &[String],
        inserts: &[String],
        sequences: &HashMap<String, u64>,
    ) -> ServiceResult<()> {
        for (members, action, before, after) in [
            (
                restores,
                ACTION_MANAGER_RESTORE,
                Some("revoked"),
                "approved",
            ),
            (
                inserts,
                ACTION_MANAGER_GRANT,
                None,
                "approved",
            ),
        ] {
            if members.is_empty() {
                continue;
            }
            let drafts: Vec<AuditRowDraft> = members
                .iter()
                .map(|member| AuditRowDraft {
                    sequence: sequences
                        .get(member)
                        .copied()
                        .expect("sorted diff assigned every sequence"),
                    subject_id: member.clone(),
                    action,
                    before_state: before,
                    after_state: after,
                })
                .collect();
            for block in drafts.chunks(AUDIT_VALUES_ROWS) {
                let statement = audit_values_statement(
                    block,
                    self.env_param().as_str().unwrap_or_default(),
                    bot_id,
                    header.mutation_id.as_str(),
                    header.actor_kind.as_str(),
                    header.actor_id.as_str(),
                );
                builder.steps.push(DbTransactionStep::ExecuteChecked {
                    statement,
                    expected_affected_rows: block.len() as u64,
                });
            }
        }
        if !restores.is_empty() {
            let mut update_params =
                vec![DbTransactionParam::value(self.env_param()), builder.bot_reference()];
            update_params.extend(
                restores
                    .iter()
                    .map(|member| {
                        DbTransactionParam::value(DbValue::from(member.as_str()))
                    }),
            );
            builder.steps.push(DbTransactionStep::ExecuteChecked {
                statement: DbStatement::with_transaction_params(
                    format!(
                        "UPDATE edge_grants \
                         SET status = 'approved', gmt_modified = {} \
                         WHERE env = ? AND {} \
                           AND grant_kind = 'manager' AND grant_ref_id = 0 \
                           AND rules IS NULL AND status = 'revoked' AND {}",
                        self.gmt_modified_now(),
                        self.to_id_eq(),
                        self.from_id_in(restores.len())
                    )
                    .as_str(),
                    update_params,
                ),
                expected_affected_rows: restores.len() as u64,
            });
        }
        if !inserts.is_empty() {
            let mut sql = String::from(
                "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, \
                 grant_ref_id, rules, status, originator_policy_type) VALUES ",
            );
            let mut insert_params = Vec::with_capacity(inserts.len() * 3);
            for (offset, member) in inserts.iter().enumerate() {
                if offset > 0 {
                    sql.push_str(", ");
                }
                sql.push_str(
                    "(?, ?, ?, 'manager', 0, NULL, 'approved', 'same_as_from')",
                );
                insert_params.push(DbTransactionParam::value(self.env_param()));
                insert_params.push(DbTransactionParam::value(DbValue::from(
                    member.as_str(),
                )));
                insert_params.push(builder.bot_reference());
            }
            builder.steps.push(DbTransactionStep::ExecuteChecked {
                statement: DbStatement::with_transaction_params(
                    sql.as_str(),
                    insert_params,
                ),
                expected_affected_rows: inserts.len() as u64,
            });
        }
        Ok(())
    }

    /// Single write pair for `edit_manager`: audit row
    /// + the status transition for the one target.
    #[allow(clippy::too_many_arguments)]
    fn edit_write(
        &self,
        builder: &mut TxBuilder,
        header: &AuditHeader,
        bot_id: &str,
        target: &str,
        plan: EditPlan,
        sequences: &HashMap<String, u64>,
    ) -> ServiceResult<()> {
        let (action, before, after, update_sql, expected) = match plan {
            EditPlan::NoOp => return Ok(()),
            EditPlan::Restore => (
                ACTION_MANAGER_RESTORE,
                Some("revoked"),
                "approved",
                format!(
                    "UPDATE edge_grants \
                     SET status = 'approved', gmt_modified = {} \
                     WHERE env = ? AND {} \
                       AND grant_kind = 'manager' AND grant_ref_id = 0 \
                       AND rules IS NULL AND status = 'revoked' AND {}",
                    self.gmt_modified_now(),
                    self.to_id_eq(),
                    self.from_id_eq()
                ),
                1u64,
            ),
            EditPlan::Insert => (
                ACTION_MANAGER_GRANT,
                None,
                "approved",
                format!(
                    "INSERT INTO edge_grants (env, from_id, to_id, \
                     grant_kind, grant_ref_id, rules, status, \
                     originator_policy_type) \
                     VALUES (?, ?, ?, 'manager', 0, NULL, 'approved', \
                     'same_as_from')"
                ),
                1u64,
            ),
            EditPlan::Remove => (
                ACTION_MANAGER_REVOKE,
                Some("approved"),
                "revoked",
                format!(
                    "UPDATE edge_grants \
                     SET status = 'revoked', gmt_modified = {} \
                     WHERE env = ? AND {} \
                       AND grant_kind = 'manager' AND grant_ref_id = 0 \
                       AND rules IS NULL AND status = 'approved' AND {}",
                    self.gmt_modified_now(),
                    self.to_id_eq(),
                    self.from_id_eq()
                ),
                1u64,
            ),
        };
        let draft = AuditRowDraft {
            sequence: sequences.get(target).copied().unwrap_or(0),
            subject_id: target.to_string(),
            action,
            before_state: before,
            after_state: after,
        };
        builder.steps.push(DbTransactionStep::ExecuteChecked {
            statement: audit_values_statement(
                &[draft],
                self.env_param().as_str().unwrap_or_default(),
                bot_id,
                header.mutation_id.as_str(),
                header.actor_kind.as_str(),
                header.actor_id.as_str(),
            ),
            expected_affected_rows: 1,
        });
        // The INSERT has (env, from, to) values; the
        // UPDATEs have (env, bot, target) values.
        let write_params: Vec<DbValue> = match plan {
            EditPlan::Insert => vec![
                self.env_param(),
                DbValue::from(target),
                DbValue::from(bot_id),
            ],
            _ => vec![
                self.env_param(),
                DbValue::from(bot_id),
                DbValue::from(target),
            ],
        };
        builder.steps.push(DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                update_sql.as_str(),
                write_params,
            ),
            expected_affected_rows: expected,
        });
        Ok(())
    }
}

// ==================== port implementation ====================

/// Owner received through the manager item endpoints:
/// 409 `owner_not_manager` (spec §5.1).
fn owner_not_manager_target_error() -> ServiceError {
    ServiceError::Conflict(format!(
        "{}: the owner is not addressable as a manager target",
        bcs_service_api::application::v1::bot_authority::ERROR_OWNER_NOT_MANAGER
    ))
}

impl DbBotAuthorityStore {
    /// Single-item manager add/restore/remove (spec
    /// §5.1): Human operator (owner or manager today),
    /// valid-Human target for adds, owner targets
    /// conflict, byte-exact success with no diff/audit
    /// for repeats. The operator's current edge is
    /// re-verified inside the Bot-locked transaction.
    pub(super) async fn edit_manager_impl(
        &self,
        command: ManagerEdit,
    ) -> ServiceResult<ManagerSetChange> {
        // 单项管理 by Humans only; the trusted service
        // path must use sync (spec §5.1/§5.2 split).
        let (operator_actor_id, operator_user_id) = match &command.mutation.actor {
            AuthorityActor::Human { user_id } => {
                (human_actor_id(user_id), user_id.clone())
            }
            _ => {
                return Err(operator_forbidden_error(
                    "single-item manager edit requires a Human actor; \
                     trusted services must use the dedicated sync command"
                        .to_string(),
                ));
            }
        };
        let target_actor = human_user_id_of_actor(&command.user_id)
            .map(|user| human_actor_id(&user))
            .ok_or_else(|| {
                human_unknown_error(format!(
                    "manager target '{}' is not a Human actor id",
                    command.user_id
                ))
            })?;
        let header = AuditHeader::from_mutation(&command.mutation);
        let op = "authority_edit_manager";
        // Per-attempt diagnostics so the budget-exhausted
        // error names WHY each try conflicted (no secrets /
        // caller data; the underlying DbError only).
        let mut conflicts: Vec<String> = Vec::new();
        for attempt in 0..=MAX_RETRIES {
            // Preparation reads: parameters only; the
            // transaction re-locks and re-verifies.
            self.require_write_bot(&command.bot_id).await?;
            let owner = self.pre_read_owner(&command.bot_id).await?;
            if owner == target_actor {
                return Err(owner_not_manager_target_error());
            }
            let operator_edges = self
                .management_edges_for(
                    &command.bot_id,
                    &operator_actor_id,
                )
                .await?;
            if operator_edges.is_empty() {
                return Err(operator_forbidden_error(format!(
                    "operator '{operator_actor_id}' holds no current \
                     owner/manager edge"
                )));
            }
            // Operator-survival gate (spec §3.1): the edge alone
            // authorizes nothing once the operator's Human ACTOR
            // row is gone — a tombstoned operator holding a
            // residual edge and a valid credential must not
            // grant or revoke manager rights. The transaction
            // re-verifies this under the row lock below.
            let operator_missing = self
                .pre_read_missing_humans(&[operator_user_id.as_str()])
                .await?;
            if !operator_missing.is_empty() {
                return Err(operator_forbidden_error(format!(
                    "operator '{operator_actor_id}' is not a live \
                     Human actor"
                )));
            }
            let target_state = self
                .pre_read_member_states(
                    &command.bot_id,
                    std::slice::from_ref(&target_actor),
                )
                .await?
                .get(&target_actor)
                .copied()
                .unwrap_or(EdgeRowState::Absent);
            let (plan, human_check) = match (command.action, target_state) {
                (
                    ManagerEditAction::Grant,
                    EdgeRowState::Approved,
                ) => (EditPlan::NoOp, false),
                (
                    ManagerEditAction::Grant,
                    EdgeRowState::Revoked,
                ) => (EditPlan::Restore, true),
                (
                    ManagerEditAction::Grant,
                    EdgeRowState::Absent,
                ) => (EditPlan::Insert, true),
                (ManagerEditAction::Revoke, EdgeRowState::Approved) => {
                    (EditPlan::Remove, false)
                }
                (ManagerEditAction::Revoke, _) => (EditPlan::NoOp, false),
            };
            // Adds need a valid, existing Human; removals
            // do not (a departed Human's edge may be
            // revoked idempotently).
            let target_user_id =
                human_user_id_of_actor(&target_actor)
                    .expect("validated actor shape above");
            if human_check {
                let missing = self
                    .pre_read_missing_humans(&[target_user_id.as_str()])
                    .await?;
                if !missing.is_empty() {
                    return Err(human_unknown_error(format!(
                        "manager target '{target_user_id}' is not a \
                         valid Human"
                    )));
                }
            }

            // In-transaction plan: expected approved count of the
            // target edge.
            let expected_approved = match plan {
                EditPlan::Remove | EditPlan::NoOp
                    if target_state == EdgeRowState::Approved =>
                {
                    1u64
                }
                _ => {
                    // Removes of absent/revoked targets and
                    // inserts/restores verify there is no
                    // approved row (a raced approval fails
                    // the guard and the retry re-classifies).
                    0u64
                }
            };

            let mut builder = TxBuilder {
                steps: vec![
                    self.bot_lock_step(&command.bot_id),
                    self.owner_guard_step(0),
                ],
                guard_tokens: Vec::new(),
                bot_step: 0,
                owner_step: 1,
            };
            // Operator guard: the caller still holds an
            // approved owner/manager edge under the lock.
            builder.steps.push(DbTransactionStep::Query(
                DbStatement::with_transaction_params(
                    format!(
                        "SELECT e.grant_kind AS ok FROM edge_grants e \
                         WHERE e.env = ? AND {} \
                           AND e.grant_kind IN ('owner', 'manager') \
                           AND e.grant_ref_id = 0 AND e.rules IS NULL \
                           AND e.status = 'approved' \
                           AND {} LIMIT 1{}",
                        self.to_id_eq(),
                        self.from_id_eq(),
                        self.lock_suffix()
                    ),
                    vec![
                        DbTransactionParam::value(self.env_param()),
                        builder.bot_reference(),
                        DbTransactionParam::value(DbValue::from(
                            operator_actor_id.as_str(),
                        )),
                    ],
                ),
            ));
            // Byte-exact from_id on MySQL needs the CAST form.
            let operator_index = builder.steps.len() - 1;
            builder
                .guard_tokens
                .push(DbTransactionParam::query_result(
                    operator_index,
                    0,
                    "ok",
                ));
            // Operator-survival guard under the lock (same
            // bcs_bots Human-row shape as the add-target guard):
            // a deletion racing the write cannot slip a
            // permission change through on a residual edge.
            builder.steps.push(DbTransactionStep::Query(
                DbStatement::with_transaction_params(
                    format!(
                        "SELECT t.c AS ok FROM \
                           (SELECT COUNT(*) AS c FROM bcs_bots \
                            WHERE {} \
                              AND actor_kind = 'human' \
                              AND env = ? \
                              AND COALESCE(is_deleted, 0) = 0{}) t \
                         WHERE t.c = 1",
                        self.bot_id_eq(),
                        self.lock_suffix()
                    )
                    .as_str(),
                    vec![
                        DbTransactionParam::value(DbValue::from(
                            operator_actor_id.as_str(),
                        )),
                        DbTransactionParam::value(self.env_param()),
                    ],
                ),
            ));
            let operator_alive_index = builder.steps.len() - 1;
            builder
                .guard_tokens
                .push(DbTransactionParam::query_result(
                    operator_alive_index,
                    0,
                    "ok",
                ));
            // Owner-target separation guard: the target must
            // stay distinct from the (current, single)
            // owner at write time.
            builder.steps.push(DbTransactionStep::Query(
                DbStatement::with_transaction_params(
                    "SELECT 1 AS ok FROM (SELECT ? AS o, ? AS t) w \
                     WHERE w.o <> w.t"
                        .to_string()
                        .as_str(),
                    vec![
                        TxBuilder::reference(builder.owner_step, "owner_id"),
                        DbTransactionParam::value(DbValue::from(
                            target_actor.as_str(),
                        )),
                    ],
                ),
            ));
            let owner_target_index = builder.steps.len() - 1;
            builder
                .guard_tokens
                .push(DbTransactionParam::query_result(
                    owner_target_index,
                    0,
                    "ok",
                ));
            // Target state guard: approved row presence must
            // equal the classification (1 for removes and
            // approved no-ops, 0 for adds).
            builder.steps.push(DbTransactionStep::Query(
                DbStatement::with_transaction_params(
                    format!(
                        "SELECT f.c AS ok FROM \
                           (SELECT COUNT(*) AS c FROM edge_grants \
                            WHERE env = ? AND {} \
                              AND grant_kind = 'manager' \
                              AND grant_ref_id = 0 AND rules IS NULL \
                              AND status = 'approved' \
                              AND {}) f WHERE f.c = ?{}",
                        self.to_id_eq(),
                        self.from_id_eq(),
                        self.lock_suffix()
                    )
                    .as_str(),
                    vec![
                        DbTransactionParam::value(self.env_param()),
                        builder.bot_reference(),
                        DbTransactionParam::value(DbValue::from(
                            target_actor.as_str(),
                        )),
                        DbTransactionParam::value(DbValue::from(
                            expected_approved,
                        )),
                    ],
                ),
            ));
            let state_index = builder.steps.len() - 1;
            builder
                .guard_tokens
                .push(DbTransactionParam::query_result(
                    state_index,
                    0,
                    "ok",
                ));
            // Human-existence guard for adds: the target's Human
            // ACTOR row must be live in `bcs_bots` — a real
            // Bot-table guard read under the row lock (the
            // Bot row is already locked).
            if human_check {
                builder.steps.push(DbTransactionStep::Query(
                    DbStatement::with_transaction_params(
                        format!(
                            "SELECT t.c AS ok FROM \
                               (SELECT COUNT(*) AS c FROM bcs_bots \
                                WHERE {} \
                                  AND actor_kind = 'human' \
                                  AND env = ? \
                                  AND COALESCE(is_deleted, 0) = 0{}) t \
                             WHERE t.c = 1",
                            self.bot_id_eq(),
                            self.lock_suffix()
                        )
                        .as_str(),
                        vec![
                            DbTransactionParam::value(DbValue::from(
                                human_actor_id(&target_user_id).as_str(),
                            )),
                            DbTransactionParam::value(self.env_param()),
                        ],
                    ),
                ));
                let human_index = builder.steps.len() - 1;
                builder
                    .guard_tokens
                    .push(DbTransactionParam::query_result(
                        human_index,
                        0,
                        "ok",
                    ));
            }
            let mut sequences = HashMap::new();
            if plan != EditPlan::NoOp {
                sequences.insert(target_actor.clone(), 0u64);
                self.edit_write(
                    &mut builder,
                    &header,
                    &command.bot_id,
                    &target_actor,
                    plan,
                    &sequences,
                )?;
            }
            let steps = builder.finish();

            match self
                .db
                .transaction_with_options(
                    steps,
                    DbTransactionOptions::with_deadline(TX_DEADLINE),
                )
                .await
            {
                Ok(_) => {
                    let (added, removed) = match plan {
                        EditPlan::NoOp => (0, 0),
                        EditPlan::Insert | EditPlan::Restore => (1, 0),
                        EditPlan::Remove => (0, 1),
                    };
                    let total = self
                        .manager_count_excluding_owner(&command.bot_id)
                        .await?;
                    return Ok(ManagerSetChange {
                        added,
                        removed,
                        total,
                    });
                }
                Err(err) if is_retryable(&err) => {
                    conflicts.push(format!(
                        "attempt {attempt} edit_tx: {err}"
                    ));
                    continue;
                }
                Err(err) => return Err(map_tx_error(op, err)),
            }
        }
        Err(authority_db_error(
            op,
            DbError::Backend(format!(
                "manager edit kept conflicting; retry budget \
                 exhausted; conflicts: [{conflicts:?}]"
            )),
        ))
    }

    /// Trusted full-set sync (spec §5.2, lenient ruling):
    /// one transaction makes the manager set equal the
    /// valid remainder of the deduped, owner-normalized
    /// input — unknown, deleted or non-Human-shaped
    /// members are filtered out with a warn log, never a
    /// whole-request failure; the pre-read is parameter
    /// preparation, the in-tx guards keep verifying, and
    /// failure of the transaction itself is still
    /// all-or-nothing.
    pub(super) async fn sync_managers_impl(
        &self,
        command: ManagerSync,
    ) -> ServiceResult<ManagerSetChange> {
        // sync 是可信应用专用命令; Humans use the
        // single-item commands.
        if command.mutation.actor.kind() != AuthorityActorKind::Service {
            return Err(operator_forbidden_error(
                "full-set manager sync is trusted-service only; \
                 Human operators use the single-item manager commands"
                    .to_string(),
            ));
        }
        let (typed_input, malformed) = normalize_input(&command.user_ids)?;
        let header = AuditHeader::from_mutation(&command.mutation);
        let op = "authority_sync_managers";
        if !malformed.is_empty() {
            warn!(
                operation = op,
                env = %self.env(),
                bot_id = %command.bot_id,
                mutation_id = %command.mutation.mutation_id,
                skipped = ?malformed,
                "manager sync skipped non-Human-actor input members"
            );
        }
        // Lenient target classification (spec §5.2): a
        // missing/tombstoned target bot is a zeros no-op
        // with a warn log (no audit row, no state change);
        // a version-0 live bot still hard-rejects.
        match self.sync_bot_state(&command.bot_id).await? {
            SyncBotState::Missing => {
                warn!(
                    operation = op,
                    env = %self.env(),
                    bot_id = %command.bot_id,
                    mutation_id = %command.mutation.mutation_id,
                    input_size = command.user_ids.len(),
                    "manager sync target bot is missing; returning zeros"
                );
                return Ok(ManagerSetChange {
                    added: 0,
                    removed: 0,
                    total: 0,
                });
            }
            SyncBotState::Uninitialized => {
                return Err(not_initialized_error(&command.bot_id));
            }
            SyncBotState::Ready => {}
        }
        // Per-attempt diagnostics so the budget-exhausted
        // error names WHY each try conflicted (no secrets /
        // caller data; the underlying DbError only).
        let mut conflicts: Vec<String> = Vec::new();
        for attempt in 0..=MAX_RETRIES {
            // Preparation reads: parameters only.
            let owner = self.pre_read_owner(&command.bot_id).await?;
            // Owner normalization (spec §5.2): the same
            // owner-exclusion predicate as the guards.
            let input_owner_excluded: Vec<String> = typed_input
                .iter()
                .filter(|actor| *actor != &owner)
                .cloned()
                .collect();
            // Lenient partition (spec §5.2): members with no
            // live Human ACTOR row are filtered out of the
            // applied remainder with a warn log — the final
            // set always equals the valid remainder, so an
            // unknown-only input clears the set.
            let missing = self
                .missing_input_humans(&input_owner_excluded)
                .await?;
            if !missing.is_empty() {
                warn!(
                    operation = op,
                    env = %self.env(),
                    bot_id = %command.bot_id,
                    mutation_id = %command.mutation.mutation_id,
                    skipped = ?missing,
                    "manager sync skipped input members with no live \
                     Human actor row"
                );
            }
            let mut valid_input: Vec<String> = Vec::new();
            for actor in &input_owner_excluded {
                let user = human_user_id_of_actor(actor)
                    .expect("normalized actor shape");
                if !missing.contains(&user) {
                    valid_input.push(actor.clone());
                }
            }
            let current =
                self.pre_read_current_set(&command.bot_id, &owner)
                    .await?;
            if current.len() as u64 > MANAGER_STOCK_BOUND {
                return Err(stock_limit_error(current.len() as u64));
            }
            let current_set: BTreeSet<String> =
                current.iter().cloned().collect();
            let input_set: BTreeSet<String> =
                valid_input.iter().cloned().collect();
            let removed: Vec<String> = current
                .iter()
                .filter(|actor| !input_set.contains(*actor))
                .cloned()
                .collect();
            let mut added: Vec<String> = Vec::new();
            let mut restores: Vec<String> = Vec::new();
            let mut inserts: Vec<String> = Vec::new();
            let pending_adds: Vec<String> = valid_input
                .iter()
                .filter(|actor| !current_set.contains(*actor))
                .cloned()
                .collect();
            // The pending adds are a subset of the valid
            // remainder (already registry-filtered); they
            // only classify into restores (revoked row)
            // and fresh inserts.
            if !pending_adds.is_empty() {
                let states = self
                    .pre_read_member_states(
                        &command.bot_id,
                        &pending_adds,
                    )
                    .await?;
                for add in &pending_adds {
                    added.push(add.clone());
                    match states.get(add).copied().unwrap_or(EdgeRowState::Absent)
                    {
                        EdgeRowState::Revoked => restores.push(add.clone()),
                        EdgeRowState::Absent => inserts.push(add.clone()),
                        EdgeRowState::Approved => {}
                    }
                }
            }
            let change = ManagerSetChange {
                added: added.len() as u64,
                removed: removed.len() as u64,
                total: valid_input.len() as u64,
            };
            let sequences = assign_sequences(&removed, &added);

            let mut builder = TxBuilder {
                steps: vec![
                    self.bot_lock_step(&command.bot_id),
                    self.owner_guard_step(0),
                ],
                guard_tokens: Vec::new(),
                bot_step: 0,
                owner_step: 1,
            };
            // Full-set guards: total count (empty set
            // included) + one membership chunk guard per
            // ≤100-sorted-id chunk.
            self.count_guard_step(&mut builder, current.len() as u64);
            let chunks_of_current: Vec<&[String]> =
                current.chunks(MANAGER_CHUNK).collect();
            for chunk in &chunks_of_current {
                self.chunk_guard_step(&mut builder, chunk);
            }
            // Human-existence guards per added chunk (the
            // registry is keyed by the `human_<user_id>`
            // bot_uuid; the adds were already filtered, the
            // guard only re-verifies under the row lock).
            for chunk in added.chunks(MANAGER_CHUNK) {
                let user_chunk: Vec<String> = chunk
                    .iter()
                    .map(|actor| {
                        human_user_id_of_actor(actor)
                            .expect("normalized actor shape")
                    })
                    .collect();
                self.human_guard_step(&mut builder, &user_chunk);
            }
            // Diff writes: revoke-side audit-then-flip per
            // removed chunk，then add-side audit + write per
            // added chunk. Sequences: removed sorted first,
            // then added sorted.
            let mut sequence_cursor = 0u64;
            for chunk in removed.chunks(MANAGER_CHUNK) {
                self.revoke_writes(
                    &mut builder,
                    &header,
                    &command.bot_id,
                    chunk,
                    sequence_cursor,
                );
                sequence_cursor += chunk.len() as u64;
            }
            for chunk in added.chunks(MANAGER_CHUNK) {
                let restores_in_chunk: Vec<String> = restores
                    .iter()
                    .filter(|member| chunk.contains(member))
                    .cloned()
                    .collect();
                let inserts_in_chunk: Vec<String> = inserts
                    .iter()
                    .filter(|member| chunk.contains(member))
                    .cloned()
                    .collect();
                self.add_writes(
                    &mut builder,
                    &header,
                    &command.bot_id,
                    &restores_in_chunk,
                    &inserts_in_chunk,
                    &sequences,
                )?;
            }
            // Post-state re-verification (sketch's sink
            // role) + trailing consumption of every guard.
            // The expected count is the VALID remainder —
            // a lenient sync with skipped members replaces
            // with exactly that set (possibly empty).
            self.post_state_guard_step(
                &mut builder,
                valid_input.len() as u64,
            );
            let steps = builder.finish();

            match self
                .db
                .transaction_with_options(
                    steps,
                    DbTransactionOptions::with_deadline(TX_DEADLINE),
                )
                .await
            {
                Ok(_) => return Ok(change),
                Err(err) if is_retryable(&err) => {
                    conflicts.push(format!(
                        "attempt {attempt} sync_tx: {err}"
                    ));
                    continue;
                }
                Err(err) => return Err(map_tx_error(op, err)),
            }
        }
        Err(authority_db_error(
            op,
            DbError::Backend(format!(
                "manager sync kept conflicting; retry budget \
                 exhausted; conflicts: [{conflicts:?}]"
            )),
        ))
    }
}

impl TxBuilder {
    fn reference(step: usize, column: &'static str) -> DbTransactionParam {
        DbTransactionParam::query_result(step, 0, column)
    }
}
