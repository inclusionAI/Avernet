//! Per-task immutable final-product archive (plan T4 / Stage 2).
//!
//! Archiving a task's terminal products never confers public eligibility.
//! One `(scope, group, Tid)` owns at most one archive row whose state walks
//! `preparing -> committed -> expired`:
//!
//! - **preparing** is the pin/lease registered BEFORE the first immutable
//!   object write, so a concurrent reclamation sweep can never delete a blob
//!   that a pending archive intends to reference. Preparing rows and their
//!   bytes are readable only for recovery, never as success.
//! - **committed** is written by exactly ONE finishing transaction that writes
//!   the final record (`archive_json`), every ordered artifact part and the
//!   immutable blob references (artifact rows with content-addressed keys,
//!   plus the result object). Any failure leaves the row preparing: no half
//!   archive is ever visible.
//! - **expired** is the reclaim tombstone: it stays readable after proof and
//!   result deletion, so a reclaimed Tid never looks absent and can never be
//!   re-executed as new.
//!
//! The D0-frozen wire-projection contract: before commit success the store
//! bounded-encodes the complete default public projection (GetTask body plus
//! the mandatory state/artifact SSE frames — envelope, escaping, history and
//! every part) through the host-injected pure validator and saves the budget
//! version, projection digest and measured size with the archive. A
//! projection that does not fit can never confirm a Completed proof (the
//! bounded failure finale belongs to the round, not to storage).
//!
//! Authorization is checked before any disclosure: a caller without grants
//! gets one uniform `archive_not_disclosed` denial across absent, preparing,
//! committed and expired tasks, and denied reads issue no storage IO at all.
//!
//! DB cost per method (SQLite/MySQL, one DbPlugin connection; max_page_rows
//! is the injected budget profile's page bound, parts per archive are
//! validated to at most that many rows, and each part's blob to at most one
//! feed-event budget):
//! - `archive_final`: 1 read row (≤1), 1 verifying document read, N part
//!   reads + N object INSERTs (N ≤ max_page_rows, idempotent), 1 result
//!   object probe, then ONE transaction of 1 guarded UPDATE + N part INSERTs
//!   + ≤1 result object INSERT. Rows per archive are therefore bounded, and
//!   a same-content replay resolves on the first read with zero writes.
//! - `get_archived`: 1 indexed read (≤1 row).
//! - `list_archived_tasks`: 1 indexed bounded scan (`LIMIT/OFFSET`, ≤limit
//!   rows, tid column only).
//! - `task_artifacts`: 3 indexed bounded queries — states (≤page), part rows
//!   (≤page*max_page_rows) and object bytes by hash keys (one per distinct
//!   blob). No N+1, no whole-group scans.
//! - `read_task_result`: 2 bounded reads — a `LENGTH` probe, then a
//!   `SUBSTR`-bounded prefix fetch; an oversized object is never materialized
//!   before its length is known.
//! - `mark_task_expired`: 1 guarded UPDATE; resolution re-read ≤1 on no-op.
//! - `reclaim_expired`: 2 probes + ONE transaction: 1 guarded tombstone
//!   reassert (the tombstone is proven readable inside the same transaction
//!   that deletes), ≤1 parts DELETE, 1 result DELETE, N unreferenced-blob
//!   DELETEs guarded by `NOT EXISTS` over remaining live references.
//! - `confirm_terminal_proof`: 1 read + 1 guarded UPDATE; replay resolves on

/// The default pure wire-projection validator lives beside the store, in
/// [`projection`]; the terminal-proof store in [`proof`]; finalization-side
/// row/object persistence helpers in [`finalization`].
pub use projection::JsonProjectionValidator;

mod finalization;
mod proof;
mod projection;
mod publication;

use finalization::*;


use crate::archive::{blob_reference, object_hash, result_blob_reference};
use crate::evidence::{completion_identity, EffectVerifier};
use crate::identity::{digest_fields, hex_of};
use crate::layout::KeyLayout;
use async_trait::async_trait;
use bcs_db_api::{DbPlugin, DbStatement, DbTransactionStep, DbValue};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::common::{
    EffectSource, IdempotentWrite, StoreError,
};
use bcs_service_api::port::repo::distributed::result::*;
use bcs_service_api::port::repo::distributed::session::EffectKind;
use bcs_store_api::document::DocumentStorePlugin;
use std::num::NonZeroU32;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

pub struct GroupArtifactStore {
    db: Arc<dyn DbPlugin>,
    verifier: EffectVerifier,
    documents: Arc<dyn DocumentStorePlugin>,
    layout: KeyLayout,
    namespace: String,
    namespace_hash: String,
    budget: ArchiveBudget,
    validator: Arc<dyn PublicProjectionValidator>,
}

impl GroupArtifactStore {
    /// Build over the same DB plugin and document store as the original
    /// archive; object bytes continue to live in `bcs_dist_immutable_objects`.
    /// The validator and budget are host-injected (wire types stay in the
    /// adapter, plan T8).
    pub fn new(
        db: Arc<dyn DbPlugin>,
        documents: Arc<dyn DocumentStorePlugin>,
        layout: KeyLayout,
        validator: Arc<dyn PublicProjectionValidator>,
        budget: ArchiveBudget,
    ) -> Result<Self, StoreError> {
        if !documents.supports(bcs_store_api::StoreCapability::DocumentCas) {
            return Err(not_applied("document_cas_required"));
        }
        let namespace = layout.namespace_identity();
        let namespace_hash = object_hash(&namespace);
        Ok(Self {
            db,
            verifier: EffectVerifier::new(documents.clone(), layout.clone()),
            documents,
            layout,
            namespace,
            namespace_hash,
            budget,
            validator,
        })
    }
}

/// Content-addressed part blob reference: the key is derived from the digest,
/// which covers only the exact bytes (the domain-separated blob digest), so
/// tasks with equal part bytes share one immutable object.
pub fn part_blob_reference(part_bytes: &[u8]) -> BlobRef {
    let digest = blob_reference("artifact-part", part_bytes).digest;
    BlobRef {
        key: format!("artifact-part:{}", hex_of(&digest)),
        digest,
    }
}

const PREPARING: &str = "preparing";
const COMMITTED: &str = "committed";
const EXPIRED: &str = "expired";

#[async_trait]
impl TaskArchiveRepo for GroupArtifactStore {
    async fn archive_final(
        &self,
        source: EffectSource,
        archive: TaskFinalArchive,
    ) -> Result<SavedArchive, StoreError> {
        self.validate_request(&archive)?;
        if archive.record.sid != source.commit.sid
            || archive.record.source_commit != source.commit.sequence
            || source.effect_id.sid != archive.record.sid
            || source.effect_id.commit != archive.record.source_commit
        {
            return Err(invalid("archive_source"));
        }
        let archive_json =
            serde_json::to_string(&archive).map_err(|_| invalid("archive_encoding"))?;
        let request_digest = digest_fields(&[b"archive:v1", archive_json.as_bytes()]);
        let result_blob = result_blob_reference(&archive.record)?;
        let row = ArchiveRow {
            archive_hash: archive_identity(&archive.round)?,
            scope_tenant: archive.round.scope.tenant.0.clone(),
            scope_principal: archive.round.scope.principal.0.clone(),
            group_id: archive.round.group.0.clone(),
            sid: archive.record.sid.0.clone(),
            tid: archive.record.tid.0.clone(),
            round_generation: archive.round.generation.get(),
            state: PREPARING.into(),
            request_digest: request_digest.0.clone(),
            result_digest: result_blob.digest.0.clone(),
            result_key: result_blob.key.clone(),
            commit_id: None,
            archive_json: Some(archive_json.clone()),
            budget_version: None,
            projection_digest: None,
            measured_bytes: 0,
            projection_fits: None,
            proof_json: None,
        };
        // Same identity and same content replays; different content conflicts;
        // an expired tombstone refuses re-archival entirely.
        if let Some(existing) = self.read_row(&row.archive_hash).await? {
            match existing.state.as_str() {
                COMMITTED => {
                    return Ok(if existing.request_digest == row.request_digest {
                        SavedArchive::AlreadySaved
                    } else {
                        SavedArchive::ContentConflict
                    });
                }
                EXPIRED => return Err(not_applied("archive_tombstone")),
                _ if existing.request_digest != row.request_digest => {
                    return Err(invalid("archive_prepare_conflict"));
                }
                _ => {}
            }
        }
        // Authority: persisted committed effect, exactly like save_final.
        if let Err(verification_error) = self
            .verifier
            .verify(
                &source,
                &EffectKind::SaveResult {
                    record: archive.record.clone(),
                },
            )
            .await
        {
            // A concurrent replay may have committed after our first read.
            return match self.read_row(&row.archive_hash).await? {
                Some(existing)
                    if existing.state == COMMITTED
                        && existing.request_digest == row.request_digest =>
                {
                    Ok(SavedArchive::AlreadySaved)
                }
                _ => Err(verification_error),
            };
        }
        // Wire projection measured before commit success; saved with the
        // archive (budget version, projection digest, measured size).
        let check = self.validator.measure(&self.budget, &archive)?;
        // Derive every part's content-addressed blob key first: the pin must
        // register the complete reference set before the first object write.
        let mut part_rows = Vec::with_capacity(archive.artifacts.len());
        let mut content_keys = Vec::with_capacity(archive.artifacts.len());
        for part in &archive.artifacts {
            let bytes =
                serde_json::to_vec(&part.part).map_err(|_| invalid("archive_part"))?;
            let reference = part_blob_reference(&bytes);
            content_keys.push(reference.key.clone());
            part_rows.push((part, reference.key.clone(), bytes));
        }
        // Pin every referenced immutable blob before the first object write.
        if self.read_row(&row.archive_hash).await?.is_none() {
            self.register_pin(&row, &archive_json, &content_keys).await?;
        }
        // Part blobs resolve idempotently; their references are written in
        // the finishing transaction below.
        for (_, _, bytes) in &part_rows {
            let reference = part_blob_reference(bytes);
            match self
                .put_object(&reference.key, &reference.digest, bytes, "")
                .await?
            {
                IdempotentWrite::Applied | IdempotentWrite::AlreadyApplied => {}
                IdempotentWrite::ContentConflict => {
                    return Err(invalid("archive_object_conflict"));
                }
            }
        }
        // The final result record object is written by the SAME transaction
        // when absent; its provenance is the retained completion evidence.
        let write_result_object =
            self.read_object_meta(&result_blob.key).await?.is_none();
        let provenance = completion_identity(&source)?;
        let mut steps = vec![DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "UPDATE bcs_fsm_task_archives SET state = ?, commit_id = ?, archive_json = ?, \
                 budget_version = ?, projection_digest = ?, measured_bytes = ?, \
                 projection_fits = ?, updated_at = ? WHERE archive_hash = ? AND state = ?",
                vec![
                    DbValue::String(COMMITTED.into()),
                    DbValue::String(source.commit.commit_id.0.clone()),
                    DbValue::String(archive_json.clone()),
                    numeric(check.budget_version),
                    DbValue::String(check.projection_digest.0.clone()),
                    numeric(check.measured_bytes),
                    DbValue::U64(u64::from(check.fits as u8)),
                    numeric(now_millis()),
                    DbValue::String(row.archive_hash.clone()),
                    DbValue::String(PREPARING.into()),
                ],
            ),
            expected_affected_rows: 1,
        }];
        for (part, content_key, _) in &part_rows {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                "INSERT INTO bcs_fsm_task_artifacts (tid, artifact_id, archive_hash, \
                 part_order, content_key) VALUES (?, ?, ?, ?, ?)",
                vec![
                    DbValue::String(row.tid.clone()),
                    DbValue::String(part.artifact_id.0.clone()),
                    DbValue::String(row.archive_hash.clone()),
                    numeric(u64::from(part.order.get())),
                    DbValue::String(content_key.clone()),
                ],
            )));
        }
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "DELETE FROM bcs_fsm_task_artifact_pins WHERE archive_hash = ?",
            vec![DbValue::String(row.archive_hash.clone())],
        )));
        if write_result_object {
            let record_bytes =
                serde_json::to_vec(&archive.record).map_err(|_| invalid("archive_encoding"))?;
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                "INSERT INTO bcs_dist_immutable_objects (namespace_hash, object_hash, \
                 namespace_key, object_key, content_digest, source_json, content_bytes) \
                 VALUES (?, ?, ?, ?, ?, ?, ?)",
                vec![
                    DbValue::String(self.namespace_hash.clone()),
                    DbValue::String(object_hash(&result_blob.key)),
                    DbValue::String(self.namespace.clone()),
                    DbValue::String(result_blob.key.clone()),
                    DbValue::String(result_blob.digest.0.clone()),
                    DbValue::String(provenance),
                    DbValue::Bytes(record_bytes),
                ],
            )));
        }
        match self.db.transaction(steps).await {
            Ok(_) => Ok(SavedArchive::Saved),
            Err(_) => Err(StoreError::OutcomeUnknown {
                operation_id: row.archive_hash,
            }),
        }
    }

    async fn get_archived(
        &self,
        grants: &ArchiveGrants,
        scope: &TaskScope,
        group: &GroupId,
        tid: &Tid,
    ) -> Result<Option<ArchivedTaskView>, StoreError> {
        if !grants.read {
            return Err(not_applied("archive_not_disclosed"));
        }
        let hash = archive_key(scope, group, tid)?;
        let Some(row) = self.read_row(&hash).await? else {
            return Ok(None);
        };
        row.verify_identity(scope, group, tid)?;
        match row.state.as_str() {
            PREPARING => Ok(Some(ArchivedTaskView::Preparing(stored_request(&row)?))),
            EXPIRED => Ok(Some(ArchivedTaskView::Expired {
                result_digest: Digest(row.result_digest),
            })),
            _ => {
                let archive = stored_request(&row)?;
                let budget_version = row.budget_version.ok_or_else(|| invalid("archive_corrupt"))?;
                let projection_digest = row
                    .projection_digest
                    .ok_or_else(|| invalid("archive_corrupt"))?;
                let projection_fits = row.projection_fits.ok_or_else(|| invalid("archive_corrupt"))?;
                Ok(Some(ArchivedTaskView::Committed {
                    archive,
                    check: ProjectionCheck {
                        budget_version,
                        projection_digest: Digest(projection_digest),
                        measured_bytes: row.measured_bytes,
                        fits: projection_fits == 1,
                    },
                }))
            }
        }
    }

    async fn list_archived_tasks(
        &self,
        grants: &ArchiveGrants,
        scope: &TaskScope,
        group: &GroupId,
        offset: u32,
        limit: NonZeroU32,
    ) -> Result<Vec<Tid>, StoreError> {
        if !grants.read {
            return Err(not_applied("archive_not_disclosed"));
        }
        if limit.get() > self.budget.profile.max_page_rows {
            return Err(invalid("archive_page_limit"));
        }
        validate_scope_group(scope, group)?;
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT tid FROM bcs_fsm_task_archives WHERE scope_tenant = ? AND \
                 scope_principal = ? AND group_id = ? AND state = 'committed' \
                 ORDER BY tid LIMIT ? OFFSET ?",
                vec![
                    DbValue::String(scope.tenant.0.clone()),
                    DbValue::String(scope.principal.0.clone()),
                    DbValue::String(group.0.clone()),
                    numeric(u64::from(limit.get())),
                    numeric(u64::from(offset)),
                ],
            ))
            .await
            .map_err(|_| not_applied("archive_read"))?;
        let mut tids = Vec::with_capacity(rows.len());
        for row in rows {
            let tid = row
                .get_string("tid")
                .map_err(|_| invalid("archive_column"))?
                .ok_or_else(|| invalid("archive_column"))?;
            tids.push(Tid(tid));
        }
        Ok(tids)
    }

    async fn task_artifacts(
        &self,
        grants: &ArchiveGrants,
        scope: &TaskScope,
        group: &GroupId,
        tids: &[Tid],
    ) -> Result<Vec<(Tid, ArtifactPart)>, StoreError> {
        if tids.is_empty() {
            if !grants.read {
                return Err(not_applied("archive_not_disclosed"));
            }
            return Ok(Vec::new());
        }
        let hashes = self.deny_or_resolve(grants, scope, group, tids).await?;
        let mut placeholders = Vec::new();
        let mut params = Vec::new();
        for hash in &hashes {
            placeholders.push("?".to_owned());
            params.push(DbValue::String(hash.clone()));
        }
        params.push(numeric(
            (tids.len() as u64).saturating_mul(self.budget.profile.max_page_rows as u64),
        ));
        let rows = self
            .db
            .query(DbStatement::with_params(
                format!(
                    "SELECT tid, artifact_id, part_order, content_key FROM \
                     bcs_fsm_task_artifacts WHERE archive_hash IN ({}) \
                     ORDER BY tid, part_order LIMIT ?",
                    placeholders.join(", ")
                ),
                params,
            ))
            .await
            .map_err(|_| not_applied("archive_read"))?;
        struct PartRef {
            tid: String,
            artifact_id: String,
            order: u32,
            digest: Digest,
            content_key: String,
        }
        let mut references = Vec::with_capacity(rows.len());
        let mut object_keys = Vec::with_capacity(rows.len());
        for row in &rows {
            let string = |column: &str| {
                row.get_string(column)
                    .map_err(|_| invalid("archive_column"))?
                    .ok_or_else(|| invalid("archive_column"))
            };
            let content_key = string("content_key")?;
            let order = match row.get("part_order") {
                Some(DbValue::U64(value)) => *value as u32,
                Some(DbValue::I64(value)) if *value >= 0 => *value as u32,
                _ => return Err(invalid("archive_column")),
            };
            let Some(hex) = content_key.strip_prefix("artifact-part:") else {
                return Err(invalid("archive_corrupt"));
            };
            references.push(PartRef {
                tid: string("tid")?,
                artifact_id: string("artifact_id")?,
                order,
                digest: Digest(format!("sha256:{hex}")),
                content_key: content_key.clone(),
            });
            object_keys.push(object_hash(&content_key));
        }
        // Object bytes by hashed keys: one bounded batched query per page,
        // deduplicated across tasks that share equal part content.
        let mut contents: std::collections::HashMap<String, Vec<u8>> =
            std::collections::HashMap::new();
        if !object_keys.is_empty() {
            let mut object_params = vec![DbValue::String(self.namespace_hash.clone())];
            let mut object_placeholders = Vec::new();
            for key in &object_keys {
                object_placeholders.push("?".to_owned());
                object_params.push(DbValue::String(key.clone()));
            }
            object_params.push(numeric(object_keys.len() as u64 + 1));
            let object_rows = self
                .db
                .query(DbStatement::with_params(
                    format!(
                        "SELECT object_key, content_bytes FROM bcs_dist_immutable_objects \
                         WHERE namespace_hash = ? AND object_hash IN ({}) LIMIT ?",
                        object_placeholders.join(", ")
                    ),
                    object_params,
                ))
                .await
                .map_err(|_| not_applied("archive_read"))?;
            for object_row in object_rows {
                let key = object_row
                    .get_string("object_key")
                    .map_err(|_| invalid("archive_column"))?
                    .ok_or_else(|| invalid("archive_column"))?;
                match object_row.get("content_bytes") {
                    Some(DbValue::Bytes(bytes)) => {
                        contents.insert(key, bytes.clone());
                    }
                    _ => return Err(invalid("archive_bytes")),
                }
            }
        }
        let mut parts = Vec::with_capacity(references.len());
        for reference in &references {
            let Some(bytes) = contents.get(&reference.content_key) else {
                return Err(not_applied("archive_object_missing"));
            };
            if blob_reference(&reference.content_key, bytes).digest != reference.digest {
                return Err(invalid("archive_corrupt_digest"));
            }
            let part: RoundPart =
                serde_json::from_slice(bytes).map_err(|_| invalid("archive_corrupt"))?;
            parts.push((
                Tid(reference.tid.clone()),
                ArtifactPart {
                    artifact_id: ArtifactId(reference.artifact_id.clone()),
                    order: NonZeroU32::new(reference.order)
                        .ok_or_else(|| invalid("archive_column"))?,
                    part,
                },
            ));
        }
        Ok(parts)
    }

    async fn read_task_result(
        &self,
        grants: &ArchiveGrants,
        scope: &TaskScope,
        group: &GroupId,
        tid: &Tid,
        limit: NonZeroU32,
    ) -> Result<Option<(Vec<u8>, u64)>, StoreError> {
        if !grants.read {
            return Err(not_applied("archive_not_disclosed"));
        }
        let hash = archive_key(scope, group, tid)?;
        let Some(row) = self.read_row(&hash).await? else {
            return Ok(None);
        };
        row.verify_identity(scope, group, tid)?;
        match row.state.as_str() {
            PREPARING => return Err(not_applied("archive_not_committed")),
            EXPIRED => return Err(not_applied("archive_expired")),
            _ => {}
        }
        // Length-bounded from the start of IO: probe before materializing.
        let probe = self
            .db
            .query(DbStatement::with_params(
                "SELECT namespace_key, object_key, LENGTH(content_bytes) AS stored_length \
                 FROM bcs_dist_immutable_objects WHERE namespace_hash = ? AND object_hash = ? \
                 LIMIT 1",
                vec![
                    DbValue::String(self.namespace_hash.clone()),
                    DbValue::String(object_hash(&row.result_key)),
                ],
            ))
            .await
            .map_err(|_| not_applied("archive_read"))?;
        let Some(probe_row) = probe.into_iter().next() else {
            return Ok(None);
        };
        let string = |column: &str| {
            probe_row
                .get_string(column)
                .map_err(|_| invalid("archive_column"))?
                .ok_or_else(|| invalid("archive_column"))
        };
        if string("namespace_key")? != self.namespace
            || string("object_key")? != row.result_key
        {
            return Err(invalid("archive_hash_collision"));
        }
        let stored_length = match probe_row.get("stored_length") {
            Some(DbValue::U64(value)) => *value,
            Some(DbValue::I64(value)) if *value >= 0 => *value as u64,
            _ => return Err(invalid("archive_column")),
        };
        let fetch = self
            .db
            .query(DbStatement::with_params(
                "SELECT SUBSTR(content_bytes, 1, ?) AS prefix FROM \
                 bcs_dist_immutable_objects WHERE namespace_hash = ? AND object_hash = ? \
                 LIMIT 1",
                vec![
                    numeric(u64::from(limit.get().min(stored_length as u32))),
                    DbValue::String(self.namespace_hash.clone()),
                    DbValue::String(object_hash(&row.result_key)),
                ],
            ))
            .await
            .map_err(|_| not_applied("archive_read"))?;
        let Some(fetch_row) = fetch.into_iter().next() else {
            return Ok(None);
        };
        match fetch_row.get("prefix") {
            Some(DbValue::Bytes(prefix)) => Ok(Some((prefix.clone(), stored_length))),
            _ => Err(invalid("archive_bytes")),
        }
    }

    async fn mark_task_expired(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        tid: &Tid,
    ) -> Result<IdempotentWrite, StoreError> {
        let hash = archive_key(scope, group, tid)?;
        let outcome = self
            .db
            .execute(DbStatement::with_params(
                "UPDATE bcs_fsm_task_archives SET state = ?, archive_json = NULL, proof_json = \
                 NULL, updated_at = ? WHERE archive_hash = ? AND scope_tenant = ? AND \
                 scope_principal = ? AND group_id = ? AND tid = ? AND state = 'committed'",
                vec![
                    DbValue::String(EXPIRED.into()),
                    numeric(now_millis()),
                    DbValue::String(hash.clone()),
                    DbValue::String(scope.tenant.0.clone()),
                    DbValue::String(scope.principal.0.clone()),
                    DbValue::String(group.0.clone()),
                    DbValue::String(tid.0.clone()),
                ],
            ))
            .await;
        if outcome
            .as_ref()
            .is_ok_and(|result| result.affected_rows == 1)
        {
            return Ok(IdempotentWrite::Applied);
        }
        match self.read_row(&hash).await? {
            Some(row) => match row.state.as_str() {
                EXPIRED => Ok(IdempotentWrite::AlreadyApplied),
                PREPARING => Err(not_applied("archive_not_committed")),
                _ => Err(invalid("archive_state")),
            },
            None => Err(not_applied("archive_absent")),
        }
    }

    async fn reclaim_expired(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        tid: &Tid,
    ) -> Result<IdempotentWrite, StoreError> {
        let hash = archive_key(scope, group, tid)?;
        let Some(row) = self.read_row(&hash).await? else {
            return Err(not_applied("archive_absent"));
        };
        row.verify_identity(scope, group, tid)?;
        if row.state != EXPIRED {
            return Err(not_applied("archive_not_expired"));
        }
        if row.projection_digest.is_none() {
            // Physical marker: reclamation deleted artifact parts, the result
            // object and the projection record, and only the tombstone row
            // survives. A lost delete reply resolves right here.
            return Ok(IdempotentWrite::AlreadyApplied);
        }
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT content_key FROM bcs_fsm_task_artifacts WHERE archive_hash = ?",
                vec![DbValue::String(hash.clone())],
            ))
            .await
            .map_err(|_| not_applied("archive_read"))?;
        let content_keys: Vec<String> = rows
            .into_iter()
            .map(|row| {
                row.get_string("content_key")
                    .map_err(|_| invalid("archive_column"))?
                    .ok_or_else(|| invalid("archive_column"))
            })
            .collect::<Result<_, _>>()?;
        // Tombstone-first: the expired tombstone is re-proven readable inside
        // the SAME transaction that deletes; a vanished tombstone rolls back
        // every deletion. `updated_at + 1` is genuinely changing, so the
        // checked row count is reliable on both dialects.
        let mut steps = vec![DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "UPDATE bcs_fsm_task_archives SET budget_version = NULL, \
                 projection_digest = NULL, projection_fits = NULL, measured_bytes = 0, \
                 updated_at = updated_at + 1 WHERE archive_hash = ? AND state = ?",
                vec![
                    DbValue::String(hash.clone()),
                    DbValue::String(EXPIRED.into()),
                ],
            ),
            expected_affected_rows: 1,
        }];
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "DELETE FROM bcs_fsm_task_artifacts WHERE archive_hash = ? AND tid = ?",
            vec![
                DbValue::String(hash.clone()),
                DbValue::String(tid.0.clone()),
            ],
        )));
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "DELETE FROM bcs_dist_immutable_objects WHERE namespace_hash = ? AND \
             object_hash = ? AND object_key = ?",
            vec![
                DbValue::String(self.namespace_hash.clone()),
                DbValue::String(object_hash(&row.result_key)),
                DbValue::String(row.result_key.clone()),
            ],
        )));
        for content_key in &content_keys {
            // A blob survives reclamation when ANY live reference remains:
            // a committed artifact row, or a preparing archive's pin
            // (its part rows do not exist yet — pins are the only thing that
            // can protect an unwritten-committed reference).
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                "DELETE FROM bcs_dist_immutable_objects WHERE namespace_hash = ? AND \
                 object_hash = ? AND object_key = ? AND NOT EXISTS \
                 (SELECT 1 FROM bcs_fsm_task_artifacts WHERE content_key = ?) AND \
                 NOT EXISTS (SELECT 1 FROM bcs_fsm_task_artifact_pins WHERE content_hash = ? \
                 AND content_key = ?)",
                vec![
                    DbValue::String(self.namespace_hash.clone()),
                    DbValue::String(object_hash(content_key)),
                    DbValue::String(content_key.clone()),
                    DbValue::String(content_key.clone()),
                    DbValue::String(object_hash(content_key)),
                    DbValue::String(content_key.clone()),
                ],
            )));
        }
        match self.db.transaction(steps).await {
            Ok(_) => Ok(IdempotentWrite::Applied),
            Err(_) => Err(StoreError::OutcomeUnknown {
                operation_id: hash.clone(),
            }),
        }
    }

    async fn attach_publication(
        &self,
        round: &RoundKey,
        publication: &PublicPublication,
    ) -> Result<AttachedPublication, StoreError> {
        self.attach_publication_impl(round, publication).await
    }

    async fn confirm_terminal_proof(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        tid: &Tid,
        proof: &TerminalProof,
    ) -> Result<IdempotentWrite, StoreError> {
        self.store_confirm_terminal_proof(scope, group, tid, proof).await
    }

    async fn get_terminal_proof(
        &self,
        grants: &ArchiveGrants,
        scope: &TaskScope,
        group: &GroupId,
        tid: &Tid,
    ) -> Result<Option<TerminalProof>, StoreError> {
        self.store_get_terminal_proof(grants, scope, group, tid).await
    }
}

fn archive_identity(round: &RoundKey) -> Result<String, StoreError> {
    for value in [
        &round.scope.tenant.0,
        &round.scope.principal.0,
        &round.group.0,
        &round.tid.0,
    ] {
        if value.is_empty() || value.len() > 256 {
            return Err(invalid("archive_identity"));
        }
    }
    Ok(hex_of(&digest_fields(&[
        b"fsm-task-archive:v1",
        round.scope.tenant.0.as_bytes(),
        round.scope.principal.0.as_bytes(),
        round.group.0.as_bytes(),
        round.tid.0.as_bytes(),
    ]))
    .to_owned())
}

/// The (scope, group, Tid) row key. The round generation does not key the
/// archive: one task owns exactly one final archive across replays.
fn archive_key(scope: &TaskScope, group: &GroupId, tid: &Tid) -> Result<String, StoreError> {
    validate_scope_group(scope, group)?;
    if tid.0.is_empty() || tid.0.len() > 256 {
        return Err(invalid("archive_identity"));
    }
    Ok(hex_of(&digest_fields(&[
        b"fsm-task-archive:v1",
        scope.tenant.0.as_bytes(),
        scope.principal.0.as_bytes(),
        group.0.as_bytes(),
        tid.0.as_bytes(),
    ]))
    .to_owned())
}

fn validate_scope_group(scope: &TaskScope, group: &GroupId) -> Result<(), StoreError> {
    for value in [&scope.tenant.0, &scope.principal.0, &group.0] {
        if value.is_empty() || value.len() > 256 {
            return Err(invalid("archive_identity"));
        }
    }
    Ok(())
}

fn numeric(value: u64) -> DbValue {
    DbValue::U64(value)
}

fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_millis() as u64)
        .unwrap_or(0)
}

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.into() }
}

fn not_applied(code: &str) -> StoreError {
    StoreError::NotApplied { code: code.into() }
}
