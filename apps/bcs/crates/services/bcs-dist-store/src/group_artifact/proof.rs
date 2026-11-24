//! Terminal-proof store (T7 hand-off): per-task persistent confirmation with
//! full field matching against the committed archive plus idempotent cold
//! transfer. Uncommitted archives never yield a proof; a projection that
//! does not fit the frame budget can never confirm a public Completed.
use super::*;
use super::finalization::*;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::common::{IdempotentWrite, StoreError};

impl GroupArtifactStore {
    /// T7 retains the exact proof in the owner-fenced checkpoint until this
    /// transfer is read back. SaveResult authorization alone is insufficient.
    async fn verify_terminal_authority(&self, proof: &TerminalProof) -> Result<(), StoreError> {
        use bcs_service_api::port::repo::distributed::session::{CheckpointEnvelope, TaskPhase};
        let document = self.documents.read_document(&self.layout.ckpt(&proof.key.sid)?)
            .await.map_err(|e| crate::error::from_read(&e))?
            .ok_or_else(|| not_applied("terminal_authority_missing"))?;
        let envelope: CheckpointEnvelope = crate::codec::decode_json(document.value)?;
        let checkpoint = &envelope.checkpoint;
        let retained = checkpoint.runner_state.as_ref()
            .filter(|state| state.tid == proof.key.tid)
            .and_then(|state| state.state.value.get("group_terminal"));
        let expected = serde_json::to_value(proof).map_err(|_| invalid("terminal_proof_encoding"))?;
        if checkpoint.format_version != 1 || checkpoint.commit_seq.0 < proof.commit_seq.0
            || retained != Some(&expected)
            || !checkpoint.tasks.iter().any(|task| task.tid == proof.key.tid
                && task.phase == TaskPhase::Done
                && task.result_ref.as_ref() == Some(&proof.result_ref)) {
            return Err(not_applied("terminal_authority_missing"));
        }
        let closure = self.documents.read_document(&proof.closure_ref.key).await
            .map_err(|e| crate::error::from_read(&e))?
            .ok_or_else(|| not_applied("terminal_closure_missing"))?;
        if crate::identity::digest_json(&closure.value) != proof.closure_ref.digest
            || closure.value.get("key") != Some(&serde_json::to_value(&proof.key)
                .map_err(|_| invalid("terminal_proof_encoding"))?)
            || closure.value.get("stopped") != Some(&serde_json::Value::Bool(true)) {
            return Err(invalid("terminal_closure_mismatch"));
        }
        Ok(())
    }
    pub(super) async fn store_confirm_terminal_proof(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        tid: &Tid,
        proof: &TerminalProof,
    ) -> Result<IdempotentWrite, StoreError> {
        let hash = archive_key(scope, group, tid)?;
        let Some(row) = self.read_row(&hash).await? else {
            return Err(not_applied("archive_absent"));
        };
        row.verify_identity(scope, group, tid)?;
        match row.state.as_str() {
            PREPARING => return Err(not_applied("archive_not_committed")),
            EXPIRED => return Err(not_applied("archive_expired")),
            _ => {}
        }
        let archive = stored_request(&row)?;
        if row.commit_id.is_none() { return Err(invalid("archive_corrupt")); }
        if proof.key.scope != archive.round.scope
            || proof.key.group != archive.round.group
            || proof.key.sid != archive.round.sid
            || proof.key.tid != archive.round.tid
            || proof.key.generation != archive.round.generation
            || proof.commit_seq.0 <= archive.record.source_commit.0
            || proof.status != archive.record.status
            || proof.result_ref.key != row.result_key
            || proof.result_ref.digest.0 != row.result_digest
            || proof.schema_version != RoundSchemaVersion::V1
        {
            return Err(invalid("terminal_proof_mismatch"));
        }
        // Never a public Completed when the saved projection does not fit:
        // B+1 only leads to a bounded failure finale.
        if proof.status == TerminalStatus::Completed && row.projection_fits != Some(1) {
            return Err(invalid("archive_projection_overflow"));
        }
        if let Some(existing) = &row.proof_json {
            let confirmed: TerminalProof =
                serde_json::from_str(existing).map_err(|_| invalid("archive_corrupt"))?;
            return Ok(if &confirmed == proof {
                IdempotentWrite::AlreadyApplied
            } else {
                return Err(invalid("terminal_proof_conflict"));
            });
        }
        self.verify_terminal_authority(proof).await?;
        let proof_json = serde_json::to_string(proof).map_err(|_| invalid("archive_encoding"))?;
        let outcome = self
            .db
            .execute(DbStatement::with_params(
                "UPDATE bcs_fsm_task_archives SET proof_json = ?, updated_at = ? \
                 WHERE archive_hash = ? AND state = 'committed' AND proof_json IS NULL",
                vec![
                    DbValue::String(proof_json.clone()),
                    numeric(now_millis()),
                    DbValue::String(hash.clone()),
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
            Some(row) => match &row.proof_json {
                Some(existing) if existing == &proof_json => {
                    Ok(IdempotentWrite::AlreadyApplied)
                }
                Some(_) => Err(invalid("terminal_proof_conflict")),
                None => Err(StoreError::OutcomeUnknown {
                    operation_id: hash,
                }),
            },
            None => Err(not_applied("archive_absent")),
        }
    }

    pub(super) async fn store_get_terminal_proof(
        &self,
        grants: &ArchiveGrants,
        scope: &TaskScope,
        group: &GroupId,
        tid: &Tid,
    ) -> Result<Option<TerminalProof>, StoreError> {
        if !grants.read {
            return Err(not_applied("archive_not_disclosed"));
        }
        let hash = archive_key(scope, group, tid)?;
        let Some(row) = self.read_row(&hash).await? else {
            return Ok(None);
        };
        row.verify_identity(scope, group, tid)?;
        if row.state != COMMITTED {
            return Ok(None);
        }
        let Some(existing) = &row.proof_json else {
            return Ok(None);
        };
        let proof: TerminalProof =
            serde_json::from_str(existing).map_err(|_| invalid("archive_corrupt"))?;
        Ok(Some(proof))
    }
}
