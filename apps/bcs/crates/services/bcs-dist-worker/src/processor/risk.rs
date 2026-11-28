//! Durable identity before pure evaluation; confirmed failure is never rerun.
use super::engine::*;
use crate::runtime::err;
use bcs_dist_encoding::{digest_fields, digest_json, hex_of};
use bcs_dist_model::*;
use bcs_service_api::{
    core::dist_runner::RunnerInput,
    port::repo::distributed::{control::*, session::Checkpoint},
    types::dist_runtime::RuntimeError,
};

impl WorkerProcessor {
    pub(super) fn frozen_now(&self, slot: &Slot) -> TimestampMillis {
        slot.control
            .as_ref()
            .and_then(|c| c.control.active_attempt.as_ref())
            .filter(|a| {
                a.replay.is_none()
                    && a.identity.head == slot.acquired.envelope.checkpoint.commit_seq
            })
            .map_or_else(|| (self.ports.now)(), |a| a.identity.frozen_now)
    }
    pub(super) async fn begin_evaluation(
        &self,
        slot: &mut Slot,
        base: &Checkpoint,
        inputs: &[RunnerInput],
        spec: &SessionSpec,
        now: TimestampMillis,
        initialize: bool,
    ) -> Result<AttemptIdentity, RuntimeError> {
        let input_digest =
            digest_json(&serde_json::to_value(inputs).map_err(|_| err("attempt_encoding"))?);
        let context_digest = digest_json(
            &serde_json::json!({"spec":spec,"initialize":initialize,"base":base}),
        );
        self.begin_risk(
            slot,
            "runner",
            input_digest,
            context_digest,
            now,
            None,
        )
        .await
    }
    pub(super) async fn begin_risk(
        &self,
        slot: &mut Slot,
        kind: &str,
        input_digest: Digest,
        context_digest: Digest,
        now: TimestampMillis,
        effect: Option<(&EffectId, &Digest)>,
    ) -> Result<AttemptIdentity, RuntimeError> {
        let control = slot
            .control
            .as_ref()
            .ok_or_else(|| err("control_missing"))?;
        let hash = digest_fields(&[
            b"risk-attempt:v1",
            kind.as_bytes(),
            control.owner.sid.0.as_bytes(),
            &control.head.0.to_be_bytes(),
            control.checkpoint_digest.0.as_bytes(),
            input_digest.0.as_bytes(),
            context_digest.0.as_bytes(),
            &now.0.to_be_bytes(),
        ]);
        let mut attempt = AttemptIdentity {
            id: RequestId(format!("{kind}-{}", hex_of(&hash))),
            head: control.head,
            state_digest: control.checkpoint_digest.clone(),
            input_digest,
            context_digest,
            frozen_now: now,
        };
        if let Some((effect, digest)) = effect {
            let binding = ReplayIdentity {
                effect: effect.clone(),
                content_digest: digest.clone(),
            };
            let mut revision = control.control.revision;
            if let Some((pending, prior_binding, original_revision)) = &slot.risk_begin {
                revision = *original_revision;
                if pending.head != control.head || *prior_binding != binding {
                    return Err(err("risk_begin_conflict"));
                }
                attempt = pending.clone();
            } else if let Some(active) = control.control.active_attempt.as_ref().filter(|a| {
                a.outcome.is_none()
                    && a.replay.as_ref() == Some(&binding)
                    && a.identity.head == control.head
                    && a.identity.state_digest == attempt.state_digest
                    && a.identity.input_digest == attempt.input_digest
                    && a.identity.context_digest == attempt.context_digest
            }) {
                attempt = active.identity.clone();
            } else {
                attempt.id = RequestId(format!("effect-{}", uuid::Uuid::new_v4()));
            }
            // Retain before the first await; canceled or unknown begin retries
            // exactly this episode. A restarted process reads the pending one.
            slot.risk_begin = Some((attempt.clone(), binding, revision));
        }
        let outcome = if let Some((effect, digest)) = effect {
            self.io(self.ports.control.begin_replay_attempt(
                Self::guard(control),
                attempt.clone(),
                effect.clone(),
                digest.clone(),
            ))
            .await?
        } else {
            self.io(self
                .ports
                .control
                .begin_attempt(Self::guard(control), attempt.clone()))
                .await?
        };
        self.accept_control(slot, outcome).await?;
        slot.risk_begin = None;
        let mode = &slot
            .control
            .as_ref()
            .ok_or_else(|| err("control_missing"))?
            .control
            .mode;
        let allowed = matches!(mode, ControlMode::Normal)
            || (effect.is_some() && matches!(mode, ControlMode::Quarantined { .. }));
        if !allowed {
            return Err(err("session_control_paused"));
        }
        Ok(attempt)
    }
    pub(super) fn reject_risk(
        &self,
        slot: &mut Slot,
        attempt: AttemptIdentity,
        code: &str,
        safe: bool,
    ) {
        let hash = digest_fields(&[b"isolation:v1", attempt.id.0.as_bytes(), code.as_bytes()]);
        let isolation_id = RequestId(format!("isolate-{}", hex_of(&hash)));
        slot.risk_outcome = Some((
            attempt,
            if safe {
                AttemptOutcome::Deterministic {
                    code: code.into(),
                    isolation_id,
                    safe_to_finalize: true,
                }
            } else {
                AttemptOutcome::Uninterpretable {
                    code: code.into(),
                    isolation_id,
                }
            },
        ));
        slot.evaluation = None;
    }
    pub(super) async fn finish_risk(&self, slot: &mut Slot) -> Result<(), RuntimeError> {
        let Some((attempt, outcome)) = slot.risk_outcome.clone() else {
            return Ok(());
        };
        let control = slot
            .control
            .as_ref()
            .ok_or_else(|| err("control_missing"))?;
        let result = self
            .io(self
                .ports
                .control
                .finish_attempt(Self::guard(control), attempt.id, outcome))
            .await?;
        self.accept_control(slot, result).await?;
        slot.risk_outcome = None;
        Ok(())
    }
}
