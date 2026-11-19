#![allow(clippy::unwrap_used, reason = "physical profile regression assertions")]
use bcs_dist_model::*;
use bcs_dist_projection::OutputProjectionService;
use bcs_service_api::core::dist_projection::*;
#[test]
fn task_profiles_cannot_shrink_the_shared_physical_envelope_or_reference_frame() {
    for field in [
        "max_outbox_bytes",
        "max_checkpoint_bytes",
        "max_reference_metadata_bytes",
        "checkpoint_finalization_reserve",
        "outbox_finalization_reserve",
    ] {
        let mut value = serde_json::to_value(PHASE_ONE_OUTPUT_LIMITS).unwrap();
        let reduced = match field {
            "max_outbox_bytes" => 65_537,
            "max_checkpoint_bytes" => 900_000,
            "max_reference_metadata_bytes" => 1,
            _ => 70_000,
        };
        value[field] = serde_json::json!(reduced);
        let limits: OutputLimits = serde_json::from_value(value).unwrap();
        assert!(
            OutputProjectionService.validate_limits(&limits).is_err(),
            "{field}"
        );
    }
}
#[test]
fn escaped_artifact_identity_is_charged_by_its_json_encoding() {
    let change = ArtifactChange {
        node: NodeId("\0".repeat(256)),
        artifact_id: ArtifactId("a".into()),
        class: OutputClass::Output,
        update: ArtifactUpdate::Upsert {
            content: Payload::Inline(serde_json::Value::Null),
        },
    };
    assert!(
        OutputProjectionService
            .project_candidate(
                &DisplaySnapshot {
                    format_version: 1,
                    units: vec![]
                },
                OutputUsage::default(),
                &[change],
                &PHASE_ONE_OUTPUT_LIMITS
            )
            .is_err()
    );
}
