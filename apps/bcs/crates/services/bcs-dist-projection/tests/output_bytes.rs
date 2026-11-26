#![allow(clippy::unwrap_used, reason = "output content regression assertions")]
use bcs_dist_model::*;
use bcs_dist_projection::OutputProjectionService;
use bcs_service_api::core::dist_projection::OutputProjectionCoreService;
use serde_json::json;

fn empty() -> DisplaySnapshot {
    DisplaySnapshot {
        format_version: DISPLAY_FORMAT_VERSION,
        units: vec![],
    }
}
fn upsert(id: &str, text: &str) -> ArtifactChange {
    ArtifactChange {
        source: CorrelationKey("source".into()),
        artifact_id: ArtifactId(id.into()),
        class: OutputClass::Output,
        update: ArtifactUpdate::Upsert {
            content: Payload::Inline(json!(text)),
        },
    }
}
#[test]
fn output_units_and_complete_snapshots_can_exceed_the_former_byte_quotas() {
    let text = "x".repeat(3 * 1024 * 1024);
    let changes = [upsert("a", &text), upsert("b", &text), upsert("c", &text)];
    let result = OutputProjectionService
        .project_candidate(
            &empty(),
            OutputUsage::default(),
            &changes,
            &PHASE_ONE_OUTPUT_LIMITS,
        )
        .unwrap();
    assert_eq!(result.snapshot.units.len(), 3);
    assert_eq!(result.snapshot.units[2].content, json!(text));
    assert!(serde_json::to_vec(&result.snapshot).unwrap().len() > 8 * 1024 * 1024);
    assert_eq!(
        OutputProjectionService
            .fold_committed(&empty(), &changes, &PHASE_ONE_OUTPUT_LIMITS,)
            .unwrap(),
        result.snapshot
    );
}
#[test]
fn replacement_can_accumulate_more_than_the_former_total_output_quota() {
    let text = "x".repeat(900_000);
    let mut snapshot = empty();
    let mut usage = OutputUsage::default();
    for _ in 0..10 {
        let result = OutputProjectionService
            .project_candidate(
                &snapshot,
                usage,
                &[upsert("answer", &text)],
                &PHASE_ONE_OUTPUT_LIMITS,
            )
            .unwrap();
        snapshot = result.snapshot;
        usage = result.usage;
    }
    assert_eq!(snapshot.units.len(), 1);
    assert_eq!(snapshot.units[0].content, json!(text));
    assert_eq!(usage.cumulative_bytes, 9_000_020);
}

#[test]
fn diagnostic_usage_saturates_without_rejecting_new_output() {
    let result = OutputProjectionService
        .project_candidate(
            &empty(),
            OutputUsage {
                cumulative_bytes: u64::MAX,
            },
            &[upsert("answer", "visible")],
            &PHASE_ONE_OUTPUT_LIMITS,
        )
        .unwrap();
    assert_eq!(result.usage.cumulative_bytes, u64::MAX);
    assert_eq!(result.snapshot.units[0].content, json!("visible"));
}
