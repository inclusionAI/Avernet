//! Node-isolated interaction history over the host-selected DbPlugin (plan T3).
//!
//! The log is append-only and addressed by one logical node's own identity —
//! never by Bot, never session-transcript-shared. `id` is the global append
//! order and doubles as the per-node order, so a committed boundary
//! (`HistoryCut`) names an immutable prefix and later appends are invisible
//! at that cut by construction. Because rows are immutable and the boundary
//! is fixed, the bounded newest-selection is a pure function of
//! (cut, budget rows), so a repeat freeze only compares those two values
//! against the pin — the replay path never touches raw history. Physical
//! tables come from migration 048/050 `fsm_node_history` (both dialects).
//! Hash keys bind index width; full node and pin identities are re-checked on
//! every read for collision safety.
//!
//! DB cost per method (SQLite/MySQL, one DbPlugin connection):
//! - `committed_cut`: 1 indexed aggregate read (`MAX(id)` per node-key hash).
//! - `append_once`: 1 indexed lookup (≤1 row) + 1 INSERT; an identical retry
//!   resolves on the lookup with no second row.
//! - `read_at`: 1 indexed bounded scan (`id <= cut`, `LIMIT max_page_rows+1`,
//!   ≤101 rows); ordering comes from the index — no N+1, no full scans.
//! - `freeze_node_input`: 1 pin lookup by hash key; replay answers from the
//!   pin alone (0 raw-history reads); a first freeze adds 1 bounded scan and
//!   1 INSERT; a pin-race loser resolves the winner with one extra lookup.
//! - `release_node_input`: 1 UPDATE by hash key, plus at most one read-back
//!   to distinguish already-released from never-frozen.
use crate::identity::{digest_fields, hex_of};
use async_trait::async_trait;
use bcs_db_api::{DbPlugin, DbRow, DbStatement, DbValue};
use bcs_dist_model::{
    Digest, GroupId, InteractionId, NodeId, Payload, RoundKey, Sid, TaskScope, WorkflowRef,
};
use bcs_service_api::port::repo::distributed::common::{IdempotentWrite, StoreError};
use bcs_service_api::port::repo::distributed::group_round::RoundBudgetProfile;
use bcs_service_api::port::repo::distributed::node_history::{
    CODE_HISTORY_CUT_INVALID, CODE_NODE_HISTORY_CORRUPT, CODE_NODE_INPUT_PIN_ABSENT,
    CODE_NODE_INPUT_PIN_CONFLICT, CODE_NODE_INPUT_PIN_RELEASED, CODE_NODE_KEY_INVALID, HistoryCut,
    HistoryPage, NodeHistoryEntry, NodeHistoryKey, NodeHistoryRepo, NodeHistoryRecord, PinnedHistory,
};
use serde::de::DeserializeOwned;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

/// Pin lifecycle states; a released pin row stays as a tombstone so a late
/// freeze of the same round input fails explicitly instead of re-reading
/// history at the same cut.
const PIN_STATE_PINNED: &str = "pinned";
const PIN_STATE_RELEASED: &str = "released";

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.to_owned() }
}

fn not_applied(code: &str) -> StoreError {
    StoreError::NotApplied { code: code.to_owned() }
}

fn outcome_unknown(operation_id: String) -> StoreError {
    StoreError::OutcomeUnknown { operation_id }
}

/// Deserialize a stored document; corrupt bytes are an honest error, never
/// silently skipped content.
fn stored<T: DeserializeOwned>(json: &str) -> Result<T, StoreError> {
    serde_json::from_str(json).map_err(|_| invalid(CODE_NODE_HISTORY_CORRUPT))
}

/// Canonical digest over the selected entries in order — the ordered digest
/// pinned with the frozen input and re-verified on every load.
fn ordered_digest(records: &[NodeHistoryRecord]) -> Result<Digest, StoreError> {
    let value =
        serde_json::to_value(records).map_err(|_| invalid(CODE_NODE_HISTORY_CORRUPT))?;
    Ok(crate::identity::digest_json(&value))
}

/// Every node-key component must be present; a shared/Bot-scoped fallback does
/// not exist, so a broken composite fails closed.
fn validate_key(key: &NodeHistoryKey) -> Result<(), StoreError> {
    if key.components().iter().any(|field| field.is_empty()) {
        return Err(invalid(CODE_NODE_KEY_INVALID));
    }
    Ok(())
}

fn validate_entry(entry: &NodeHistoryEntry) -> Result<(), StoreError> {
    if let Payload::Ref(reference) = &entry.content
        && (reference.key.is_empty() || reference.digest.0.is_empty())
    {
        return Err(invalid("node_entry_invalid"));
    }
    Ok(())
}

/// Hex SHA-256 of the length-prefixed key components (64 ascii chars).
fn node_key_hash(key: &NodeHistoryKey) -> String {
    let fields = key.components();
    let borrowed: Vec<&[u8]> = fields.iter().map(|field| field.as_bytes()).collect();
    hex_of(&digest_fields(&borrowed)).to_owned()
}

fn cut_digest(cut: &HistoryCut) -> Digest {
    let fields = cut.components();
    let borrowed: Vec<&[u8]> = fields.iter().map(|field| field.as_bytes()).collect();
    digest_fields(&borrowed)
}

/// The pin key hash binds (scope, group, sid, tid, generation, node).
fn pin_key_hash(round: &RoundKey, node: &NodeId) -> String {
    let generation = round.generation.get().to_string();
    let fields = [
        "fsm-node-pin:v1".as_bytes(),
        round.scope.tenant.0.as_bytes(),
        round.scope.principal.0.as_bytes(),
        round.group.0.as_bytes(),
        round.sid.0.as_bytes(),
        round.tid.0.as_bytes(),
        generation.as_bytes(),
        node.0.as_bytes(),
    ];
    hex_of(&digest_fields(&fields)).to_owned()
}

fn now_millis() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|since| since.as_millis() as i64)
        .unwrap_or(0)
}

/// Validate the cut as the admission handoff: bound to exactly this node key
/// and self-consistent. The boundary value itself is authoritative caller
/// input; the store never re-derives or refreshes it from live state.
fn validate_cut(key: &NodeHistoryKey, cut: &HistoryCut) -> Result<(), StoreError> {
    if &cut.history != key || cut_digest(cut) != cut.digest {
        return Err(invalid(CODE_HISTORY_CUT_INVALID));
    }
    Ok(())
}

fn page_rows(budget: &RoundBudgetProfile) -> Result<u64, StoreError> {
    if budget.max_page_rows == 0 {
        return Err(invalid("node_history_budget"));
    }
    Ok(u64::from(budget.max_page_rows))
}

/// Column reader with the corrupt-bytes error already in scope.
fn text_of(row: &DbRow, column: &str) -> Result<String, StoreError> {
    row.get_string(column)
        .map_err(|_| invalid(CODE_NODE_HISTORY_CORRUPT))?
        .ok_or_else(|| invalid(CODE_NODE_HISTORY_CORRUPT))
}

/// Rebuild the stored full node identity for hash-collision checks.
fn stored_node_key(row: &DbRow) -> Result<NodeHistoryKey, StoreError> {
    Ok(NodeHistoryKey {
        scope: TaskScope {
            tenant: bcs_dist_model::TenantId(text_of(row, "scope_tenant")?),
            principal: bcs_dist_model::PrincipalId(text_of(row, "scope_principal")?),
        },
        group: GroupId(text_of(row, "group_id")?),
        sid: Sid(text_of(row, "sid")?),
        workflow_ref: WorkflowRef {
            definition_id: text_of(row, "workflow_id")?,
            revision: text_of(row, "workflow_revision")?,
            digest: Digest(text_of(row, "workflow_digest")?),
        },
        node_id: NodeId(text_of(row, "node_id")?),
    })
}

/// Verify a stored pin row's identity columns against the caller's pin key.
fn stored_pin_identity(row: &DbRow, round: &RoundKey, node: &NodeId) -> Result<(), StoreError> {
    let generation = u64::try_from(
        row.get_i64("round_generation")
            .map_err(|_| invalid(CODE_NODE_HISTORY_CORRUPT))?
            .ok_or_else(|| invalid(CODE_NODE_HISTORY_CORRUPT))?,
    )
    .map_err(|_| invalid(CODE_NODE_HISTORY_CORRUPT))?;
    if text_of(row, "scope_tenant")? != round.scope.tenant.0
        || text_of(row, "scope_principal")? != round.scope.principal.0
        || text_of(row, "group_id")? != round.group.0
        || text_of(row, "sid")? != round.sid.0
        || text_of(row, "tid")? != round.tid.0
        || text_of(row, "node_id")? != node.0
        || generation != round.generation.get()
    {
        return Err(invalid("node_input_pin_hash_collision"));
    }
    Ok(())
}

struct PinRow {
    cut_json: String,
    budget_rows: u64,
    entries: Vec<NodeHistoryRecord>,
    history_digest: Digest,
    truncated: bool,
    state: String,
}

pub struct DbNodeHistoryStore {
    db: Arc<dyn DbPlugin>,
}

impl DbNodeHistoryStore {
    /// The caller runs the dialect's `fsm_node_history` migration first; a
    /// missing table surfaces as an honest store error, never as empty data.
    pub fn new(db: Arc<dyn DbPlugin>) -> Result<Self, StoreError> {
        Ok(Self { db })
    }

    async fn lookup_row(
        &self,
        key: &NodeHistoryKey,
        interaction_id: &InteractionId,
    ) -> Result<Option<String>, StoreError> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT entry_json, scope_tenant, scope_principal, group_id, sid, \
                 workflow_id, workflow_revision, workflow_digest, node_id \
                 FROM bcs_fsm_node_history WHERE node_key_hash = ? AND interaction_id = ? LIMIT 1",
                vec![
                    DbValue::String(node_key_hash(key)),
                    DbValue::String(interaction_id.0.clone()),
                ],
            ))
            .await
            .map_err(|_| not_applied("node_history_read"))?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        if stored_node_key(&row)? != *key {
            return Err(invalid("node_history_hash_collision"));
        }
        Ok(Some(text_of(&row, "entry_json")?))
    }

    /// Indexed bounded cut-scoped tail read: the newest entries at or before
    /// the boundary, returned in append order with the truncation flag.
    async fn read_prefix(
        &self,
        key: &NodeHistoryKey,
        cut: &HistoryCut,
        rows_budget: u64,
    ) -> Result<(Vec<NodeHistoryRecord>, bool), StoreError> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT entry_json, scope_tenant, scope_principal, group_id, sid, \
                 workflow_id, workflow_revision, workflow_digest, node_id, interaction_id \
                 FROM bcs_fsm_node_history WHERE node_key_hash = ? AND id <= ? \
                 ORDER BY id DESC LIMIT ?",
                vec![
                    DbValue::String(node_key_hash(key)),
                    DbValue::U64(cut.boundary),
                    DbValue::U64(rows_budget + 1),
                ],
            ))
            .await
            .map_err(|_| not_applied("node_history_read"))?;
        let truncated = rows.len() > rows_budget as usize;
        let mut records = Vec::with_capacity(rows.len().min(rows_budget as usize));
        for row in rows.into_iter().take(rows_budget as usize).rev() {
            if stored_node_key(&row)? != *key {
                return Err(invalid("node_history_hash_collision"));
            }
            let entry: NodeHistoryEntry = stored(&text_of(&row, "entry_json")?)?;
            records.push(NodeHistoryRecord {
                interaction_id: InteractionId(text_of(&row, "interaction_id")?),
                entry,
            });
        }
        Ok((records, truncated))
    }

    /// Look up one pin by its hash key. On every load the stored entries are
    /// re-verified against the stored ordered digest — dispatch loads a
    /// verified frozen version, never a bare re-read.
    async fn lookup_pin(
        &self,
        round: &RoundKey,
        node: &NodeId,
    ) -> Result<Option<PinRow>, StoreError> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT cut_json, entries_json, history_digest, truncated, budget_rows, state, \
                 scope_tenant, scope_principal, group_id, sid, tid, round_generation, node_id \
                 FROM bcs_fsm_node_input_pins WHERE pin_key_hash = ? LIMIT 1",
                vec![DbValue::String(pin_key_hash(round, node))],
            ))
            .await
            .map_err(|_| not_applied("node_history_read"))?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        stored_pin_identity(&row, round, node)?;
        let entries: Vec<NodeHistoryRecord> = stored(&text_of(&row, "entries_json")?)?;
        let digest = ordered_digest(&entries)?;
        if digest.0 != text_of(&row, "history_digest")? {
            return Err(invalid(CODE_NODE_HISTORY_CORRUPT));
        }
        let budget_rows = u64::try_from(
            row.get_i64("budget_rows")
                .map_err(|_| invalid(CODE_NODE_HISTORY_CORRUPT))?
                .ok_or_else(|| invalid(CODE_NODE_HISTORY_CORRUPT))?,
        )
        .map_err(|_| invalid(CODE_NODE_HISTORY_CORRUPT))?;
        Ok(Some(PinRow {
            cut_json: text_of(&row, "cut_json")?,
            budget_rows,
            entries,
            history_digest: digest,
            truncated: row
                .get_bool("truncated")
                .map_err(|_| invalid(CODE_NODE_HISTORY_CORRUPT))?
                .unwrap_or(false),
            state: text_of(&row, "state")?,
        }))
    }

    /// Replay or reject against an existing pin. Because history is append-only
    /// and the boundary is fixed, the newest-entries selection is a pure
    /// function of (cut, budget rows), so comparing those two values is exact
    /// — the stored selection must be the one the same arguments produce.
    fn compare_pin(
        existing: &PinRow,
        cut: &HistoryCut,
        budget_rows: u64,
    ) -> Result<PinnedHistory, StoreError> {
        if existing.state == PIN_STATE_RELEASED {
            // A released pin is closed forever: never a bare read at the same
            // cut, never a silent re-materialization.
            return Err(not_applied(CODE_NODE_INPUT_PIN_RELEASED));
        }
        let cut_json =
            serde_json::to_string(cut).map_err(|_| invalid(CODE_NODE_HISTORY_CORRUPT))?;
        if existing.cut_json != cut_json || existing.budget_rows != budget_rows {
            return Err(invalid(CODE_NODE_INPUT_PIN_CONFLICT));
        }
        Ok(PinnedHistory {
            entries: existing.entries.clone(),
            ordered_digest: existing.history_digest.clone(),
            truncated: existing.truncated,
        })
    }

    async fn insert_pin(
        &self,
        round: &RoundKey,
        node: &NodeId,
        cut: &HistoryCut,
        budget_rows: u64,
        entries: &[NodeHistoryRecord],
        truncated: bool,
    ) -> Result<PinnedHistory, StoreError> {
        let cut_json = serde_json::to_string(cut).map_err(|_| invalid(CODE_NODE_HISTORY_CORRUPT))?;
        let entries_json =
            serde_json::to_string(entries).map_err(|_| invalid(CODE_NODE_HISTORY_CORRUPT))?;
        let history_digest = ordered_digest(entries)?;
        let outcome = self
            .db
            .execute(DbStatement::with_params(
                "INSERT INTO bcs_fsm_node_input_pins \
                 (pin_key_hash, scope_tenant, scope_principal, group_id, sid, tid, round_generation, \
                  node_id, cut_json, entries_json, history_digest, truncated, budget_rows, state, \
                  pinned_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                vec![
                    DbValue::String(pin_key_hash(round, node)),
                    DbValue::String(round.scope.tenant.0.clone()),
                    DbValue::String(round.scope.principal.0.clone()),
                    DbValue::String(round.group.0.clone()),
                    DbValue::String(round.sid.0.clone()),
                    DbValue::String(round.tid.0.clone()),
                    DbValue::U64(round.generation.get()),
                    DbValue::String(node.0.clone()),
                    DbValue::String(cut_json),
                    DbValue::String(entries_json),
                    DbValue::String(history_digest.0.clone()),
                    DbValue::Bool(truncated),
                    DbValue::U64(budget_rows),
                    DbValue::String(PIN_STATE_PINNED.to_owned()),
                    DbValue::I64(now_millis()),
                ],
            ))
            .await;
        if matches!(&outcome, Ok(result) if result.affected_rows == 1) {
            return Ok(PinnedHistory {
                entries: entries.to_vec(),
                ordered_digest: history_digest,
                truncated,
            });
        }
        // A concurrent freeze (or a released tombstone) owns the unique pin
        // key: resolve by exact comparison; divergence is a conflict and an
        // unresolved outcome is unknown — never an automatic replacement.
        match self.lookup_pin(round, node).await {
            Ok(Some(existing)) => Self::compare_pin(&existing, cut, budget_rows),
            Ok(None) => Err(outcome_unknown(format!(
                "freeze:{}#{}",
                round.tid.0, round.generation.get()
            ))),
            Err(_) => Err(outcome_unknown(format!(
                "freeze:{}#{}",
                round.tid.0, round.generation.get()
            ))),
        }
    }
}

#[async_trait]
impl NodeHistoryRepo for DbNodeHistoryStore {
    async fn committed_cut(&self, history: &NodeHistoryKey) -> Result<HistoryCut, StoreError> {
        validate_key(history)?;
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT COALESCE(MAX(id), 0) AS boundary FROM bcs_fsm_node_history \
                 WHERE node_key_hash = ?",
                vec![DbValue::String(node_key_hash(history))],
            ))
            .await
            .map_err(|_| not_applied("node_history_read"))?;
        let boundary = rows
            .into_iter()
            .next()
            .and_then(|row| row.get_i64("boundary").ok().flatten())
            .ok_or_else(|| invalid(CODE_NODE_HISTORY_CORRUPT))?;
        let boundary = u64::try_from(boundary).map_err(|_| invalid(CODE_NODE_HISTORY_CORRUPT))?;
        let seed = HistoryCut {
            history: history.clone(),
            boundary,
            digest: Digest(String::new()),
        };
        Ok(HistoryCut {
            history: history.clone(),
            boundary,
            digest: cut_digest(&seed),
        })
    }

    async fn append_once(
        &self,
        history: &NodeHistoryKey,
        interaction_id: &InteractionId,
        entry: &NodeHistoryEntry,
    ) -> Result<IdempotentWrite, StoreError> {
        validate_key(history)?;
        validate_entry(entry)?;
        if interaction_id.0.is_empty() {
            return Err(invalid(CODE_NODE_KEY_INVALID));
        }
        let entry_json =
            serde_json::to_string(entry).map_err(|_| invalid(CODE_NODE_HISTORY_CORRUPT))?;
        if let Some(stored_json) = self.lookup_row(history, interaction_id).await? {
            // Stable interaction identity: an identical retry records once.
            return Ok(if stored_json == entry_json {
                IdempotentWrite::AlreadyApplied
            } else {
                IdempotentWrite::ContentConflict
            });
        }
        let outcome = self
            .db
            .execute(DbStatement::with_params(
                "INSERT INTO bcs_fsm_node_history \
                 (node_key_hash, scope_tenant, scope_principal, group_id, sid, workflow_id, \
                  workflow_revision, workflow_digest, node_id, interaction_id, entry_json, \
                  appended_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                vec![
                    DbValue::String(node_key_hash(history)),
                    DbValue::String(history.scope.tenant.0.clone()),
                    DbValue::String(history.scope.principal.0.clone()),
                    DbValue::String(history.group.0.clone()),
                    DbValue::String(history.sid.0.clone()),
                    DbValue::String(history.workflow_ref.definition_id.clone()),
                    DbValue::String(history.workflow_ref.revision.clone()),
                    DbValue::String(history.workflow_ref.digest.0.clone()),
                    DbValue::String(history.node_id.0.clone()),
                    DbValue::String(interaction_id.0.clone()),
                    DbValue::String(entry_json.clone()),
                    DbValue::I64(now_millis()),
                ],
            ))
            .await;
        if matches!(&outcome, Ok(result) if result.affected_rows == 1) {
            return Ok(IdempotentWrite::Applied);
        }
        // The unique row exists (a lost race with an identical retry) or the
        // outcome is unknown; resolve by exact identity, never by re-inserting.
        match self.lookup_row(history, interaction_id).await {
            Ok(Some(stored_json)) => Ok(if stored_json == entry_json {
                IdempotentWrite::AlreadyApplied
            } else {
                IdempotentWrite::ContentConflict
            }),
            Ok(None) | Err(_) => Err(outcome_unknown(interaction_id.0.clone())),
        }
    }

    async fn read_at(
        &self,
        history: &NodeHistoryKey,
        cut: &HistoryCut,
        budget: &RoundBudgetProfile,
    ) -> Result<HistoryPage, StoreError> {
        validate_key(history)?;
        validate_cut(history, cut)?;
        let rows_budget = page_rows(budget)?;
        let (entries, truncated) = self.read_prefix(history, cut, rows_budget).await?;
        Ok(HistoryPage { entries, truncated })
    }

    async fn freeze_node_input(
        &self,
        round: &RoundKey,
        node: &NodeId,
        cut: &HistoryCut,
        budget: &RoundBudgetProfile,
    ) -> Result<PinnedHistory, StoreError> {
        if node.0.is_empty() {
            return Err(invalid(CODE_NODE_KEY_INVALID));
        }
        validate_key(&cut.history)?;
        validate_cut(&cut.history, cut)?;
        // The explicit upstream binding is THIS round's input: the cut must
        // belong to the same scope/group/Sid and to this exact node.
        if cut.history.scope != round.scope
            || cut.history.group != round.group
            || cut.history.sid != round.sid
            || cut.history.node_id != *node
        {
            return Err(invalid("node_input_binding"));
        }
        let rows_budget = page_rows(budget)?;
        if let Some(existing) = self.lookup_pin(round, node).await? {
            // Replay path: the pin alone answers; raw history is never
            // consulted, so same-round retry records appended after the
            // freeze are invisible by construction.
            return Self::compare_pin(&existing, cut, rows_budget);
        }
        let (entries, truncated) = self
            .read_prefix(&cut.history, cut, rows_budget)
            .await?;
        self.insert_pin(round, node, cut, rows_budget, &entries, truncated)
            .await
    }

    async fn load_node_input(
        &self,
        round: &RoundKey,
        node: &NodeId,
        cut: &HistoryCut,
        budget: &RoundBudgetProfile,
    ) -> Result<PinnedHistory, StoreError> {
        validate_key(&cut.history)?;
        validate_cut(&cut.history, cut)?;
        if cut.history.scope != round.scope || cut.history.group != round.group
            || cut.history.sid != round.sid || cut.history.node_id != *node
        {
            return Err(invalid("node_input_binding"));
        }
        let existing = self.lookup_pin(round, node).await?
            .ok_or_else(|| invalid(CODE_NODE_INPUT_PIN_ABSENT))?;
        Self::compare_pin(&existing, cut, page_rows(budget)?)
    }

    async fn release_node_input(
        &self,
        round: &RoundKey,
        node: &NodeId,
    ) -> Result<IdempotentWrite, StoreError> {
        if node.0.is_empty() {
            return Err(invalid(CODE_NODE_KEY_INVALID));
        }
        let outcome = self
            .db
            .execute(DbStatement::with_params(
                "UPDATE bcs_fsm_node_input_pins SET state = ? WHERE pin_key_hash = ? AND state = ?",
                vec![
                    DbValue::String(PIN_STATE_RELEASED.to_owned()),
                    DbValue::String(pin_key_hash(round, node)),
                    DbValue::String(PIN_STATE_PINNED.to_owned()),
                ],
            ))
            .await;
        if matches!(&outcome, Ok(result) if result.affected_rows == 1) {
            return Ok(IdempotentWrite::Applied);
        }
        // Zero affected rows distinguishes already-released from never-frozen
        // by one read-back; a failed read-back is an unknown outcome, never a
        // guessed success.
        match self.lookup_pin(round, node).await {
            Ok(Some(existing)) if existing.state == PIN_STATE_RELEASED => {
                Ok(IdempotentWrite::AlreadyApplied)
            }
            Ok(Some(_)) | Err(_) => Err(outcome_unknown(format!(
                "release:{}#{}",
                round.tid.0, round.generation.get()
            ))),
            Ok(None) => Err(not_applied(CODE_NODE_INPUT_PIN_ABSENT)),
        }
    }
}
