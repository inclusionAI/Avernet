//! Large content: a payload stored by reference is fetched and inlined before
//! it leaves the core, so callers above never deal with storage references.

use crate::error::read_error;
use bcs_dist_model::{OutputBody, OutputEvent, Payload};
use bcs_service_api::core::dist_gateway::DistGatewayCoreError;
use bcs_service_api::port::repo::distributed::result::BlobStorePort;

pub(crate) async fn inline(
    blobs: &dyn BlobStorePort,
    payload: Payload,
) -> Result<Payload, DistGatewayCoreError> {
    let Payload::Ref(reference) = payload else {
        return Ok(payload);
    };
    let unavailable = |code: &str| DistGatewayCoreError::Unavailable {
        code: code.to_owned(),
    };
    let bytes = blobs
        .get(&reference)
        .await
        .map_err(read_error)?
        .ok_or_else(|| unavailable("content_blob_missing"))?;
    serde_json::from_slice(&bytes)
        .map(Payload::Inline)
        .map_err(|_| unavailable("content_blob_not_json"))
}

/// Inlines the content of an artifact event. A terminal event keeps its result
/// reference: the final task view carries the final content.
pub(crate) async fn inline_event(
    blobs: &dyn BlobStorePort,
    mut event: OutputEvent,
) -> Result<OutputEvent, DistGatewayCoreError> {
    if let OutputBody::Artifact { content, .. } = &mut event.body {
        let taken = std::mem::replace(content, Payload::Inline(serde_json::Value::Null));
        *content = inline(blobs, taken).await?;
    }
    Ok(event)
}
