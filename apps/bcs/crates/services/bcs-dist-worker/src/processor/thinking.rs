//! Temporary remote output advances raw sequencing, never pure Runner state.
use super::{engine::*, transitions::*};
use crate::runtime::{err, store_error};
use bcs_dist_encoding::{digest_fields, hex_of};
use bcs_dist_model::*;
use bcs_service_api::{
    core::dist_projection::ProjectionError, port::repo::distributed::session::*,
    types::dist_runtime::RuntimeError,
};
use std::num::NonZeroU32;
use tokio::time::Instant;
const TOKEN: u64 = 1_000_000_000;
pub(super) struct ThinkingBudget {
    available: u64,
    at: Instant,
}
impl ThinkingBudget {
    pub fn new(rate: u32) -> Self {
        Self {
            available: u64::from(rate) * TOKEN,
            at: Instant::now(),
        }
    }
    fn take(&mut self, rate: u32) -> bool {
        let now = Instant::now();
        let elapsed = now
            .duration_since(self.at)
            .as_nanos()
            .min(u128::from(u64::MAX)) as u64;
        self.available = self
            .available
            .saturating_add(elapsed.saturating_mul(u64::from(rate)))
            .min(u64::from(rate) * TOKEN);
        self.at = now;
        if self.available < TOKEN {
            return false;
        }
        self.available -= TOKEN;
        true
    }
}
impl WorkerProcessor {
    pub(super) async fn thinking(
        &self,
        slot: &Slot,
        base: &Checkpoint,
        metadata: &TaskSnapshot,
        event: &AgentEvent,
    ) -> Result<Checkpoint, RuntimeError> {
        let mut candidate = next_checkpoint(base, slot.completed.as_ref()).map_err(store_error)?;
        if !self
            .thinking_budget
            .lock()
            .map_err(|_| err("thinking_budget_poisoned"))?
            .take(self.config.thinking_events_per_second)
        {
            return Ok(candidate);
        }
        let dispatch = self
            .io(self.ports.dispatch.get(&event.did))
            .await?
            .ok_or_else(|| err("dispatch_missing"))?;
        if dispatch.view.spec.sid != metadata.sid || dispatch.view.spec.tid != metadata.tid {
            return Err(err("dispatch_task_identity"));
        }
        let content = self
            .references
            .resolve(&event.payload, PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES)
            .await
            .map_err(store_error)?;
        let artifact_id = ArtifactId(format!(
            "thinking-{}",
            hex_of(&digest_fields(&[b"thinking:v1", event.did.0.as_bytes()]))
        ));
        let change = ArtifactChange {
            node: dispatch.view.spec.node,
            artifact_id,
            class: OutputClass::Thinking,
            update: ArtifactUpdate::Upsert {
                content: Payload::Inline(content),
            },
        };
        // The shared projection validates event identity/size. Thinking is
        // deliberately independent of the current full display and its usage.
        match self.ports.projection.project_candidate(
            &DisplaySnapshot {
                format_version: DISPLAY_FORMAT_VERSION,
                units: vec![],
            },
            OutputUsage::default(),
            std::slice::from_ref(&change),
            &metadata.output_limits,
        ) {
            Ok(_) => {}
            Err(ProjectionError::OutputQuotaExceeded { .. }) => return Ok(candidate),
            Err(_) => return Err(err("thinking_projection")),
        }
        let id = EventId {
            commit: candidate.commit_seq,
            index: NonZeroU32::MIN,
        };
        candidate.last_event_id = Some(id);
        install_effects(
            &mut candidate,
            &metadata.sid,
            vec![EffectKind::AppendEvents {
                previous: base.last_event_id,
                events: vec![OutputEvent {
                    tid: metadata.tid.clone(),
                    id,
                    body: OutputBody::Artifact {
                        node: change.node,
                        artifact_id: change.artifact_id,
                        class: change.class,
                        update: change.update,
                    },
                }],
            }],
        )
        .map_err(store_error)?;
        Ok(candidate)
    }
}
