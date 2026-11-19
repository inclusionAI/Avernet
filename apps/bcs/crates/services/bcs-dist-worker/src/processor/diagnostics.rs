//! Deterministic failures preserve evidence in the independent control header.
use super::engine::*;
use crate::runtime::err;
use bcs_dist_encoding::{digest_fields, hex_of};
use bcs_dist_model::*;
use bcs_service_api::{port::repo::distributed::control::*, types::dist_runtime::RuntimeError};
pub(super) fn requires_block(code: &str) -> bool {
    matches!(
        code,
        "store_invalid"
            | "store_format"
            | "checkpoint_proof_missing"
            | "checkpoint_content_changed"
            | "risk_begin_conflict"
            | "commit_content_conflict"
            | "control_content_conflict"
            | "workflow_invalid_revision"
            | "task_snapshot_missing"
            | "task_snapshot_identity"
            | "frozen_task_limits"
            | "session_capacity_profile"
            | "thinking_projection"
            | "display_format"
            | "dispatch_missing"
            | "dispatch_task_identity"
            | "processor_input_scope"
            | "processor_input_kind"
    ) || code.starts_with("outbox_")
        || code.starts_with("effect_progress_")
        || matches!(
            code,
            "output_append_conflict"
                | "output_publication_conflict"
                | "dispatch_creation_conflict"
                | "archive_content_conflict"
                | "dispatch_control_conflict"
        )
}
impl WorkerProcessor {
    pub(super) async fn block(&self, slot: &mut Slot, code: &str) -> Result<(), RuntimeError> {
        self.load_current_control(slot).await?;
        let control = slot
            .control
            .as_ref()
            .ok_or_else(|| err("control_missing"))?;
        if matches!(control.control.mode, ControlMode::Blocked { .. }) {
            return Ok(());
        }
        let hash = digest_fields(&[
            b"diagnostic:v1",
            control.owner.sid.0.as_bytes(),
            &control.head.0.to_be_bytes(),
            control.checkpoint_digest.0.as_bytes(),
            code.as_bytes(),
        ]);
        let attempt = AttemptIdentity {
            id: RequestId(format!("diagnostic-{}", hex_of(&hash))),
            head: control.head,
            state_digest: control.checkpoint_digest.clone(),
            input_digest: Digest("none".into()),
            context_digest: Digest(format!("code:{code}")),
            frozen_now: self.frozen_now(slot),
        };
        let outcome = AttemptOutcome::Uninterpretable {
            code: code.into(),
            isolation_id: RequestId(format!("blocked-{}", hex_of(&hash))),
        };
        let result = self
            .io(self
                .ports
                .control
                .isolate(Self::guard(control), attempt, outcome))
            .await?;
        self.accept_control(slot, result).await?;
        // Nothing from the rejected candidate can acquire authority after this
        // persisted revision/mode fence. Keep the committed checkpoint/outbox.
        slot.prepared = None;
        slot.evaluation = None;
        slot.risk_outcome = None;
        if slot.round_started && slot.pending.is_none() {
            self.runtime.round_aborted(&slot.acquired.ownership)?;
            slot.round_started = false;
        }
        Ok(())
    }
}
