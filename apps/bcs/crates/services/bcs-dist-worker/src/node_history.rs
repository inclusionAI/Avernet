//! Trusted admission boundaries for the node histories of one round.
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    common::{IdempotentWrite, StoreError}, node_history::{NodeHistoryKey, NodeHistoryRepo},
    result::BlobStorePort,
};

pub(crate) async fn capture(
    history: &dyn NodeHistoryRepo,
    blobs: &dyn BlobStorePort,
    spec: &SessionSpec,
    round: &RoundKey,
) -> Result<BlobRef, StoreError> {
    use bcs_service_api::port::repo::distributed::node_history::HistoryManifest;
    if spec.scope != round.scope || spec.group != round.group || spec.sid != round.sid {
        return Err(invalid("node_history_round_binding"));
    }
    let mut cuts = Vec::with_capacity(spec.participants.len());
    // One indexed boundary lookup per frozen participant. The predecessor has
    // already reached Done, so its committed history cannot advance here.
    for participant in &spec.participants {
        cuts.push(history.committed_cut(&NodeHistoryKey {
            scope: round.scope.clone(),
            group: round.group.clone(),
            sid: round.sid.clone(),
            workflow_ref: spec.workflow.clone(),
            node_id: participant.node.clone(),
        }).await?);
    }
    let bytes = serde_json::to_vec(&HistoryManifest { round: round.clone(), cuts })
        .map_err(|_| invalid("node_history_manifest_encoding"))?;
    let digest = bcs_dist_encoding::blob_reference("", &bytes).digest;
    let key = format!("{}{}", HistoryManifest::KEY_PREFIX, bcs_dist_encoding::hex_of(&digest));
    let reference = BlobRef { key, digest };
    match blobs.put_immutable(reference.clone(), bytes).await? {
        IdempotentWrite::Applied | IdempotentWrite::AlreadyApplied => Ok(reference),
        IdempotentWrite::ContentConflict => Err(invalid("node_history_manifest_conflict")),
    }
}

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.into() }
}
