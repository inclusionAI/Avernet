//! Pure wire-projection size validation (D0-frozen contract, plan T4/T8).
use super::*;
use bcs_service_api::port::repo::distributed::result::*;
use crate::identity::digest_fields;
use bcs_service_api::port::repo::distributed::common::StoreError;


/// The default pure validator of the D0-frozen public-projection size
/// contract: one canonical bounded encode of the complete default GetTask
/// body plus the mandatory state and artifact SSE frames — outer envelope
/// shell, exact JSON escaping, the public history and every part, each
/// exactly once. Host-injected; the adapter (T8) reuses this contract
/// without pulling in any store or adapter dependency.
pub struct JsonProjectionValidator;

impl PublicProjectionValidator for JsonProjectionValidator {
    fn measure(
        &self,
        budget: &ArchiveBudget,
        archive: &TaskFinalArchive,
    ) -> Result<ProjectionCheck, StoreError> {
        let frames: Vec<serde_json::Value> = archive
            .public_history
            .iter()
            .map(|event| serde_json::json!({ "kind": "state", "event": event }))
            .chain(archive.artifacts.iter().map(|part| {
                serde_json::json!({
                    "kind": "artifact",
                    "artifact_id": part.artifact_id.0,
                    "order": part.order.get(),
                    "part": part.part,
                })
            }))
            .collect();
        let document = serde_json::to_vec(&serde_json::json!({
            "envelope": {
                "version": budget.version,
                "kind": "get_task",
                "scope_tenant": archive.round.scope.tenant.0,
                "scope_principal": archive.round.scope.principal.0,
                "group": archive.round.group.0,
                "tid": archive.round.tid.0,
            },
            "get_task": {
                "record": archive.record,
                "history": archive.public_history,
            },
            "frames": frames,
        }))
        .map_err(|_| invalid("archive_encoding"))?;
        let measured = document.len() as u64;
        Ok(ProjectionCheck {
            budget_version: budget.version,
            projection_digest: digest_fields(&[b"wire:v1", &document]),
            measured_bytes: measured,
            fits: measured <= budget.profile.public_frame_bytes as u64,
        })
    }
}
