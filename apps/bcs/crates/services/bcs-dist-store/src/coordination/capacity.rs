//! The control plane cannot consume the business checkpoint's reserved frame.
use super::*;
use bcs_service_api::port::repo::distributed::control::SessionControl;
use serde_json::{Value, json};

pub(super) const MAX_CONTROL_BYTES: usize = 48 * 1024;
pub(super) const MAX_RECOVERY_ATTEMPTS_BYTES: usize = 8 * 1024;
pub(super) const MAX_RECOVERY_ID_BYTES: usize = 256;

pub(super) fn encoded_bytes(value: &impl Serialize) -> Result<usize, StoreError> {
    serde_json::to_vec(value)
        .map(|bytes| bytes.len())
        .map_err(|_| invalid("capacity_encoding"))
}

/// Serialize the actual provider row, substituting the separately enforced
/// aggregate byte bounds for control and retained legacy attempt identities.
/// Owner key segments allow quotes/backslashes, so their encoded maximum is
/// twice the 256-byte raw segment allowance, plus the string delimiters.
pub(super) fn maximum_envelope_bytes() -> Result<usize, StoreError> {
    let row = StoredCheckpoint {
        envelope: CheckpointEnvelope {
            owner: ShardOwner {
                instance: Instance {
                    node: "\\".repeat(256),
                    incarnation: "\\".repeat(256),
                },
                epoch: WorkerEpoch(u64::MAX),
            },
            checkpoint: Checkpoint {
                format_version: 2,
                commit_seq: CommitSeq(0),
                consumed: None,
                tasks: Default::default(),
                downstream_generation: 0,
                pending_cancel_did: None,
                runner_state: None,
                dispatch_seq: Default::default(),
                deadlines: vec![],
                deadline_history: Default::default(),
                view_drafts: Default::default(),
                last_event_id: None,
                outbox: None,
            },
            recovery: RecoveryMetadata {
                takeover_streak: u32::MAX,
                last_attempt: Some(RequestId("x".repeat(MAX_RECOVERY_ID_BYTES - 2))),
                quarantined: false,
                last_active_at: TimestampMillis(i64::MIN),
            },
        },
        recovery_attempts: vec![],
        control: SessionControl::default(),
    };
    let mut encoded = encode_json(&row)?;
    encoded["control"] = json!("x".repeat(MAX_CONTROL_BYTES - 2));
    encoded["recovery_attempts"] = json!(["x".repeat(MAX_RECOVERY_ATTEMPTS_BYTES - 4)]);
    Ok(encoded_bytes(&encoded)?.saturating_sub(encoded_bytes(&encoded["checkpoint"])?))
}

pub(super) fn validate_config(config: &CoordinationStoreConfig) -> Result<(), StoreError> {
    let bytes = maximum_envelope_bytes()?
        .checked_add(config.storage_envelope_bytes)
        .ok_or_else(|| invalid("checkpoint_envelope_byte_budget"))?;
    if bytes > config.checkpoint_capacity.max_envelope_bytes {
        return Err(invalid("checkpoint_envelope_capacity"));
    }
    Ok(())
}

pub(super) fn validate_row(
    data: &Value,
    config: &CoordinationStoreConfig,
) -> Result<(), StoreError> {
    let bytes = encoded_bytes(data)?
        .checked_add(config.storage_envelope_bytes)
        .ok_or_else(|| invalid("checkpoint_document_byte_budget"))?;
    if bytes > config.checkpoint_capacity.max_document_bytes {
        return Err(invalid("checkpoint_document_byte_budget"));
    }
    if let Some(checkpoint) = data.get("checkpoint") {
        if bytes.saturating_sub(encoded_bytes(checkpoint)?)
            > config.checkpoint_capacity.max_envelope_bytes
        {
            return Err(invalid("checkpoint_envelope_byte_budget"));
        }
        if data
            .get("control")
            .map(encoded_bytes)
            .transpose()?
            .is_some_and(|n| n > MAX_CONTROL_BYTES)
        {
            return Err(invalid("control_byte_budget"));
        }
        if data
            .get("recovery_attempts")
            .map(encoded_bytes)
            .transpose()?
            .is_some_and(|n| n > MAX_RECOVERY_ATTEMPTS_BYTES)
        {
            return Err(invalid("recovery_attempt_byte_budget"));
        }
    }
    Ok(())
}
