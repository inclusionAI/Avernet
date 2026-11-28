//! Protect a shared downstream conversation until an aborted send terminates.
use crate::runtime::err;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::session::{Checkpoint, EffectKind};
use bcs_service_api::types::dist_runtime::RuntimeError;
use std::time::Duration;

pub(super) fn arm(cp: &mut Checkpoint, grace: Duration) -> Result<(), RuntimeError> {
    if cp.pending_cancel_did.is_some() {
        return Ok(());
    }
    let Some(batch) = &cp.outbox else {
        return Ok(());
    };
    let Some(record) = batch.effects.iter().find_map(|effect| match &effect.kind {
        EffectKind::SaveResult { record } => Some(record),
        _ => None,
    }) else {
        return Ok(());
    };
    for effect in &batch.effects {
        if let EffectKind::RequestCancel { did, .. } = &effect.kind
            && cp
                .dispatch_seq
                .get(did)
                .is_some_and(|seq| seq.tid == record.tid && !seq.terminal_seen)
        {
            let millis = i64::try_from(grace.as_millis()).map_err(|_| err("cancel_grace_range"))?;
            let at = record
                .finalized_at
                .0
                .checked_add(millis)
                .ok_or_else(|| err("cancel_grace_range"))?;
            cp.pending_cancel_did = Some(did.clone());
            cp.deadlines.push(Deadline {
                id: DeadlineId(format!("cancel-grace:{}", did.0)),
                kind: DeadlineKind::CancelGrace { did: did.clone() },
                at: TimestampMillis(at),
            });
            break;
        }
    }
    Ok(())
}
