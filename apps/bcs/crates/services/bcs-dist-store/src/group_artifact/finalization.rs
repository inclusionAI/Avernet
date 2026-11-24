//! Finalization-side storage helpers: validation of the incoming archive
//! request, the pre-object-write pin row, idempotent immutable object
//! writes, archive-row reads, page authorization resolution and the
//! decoded archive-row shape. Private to the group-artifact module tree.
use super::*;
use bcs_db_api::{DbRow, DbStatement, DbValue};
use bcs_dist_model::{Digest, GroupId, TaskScope, Tid};
use bcs_service_api::port::repo::distributed::common::{IdempotentWrite, StoreError};
use bcs_service_api::port::repo::distributed::result::{ArchiveGrants, TaskFinalArchive};
use crate::archive::{object_hash, result_blob_reference, validate_record};

impl GroupArtifactStore {

    pub(super) fn validate_request(&self, archive: &TaskFinalArchive) -> Result<(), StoreError> {
        if archive.schema_version != 2 {
            return Err(StoreError::UnsupportedFormat {
                format: archive.schema_version,
            });
        }
        validate_record(&archive.record)?;
        let round = &archive.round;
        if round.scope != archive.record.scope
            || round.sid != archive.record.sid
            || round.tid != archive.record.tid
        {
            return Err(invalid("archive_round"));
        }
        archive_identity(&round)?;
        if archive.artifacts.len() > self.budget.profile.max_page_rows as usize {
            return Err(invalid("archive_part_count"));
        }
        let mut previous_order = 0u32;
        for part in &archive.artifacts {
            if part.artifact_id.0.is_empty() || part.artifact_id.0.len() > 256 {
                return Err(invalid("archive_part_id"));
            }
            if part.order.get() <= previous_order {
                return Err(invalid("archive_part_order"));
            }
            previous_order = part.order.get();
            let bytes = serde_json::to_vec(&part.part).map_err(|_| invalid("archive_part"))?;
            if bytes.len() > self.budget.profile.feed_event_bytes {
                return Err(invalid("archive_part_budget"));
            }
        }
        if archive.result_digest != result_blob_reference(&archive.record)?.digest {
            return Err(invalid("archive_result_digest"));
        }
        Ok(())
    }

    /// Pin/lease row registered before any object write. Keeps the intended
    /// blob references (via the full request) so reclamation never eats an
    /// object a pending archive will reference.
    pub(super) async fn register_pin(
        &self,
        row: &ArchiveRow,
        archive_json: &str,
        content_keys: &[String],
    ) -> Result<(), StoreError> {
        // The pin row AND its blob references land in ONE transaction, so a
        // preparing archive always leaves the complete set of content keys a
        // reclamation sweep must respect. Pin references with identical
        // content are deduplicated before the write.
        let mut seen = std::collections::HashSet::new();
        let mut steps = vec![DbTransactionStep::Execute(DbStatement::with_params(
            "INSERT INTO bcs_fsm_task_archives (archive_hash, scope_tenant, \
             scope_principal, group_id, sid, tid, round_generation, state, request_digest, \
             result_digest, result_key, commit_id, archive_json, measured_bytes, \
             updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, ?)",
            vec![
                DbValue::String(row.archive_hash.clone()),
                DbValue::String(row.scope_tenant.clone()),
                DbValue::String(row.scope_principal.clone()),
                DbValue::String(row.group_id.clone()),
                DbValue::String(row.sid.clone()),
                DbValue::String(row.tid.clone()),
                numeric(row.round_generation),
                DbValue::String(PREPARING.into()),
                DbValue::String(row.request_digest.clone()),
                DbValue::String(row.result_digest.clone()),
                DbValue::String(row.result_key.clone()),
                DbValue::String(archive_json.to_owned()),
                numeric(now_millis()),
            ],
        ))];
        for content_key in content_keys {
            if !seen.insert(content_key.clone()) {
                continue;
            }
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                "INSERT INTO bcs_fsm_task_artifact_pins (archive_hash, content_hash, \
                 content_key) VALUES (?, ?, ?)",
                vec![
                    DbValue::String(row.archive_hash.clone()),
                    DbValue::String(object_hash(content_key)),
                    DbValue::String(content_key.clone()),
                ],
            )));
        }
        match self.db.transaction(steps).await {
            Ok(_) => return Ok(()),
            Err(_) => {}
        }
        // A concurrent same-identity insert resolved first: re-read to
        // reconcile; absence still cannot prove our write failed.
        match self.read_row(&row.archive_hash).await? {
            Some(existing) if existing.state == PREPARING
                && existing.request_digest == row.request_digest =>
            {
                Ok(())
            }
            _ => Err(StoreError::OutcomeUnknown {
                operation_id: row.archive_hash.clone(),
            }),
        }
    }

    pub(super) async fn read_row(&self, archive_hash: &str) -> Result<Option<ArchiveRow>, StoreError> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT archive_hash, scope_tenant, scope_principal, group_id, sid, tid, \
                 round_generation, state, request_digest, result_digest, result_key, commit_id, \
                 archive_json, budget_version, projection_digest, measured_bytes, \
                 projection_fits, proof_json FROM bcs_fsm_task_archives \
                 WHERE archive_hash = ? LIMIT 1",
                vec![DbValue::String(archive_hash.to_owned())],
            ))
            .await
            .map_err(|_| not_applied("archive_read"))?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        Ok(Some(ArchiveRow::decode(row)?))
    }

    /// Insert one immutable object row idempotently (mirrors the archive).
    pub(super) async fn put_object(
        &self,
        key: &str,
        digest: &Digest,
        bytes: &[u8],
        source: &str,
    ) -> Result<IdempotentWrite, StoreError> {
        if key.is_empty() || key.len() > 1024 || key.chars().any(char::is_control) {
            return Err(invalid("blob_key"));
        }
        if source.len() > 16 * 1024 {
            return Err(invalid("archive_provenance_size"));
        }
        if let Some((stored_digest, stored_source)) = self.read_object_meta(key).await? {
            return Ok(match (stored_digest == digest.0, stored_source == source) {
                (true, true) => IdempotentWrite::AlreadyApplied,
                _ => IdempotentWrite::ContentConflict,
            });
        }
        let outcome = self
            .db
            .execute(DbStatement::with_params(
                "INSERT INTO bcs_dist_immutable_objects (namespace_hash, object_hash, \
                 namespace_key, object_key, content_digest, source_json, content_bytes) \
                 VALUES (?, ?, ?, ?, ?, ?, ?)",
                vec![
                    DbValue::String(self.namespace_hash.clone()),
                    DbValue::String(object_hash(key)),
                    DbValue::String(self.namespace.clone()),
                    DbValue::String(key.to_owned()),
                    DbValue::String(digest.0.clone()),
                    DbValue::String(source.to_owned()),
                    DbValue::Bytes(bytes.to_vec()),
                ],
            ))
            .await;
        if outcome
            .as_ref()
            .is_ok_and(|result| result.affected_rows == 1)
        {
            return Ok(IdempotentWrite::Applied);
        }
        match self.read_object_meta(key).await? {
            Some((stored_digest, stored_source)) => Ok(match (
                stored_digest == digest.0,
                stored_source == source,
            ) {
                (true, true) => IdempotentWrite::AlreadyApplied,
                _ => IdempotentWrite::ContentConflict,
            }),
            None => Err(StoreError::OutcomeUnknown {
                operation_id: object_hash(key),
            }),
        }
    }

    pub(super) async fn read_object_meta(
        &self,
        key: &str,
    ) -> Result<Option<(String, String)>, StoreError> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT namespace_key, object_key, content_digest, source_json \
                 FROM bcs_dist_immutable_objects WHERE namespace_hash = ? AND object_hash = ? \
                 LIMIT 1",
                vec![
                    DbValue::String(self.namespace_hash.clone()),
                    DbValue::String(object_hash(key)),
                ],
            ))
            .await
            .map_err(|_| not_applied("archive_read"))?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        let string = |column: &str| {
            row.get_string(column)
                .map_err(|_| invalid("archive_column"))?
                .ok_or_else(|| invalid("archive_column"))
        };
        if string("namespace_key")? != self.namespace || string("object_key")? != key {
            return Err(invalid("archive_hash_collision"));
        }
        Ok(Some((string("content_digest")?, string("source_json")?)))
    }
    pub(super) async fn deny_or_resolve(
        &self,
        grants: &ArchiveGrants,
        scope: &TaskScope,
        group: &GroupId,
        tids: &[Tid],
    ) -> Result<Vec<String>, StoreError> {
        if !grants.read {
            return Err(not_applied("archive_not_disclosed"));
        }
        let mut placeholders = Vec::new();
        let mut params = Vec::new();
        for tid in tids {
            placeholders.push("?".to_owned());
            params.push(DbValue::String(archive_key(scope, group, tid)?));
        }
        let rows = self
            .db
            .query(DbStatement::with_params(
                format!(
                    "SELECT archive_hash, state FROM bcs_fsm_task_archives \
                     WHERE archive_hash IN ({}) LIMIT ?",
                    placeholders.join(", ")
                ),
                {
                    params.push(DbValue::U64(tids.len() as u64));
                    params
                },
            ))
            .await
            .map_err(|_| not_applied("archive_read"))?;
        rows.into_iter()
            .map(|row| {
                let (hash, state) = ArchiveRow::decode_state(&row)?;
                if state == PREPARING {
                    Err(not_applied("archive_not_committed"))
                } else if state == EXPIRED {
                    Err(not_applied("archive_expired"))
                } else {
                    Ok(hash)
                }
            })
            .collect::<Result<Vec<String>, StoreError>>()
    }
}



pub(super) struct ArchiveRow {
    pub(super) archive_hash: String,
    pub(super) scope_tenant: String,
    pub(super) scope_principal: String,
    pub(super) group_id: String,
    pub(super) sid: String,
    pub(super) tid: String,
    pub(super) round_generation: u64,
    pub(super) state: String,
    pub(super) request_digest: String,
    pub(super) result_digest: String,
    pub(super) result_key: String,
    pub(super) commit_id: Option<String>,
    pub(super) archive_json: Option<String>,
    pub(super) budget_version: Option<u64>,
    pub(super) projection_digest: Option<String>,
    pub(super) measured_bytes: u64,
    pub(super) projection_fits: Option<i64>,
    pub(super) proof_json: Option<String>,
}

impl ArchiveRow {
    fn decode(row: DbRow) -> Result<Self, StoreError> {
        let string = |column: &str| {
            row.get_string(column)
                .map_err(|_| invalid("archive_column"))?
                .ok_or_else(|| invalid("archive_column"))
        };
        let generation = match row.get("round_generation") {
            Some(DbValue::U64(value)) => *value,
            Some(DbValue::I64(value)) if *value >= 0 => *value as u64,
            _ => return Err(invalid("archive_column")),
        };
        let measured = match row.get("measured_bytes") {
            Some(DbValue::U64(value)) => *value,
            Some(DbValue::I64(value)) if *value >= 0 => *value as u64,
            _ => return Err(invalid("archive_column")),
        };
        Ok(Self {
            archive_hash: string("archive_hash")?,
            scope_tenant: string("scope_tenant")?,
            scope_principal: string("scope_principal")?,
            group_id: string("group_id")?,
            sid: string("sid")?,
            tid: string("tid")?,
            round_generation: generation,
            state: string("state")?,
            request_digest: string("request_digest")?,
            result_digest: string("result_digest")?,
            result_key: string("result_key")?,
            commit_id: row.get_string("commit_id").ok().flatten(),
            archive_json: row.get_string("archive_json").ok().flatten(),
            budget_version: match row.get("budget_version") {
                Some(DbValue::U64(value)) => Some(*value),
                Some(DbValue::I64(value)) if *value >= 0 => Some(*value as u64),
                _ => None,
            },
            projection_digest: row.get_string("projection_digest").ok().flatten(),
            measured_bytes: measured,
            projection_fits: match row.get("projection_fits") {
                Some(DbValue::U64(value)) => Some(*value as i64),
                Some(DbValue::I64(value)) => Some(*value),
                _ => None,
            },
            proof_json: row.get_string("proof_json").ok().flatten(),
        })
    }

    fn decode_state(row: &DbRow) -> Result<(String, String), StoreError> {
        let hash = row
            .get_string("archive_hash")
            .map_err(|_| invalid("archive_column"))?
            .ok_or_else(|| invalid("archive_column"))?;
        let state = row
            .get_string("state")
            .map_err(|_| invalid("archive_column"))?
            .ok_or_else(|| invalid("archive_column"))?;
        Ok((hash, state))
    }

    pub(super) fn verify_identity(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        tid: &Tid,
    ) -> Result<(), StoreError> {
        if self.scope_tenant != scope.tenant.0
            || self.scope_principal != scope.principal.0
            || self.group_id != group.0
            || self.tid != tid.0
        {
            return Err(invalid("archive_hash_collision"));
        }
        Ok(())
    }
}

pub(super) fn stored_request(row: &ArchiveRow) -> Result<TaskFinalArchive, StoreError> {
    let json = row.archive_json.as_deref().ok_or_else(|| invalid("archive_corrupt"))?;
    let archive: TaskFinalArchive =
        serde_json::from_str(json).map_err(|_| invalid("archive_corrupt"))?;
    Ok(archive)
}
