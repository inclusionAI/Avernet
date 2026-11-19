//! Header-only, separately versioned isolation/attempt control CAS.
mod acquire;
mod attempts;
mod history;
mod isolation;
mod recovery;
use super::*;
use bcs_service_api::port::repo::distributed::{admission::*, control::*};
use serde_json::Value;
use std::time::Duration;

pub(super) struct Header {
    version: Version,
    raw: Value,
    snapshot: ControlSnapshot,
}
impl CoordinationStore {
    async fn control_header(&self, sid: &Sid) -> Result<Option<Header>, StoreError> {
        let key = self.layout.ckpt(sid)?;
        let Some(value) = self.read::<Value>(&key).await? else {
            return if self.known_checkpoint(sid)? {
                Err(not_applied("known_checkpoint_missing"))
            } else {
                Ok(None)
            };
        };
        self.observe_checkpoint(sid, false)?;
        let owner: ShardOwner = decode_json(
            value
                .value
                .get("owner")
                .cloned()
                .ok_or_else(|| invalid("control_owner_missing"))?,
        )?;
        let head: CommitSeq = decode_json(
            value
                .value
                .pointer("/checkpoint/commit_seq")
                .cloned()
                .ok_or_else(|| invalid("control_head_missing"))?,
        )?;
        let control = value
            .value
            .get("control")
            .cloned()
            .map(decode_json)
            .transpose()?
            .unwrap_or_default();
        let checkpoint_digest = crate::identity::digest_json(&value.value["checkpoint"]);
        Ok(Some(Header {
            version: value.version,
            raw: value.value,
            snapshot: ControlSnapshot {
                checkpoint_digest,
                owner: SessionOwnership {
                    sid: sid.clone(),
                    shard: KeyLayout::shard_of(sid)?,
                    owner,
                },
                head,
                control,
            },
        }))
    }
    fn check_control(header: &Header, guard: &ControlGuard) -> Option<ControlOutcome> {
        if header.snapshot.owner != guard.owner {
            Some(ControlOutcome::Fenced)
        } else if header.snapshot.head != guard.expected_head {
            Some(ControlOutcome::HeadConflict)
        } else {
            None
        }
    }
    fn check_revision(header: &Header, guard: &ControlGuard) -> Option<ControlOutcome> {
        (header.snapshot.control.revision != guard.expected_revision)
            .then_some(ControlOutcome::RevisionConflict)
    }
    async fn save_control(&self, header: &mut Header, operation: &str) -> Result<bool, StoreError> {
        header.snapshot.control.revision = header
            .snapshot
            .control
            .revision
            .checked_add(1)
            .ok_or_else(|| invalid("control_revision_exhausted"))?;
        let encoded = encode_json(&header.snapshot.control)?;
        if serde_json::to_vec(&encoded)
            .map_err(|_| invalid("control_encoding"))?
            .len()
            > capacity::MAX_CONTROL_BYTES
        {
            return Err(invalid("control_byte_budget"));
        }
        header.raw["control"] = encoded;
        if let Some(recovery) = header
            .raw
            .get_mut("recovery")
            .and_then(Value::as_object_mut)
        {
            recovery.insert(
                "quarantined".into(),
                (!matches!(header.snapshot.control.mode, ControlMode::Normal)).into(),
            );
        }
        // Invalidate before awaiting: cancellation/lost acknowledgement can
        // also leave control newer than the typed cache. Preserve existence.
        let sid = &header.snapshot.owner.sid;
        self.observe_checkpoint(sid, true)?;
        let version = self
            .write(
                &self.layout.ckpt(sid)?,
                Some(header.version),
                &header.raw,
                operation,
            )
            .await?;
        if let Some(version) = version
            && let Ok(value) = serde_json::from_value::<StoredCheckpoint>(header.raw.clone())
            && value.checkpoint.format_version == 1
        {
            self.remember(sid, Versioned { version, value })?;
        }
        Ok(version.is_some())
    }
    async fn retain_attempt(&self, sid: &Sid, attempt: &RiskAttempt) -> Result<(), StoreError> {
        let key = self.layout.control_attempt(sid, &attempt.identity.id)?;
        let expected = if let Some(stored) = self.read::<RiskAttempt>(&key).await? {
            if stored.value == *attempt {
                return Ok(());
            }
            // A prepared pending receipt may race the active attempt's finish.
            // Only that same identity can acquire its first confirmed outcome;
            // an existing outcome is immutable.
            if stored.value.identity != attempt.identity
                || stored.value.replay != attempt.replay
                || stored.value.outcome.is_some()
                || attempt.outcome.is_none()
            {
                return Err(invalid("attempt_history_conflict"));
            }
            Some(stored.version)
        } else {
            None
        };
        if self
            .write(&key, expected, attempt, &attempt.identity.id.0)
            .await?
            .is_some()
        {
            return Ok(());
        }
        match self.read::<RiskAttempt>(&key).await? {
            Some(stored) if stored.value == *attempt => Ok(()),
            _ => Err(unknown(&attempt.identity.id.0)),
        }
    }
}
fn validate_text(value: &str) -> Result<(), StoreError> {
    if value.is_empty() || value.len() > 256 {
        return Err(invalid("control_identity"));
    }
    Ok(())
}
fn isolation_mut(mode: &mut ControlMode) -> Option<&mut IsolationRecord> {
    match mode {
        ControlMode::Quarantined { isolation }
        | ControlMode::Blocked { isolation }
        | ControlMode::Recovering { isolation, .. } => Some(isolation),
        ControlMode::Normal => None,
    }
}
fn isolation(mode: &ControlMode) -> Option<&IsolationRecord> {
    match mode {
        ControlMode::Quarantined { isolation }
        | ControlMode::Blocked { isolation }
        | ControlMode::Recovering { isolation, .. } => Some(isolation),
        ControlMode::Normal => None,
    }
}
async fn budget<T>(
    id: &str,
    future: impl Future<Output = Result<T, StoreError>>,
) -> Result<T, StoreError> {
    tokio::time::timeout(Duration::from_millis(500), future)
        .await
        .map_err(|_| unknown(id))?
}
async fn backoff(attempt: usize) -> Result<(), StoreError> {
    if attempt > 0 {
        tokio::time::sleep(Duration::from_millis((5u64 << (attempt - 1)).min(200))).await;
    }
    Ok(())
}
