use super::{engine::WorkerProcessor, terminal::attempt_stopped};
use crate::runtime::err;
use bcs_dist_encoding::{digest_fields, hex_of};
use bcs_dist_model::*;
use bcs_service_api::{
    port::repo::distributed::{
        common::{IdempotentWrite, StoreError}, group_round::{RoundBudgetProfile, StrictLicense},
        node_history::{HistoryManifest, NodeHistoryEntry, NodeHistoryKey, NodeHistoryKind, NodeHistoryRepo},
        result::BlobStorePort,
    },
    types::dist_runtime::RuntimeError,
};

pub(super) async fn freeze_for_dispatch(
    spec: &DispatchSpec,
    pin: &DispatchHistory,
    repo: &dyn NodeHistoryRepo,
    blobs: &dyn BlobStorePort,
) -> Result<(), StoreError> {
    let invalid = |code: &str| StoreError::Invalid { code: code.into() };
    let reference = &pin.cut_manifest;
    let bytes = blobs.get(reference).await?.ok_or_else(|| invalid("node_history_manifest_missing"))?;
    if bcs_dist_encoding::blob_reference(&reference.key, &bytes) != *reference {
        return Err(invalid("node_history_manifest_digest"));
    }
    let manifest: HistoryManifest = serde_json::from_slice(&bytes)
        .map_err(|_| invalid("node_history_manifest_encoding"))?;
    let cut = manifest.dispatch_cut(spec, pin)?;
    repo.freeze_node_input(&pin.round, &spec.node, cut, &RoundBudgetProfile::v1()).await?;
    Ok(())
}

impl WorkerProcessor {
    /// Publish history only once the stop barrier has sealed the facts for
    /// every attempt. `Done` (including the admission-side terminal repair)
    /// may never authorize a successor above an incomplete history write.
    pub(super) async fn append_round_history(
        &self,
        license: &StrictLicense,
        status: TerminalStatus,
        views: &mut [DispatchView],
    ) -> Result<(), RuntimeError> {
        let Some(cut_manifest) = &license.history_cut else { return Ok(()) };
        let repo = self.node_history.as_ref().ok_or_else(|| err("node_history_unconfigured"))?;
        let round = license.round_key();
        // Per-node attempt order does not depend on DID hashing or takeover.
        views.sort_by(|a, b| (&a.spec.node.0, a.spec.attempt).cmp(&(&b.spec.node.0, b.spec.attempt)));
        for view in views {
            let provenance = view.spec.node_history.as_ref().ok_or_else(|| err("node_history_dispatch_missing"))?;
            if provenance.round != round || &provenance.cut_manifest != cut_manifest
                || view.spec.sid != round.sid || view.spec.tid != round.tid
            {
                return Err(err("node_history_dispatch_binding"));
            }
            // A never-sent canceled dispatch has no interaction to publish.
            let Some(delivery) = view.execution.node_input_delivery else { continue };
            if !attempt_stopped(&view.execution) {
                return Err(err("node_history_dispatch_unsettled"));
            }
            let key = NodeHistoryKey {
                scope: round.scope.clone(), group: round.group.clone(), sid: round.sid.clone(),
                workflow_ref: provenance.workflow.clone(), node_id: view.spec.node.clone(),
            };
            let id = InteractionId(format!("node-input:{}", hex_of(&digest_fields(&[
                b"fsm-node-input:v1", view.spec.did.0.as_bytes(),
            ]))));
            let input = NodeHistoryEntry {
                round_generation: round.generation,
                kind: NodeHistoryKind::NodeInput { delivery },
                // The actual dispatch body, excluding the separately pinned
                // memory part, so old history is never nested recursively.
                content: view.spec.request.clone(),
                round_outcome: Some(status),
            };
            check_write(self.io(repo.append_once(&key, &id, &input)).await?)?;
            for response in &view.execution.node_responses {
                let record = NodeHistoryEntry {
                    round_generation: round.generation,
                    kind: NodeHistoryKind::NodeResponse,
                    content: Payload::Ref(response.content.clone()),
                    round_outcome: Some(status),
                };
                check_write(self.io(repo.append_once(&key, &response.id, &record)).await?)?;
            }
        }
        Ok(())
    }
}

fn check_write(outcome: IdempotentWrite) -> Result<(), RuntimeError> {
    if outcome == IdempotentWrite::ContentConflict { return Err(err("node_history_interaction_conflict")) }
    Ok(())
}
