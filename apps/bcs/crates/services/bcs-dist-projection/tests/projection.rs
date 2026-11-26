#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]
use bcs_dist_model::*;
use bcs_dist_projection::OutputProjectionService;
use bcs_service_api::core::dist_projection::*;
use serde_json::{Value, json};

fn empty() -> DisplaySnapshot {
    DisplaySnapshot {
        format_version: DISPLAY_FORMAT_VERSION,
        units: vec![],
    }
}
fn upsert(id: &str, value: Value) -> ArtifactChange {
    ArtifactChange {
        source: CorrelationKey("source".into()),
        artifact_id: ArtifactId(id.into()),
        class: OutputClass::Output,
        update: ArtifactUpdate::Upsert {
            content: Payload::Inline(value),
        },
    }
}
fn append(id: &str, delta: &str) -> ArtifactChange {
    let mut change = upsert(id, Value::Null);
    change.update = ArtifactUpdate::AppendText {
        delta: Payload::Inline(json!(delta)),
    };
    change
}
fn project(changes: &[ArtifactChange]) -> OutputProjection {
    OutputProjectionService
        .project_candidate(
            &empty(),
            OutputUsage::default(),
            changes,
            &PHASE_ONE_OUTPUT_LIMITS,
        )
        .unwrap()
}

#[test]
fn stable_identity_and_first_appearance_order_survive_replacement() {
    let state = project(&[
        upsert("a", json!({"old":true})),
        upsert("b", json!(2)),
        upsert("a", Value::Null),
    ]);
    assert_eq!(
        state
            .snapshot
            .units
            .iter()
            .map(|u| u.artifact_id.0.as_str())
            .collect::<Vec<_>>(),
        ["a", "b"]
    );
    assert_eq!(state.snapshot.units[0].content, Value::Null);
    assert_eq!(state.snapshot.units[1].content, json!(2));
    assert_eq!(state.usage.cumulative_bytes, 12 + 1 + 4);
    let mut by_node = upsert("a", json!("other source"));
    by_node.source = CorrelationKey("other".into());
    let mut by_class = upsert("a", json!("tool"));
    by_class.class = OutputClass::Tool;
    let expanded = OutputProjectionService
        .project_candidate(
            &state.snapshot,
            state.usage,
            &[by_node, by_class],
            &PHASE_ONE_OUTPUT_LIMITS,
        )
        .unwrap();
    assert_eq!(expanded.snapshot.units.len(), 4);
}

#[test]
fn append_creates_text_and_a_late_invalid_action_rejects_the_whole_batch() {
    let state = project(&[append("a", "hello"), append("a", " 世界")]);
    assert_eq!(state.snapshot.units[0].content, json!("hello 世界"));
    let before = state.clone();
    assert_eq!(
        OutputProjectionService.project_candidate(
            &state.snapshot,
            state.usage,
            &[
                append("a", "!"),
                upsert("b", json!({"x":1})),
                append("b", "bad")
            ],
            &PHASE_ONE_OUTPUT_LIMITS
        ),
        Err(ProjectionError::InvalidArtifact {
            code: "append_requires_text"
        })
    );
    assert_eq!(state, before);
}

#[test]
fn candidate_charges_exact_json_bytes_without_refund_but_restore_does_not_charge() {
    let first = project(&[upsert("a", json!("\"\n中"))]);
    assert_eq!(
        first.usage.cumulative_bytes,
        serde_json::to_vec(&json!("\"\n中")).unwrap().len() as u64
    );
    let delta = append("a", "尾");
    let next = OutputProjectionService
        .project_candidate(
            &first.snapshot,
            first.usage,
            std::slice::from_ref(&delta),
            &PHASE_ONE_OUTPUT_LIMITS,
        )
        .unwrap();
    assert_eq!(next.charged_bytes, 5);
    assert_eq!(
        next.usage.cumulative_bytes,
        first.usage.cumulative_bytes + 5
    );
    assert_eq!(
        OutputProjectionService
            .fold_committed(&first.snapshot, &[delta], &PHASE_ONE_OUTPUT_LIMITS)
            .unwrap(),
        next.snapshot
    );
    let replaced = OutputProjectionService
        .project_candidate(
            &next.snapshot,
            next.usage,
            &[upsert("a", json!(""))],
            &PHASE_ONE_OUTPUT_LIMITS,
        )
        .unwrap();
    assert_eq!(
        replaced.usage.cumulative_bytes,
        next.usage.cumulative_bytes + 2
    );
}

#[test]
fn thinking_is_ephemeral_and_never_consumes_persistent_quota() {
    let mut thought = upsert("thought", json!("private reasoning"));
    thought.class = OutputClass::Thinking;
    let result = project(&[thought, append("answer", "visible")]);
    assert_eq!(result.snapshot.units.len(), 1);
    assert_eq!(result.snapshot.units[0].artifact_id.0, "answer");
    assert_eq!(result.usage.cumulative_bytes, 9);
    assert_eq!(result.display_events, 1);
}


#[test]
fn unresolved_refs_unknown_formats_and_duplicate_persisted_identity_are_rejected() {
    let mut change = upsert("a", Value::Null);
    change.update = ArtifactUpdate::Upsert {
        content: Payload::Ref(BlobRef {
            key: "immutable".into(),
            digest: Digest("digest".into()),
        }),
    };
    assert_eq!(
        OutputProjectionService.project_candidate(
            &empty(),
            OutputUsage::default(),
            &[change],
            &PHASE_ONE_OUTPUT_LIMITS
        ),
        Err(ProjectionError::UnresolvedReference)
    );
    let unknown = DisplaySnapshot {
        format_version: 99,
        units: vec![],
    };
    assert_eq!(
        OutputProjectionService.fold_committed(&unknown, &[], &PHASE_ONE_OUTPUT_LIMITS),
        Err(ProjectionError::UnsupportedFormat { format: 99 })
    );
    let mut invalid = project(&[upsert("a", Value::Null)]).snapshot;
    invalid.units.push(invalid.units[0].clone());
    assert_eq!(
        OutputProjectionService.fold_committed(&invalid, &[], &PHASE_ONE_OUTPUT_LIMITS),
        Err(ProjectionError::InvalidSnapshot {
            code: "duplicate_artifact_identity"
        })
    );
}



#[test]
fn count_and_identity_bounds_cannot_be_bypassed_by_small_payloads() {
    let mut limits = PHASE_ONE_OUTPUT_LIMITS;
    limits.max_actions = 1;
    assert_eq!(
        OutputProjectionService.fold_committed(
            &empty(),
            &[upsert("a", json!(0)), upsert("b", json!(0))],
            &limits
        ),
        Err(ProjectionError::OutputQuotaExceeded {
            budget: OutputBudget::Actions
        })
    );
    limits.max_actions = 2;
    limits.max_artifacts = 1;
    assert_eq!(
        OutputProjectionService.fold_committed(
            &empty(),
            &[upsert("a", json!(0)), upsert("b", json!(0))],
            &limits
        ),
        Err(ProjectionError::OutputQuotaExceeded {
            budget: OutputBudget::Artifacts
        })
    );
    assert!(
        OutputProjectionService
            .fold_committed(
                &empty(),
                &[upsert("a", json!(0)), upsert("a", json!(1))],
                &limits
            )
            .is_ok()
    );
    let mut large_identity = upsert("a", json!(0));
    large_identity.artifact_id = ArtifactId("x".repeat(257));
    assert_eq!(
        OutputProjectionService.fold_committed(&empty(), &[large_identity], &limits),
        Err(ProjectionError::OutputQuotaExceeded {
            budget: OutputBudget::IdentityBytes
        })
    );

}

#[test]
fn malformed_existing_state_and_invalid_profiles_are_not_quota_failures() {
    let current = project(&[upsert("a", json!("content"))]).snapshot;
    let mut invalid = current.clone();
    invalid.units[0].class = OutputClass::Thinking;
    assert_eq!(
        OutputProjectionService.fold_committed(&invalid, &[], &PHASE_ONE_OUTPUT_LIMITS),
        Err(ProjectionError::InvalidSnapshot {
            code: "thinking_in_snapshot"
        })
    );
    for mut limits in [PHASE_ONE_OUTPUT_LIMITS; 4].into_iter().enumerate() {
        match limits.0 {
            0 => limits.1.max_actions = 0,
            1 => limits.1.outbox_finalization_reserve = 1,
            2 => limits.1.inline_threshold_bytes = u32::MAX,
            _ => limits.1.max_identity_bytes = 0,
        }
        assert!(matches!(
            OutputProjectionService.validate_limits(&limits.1),
            Err(ProjectionError::InvalidLimits { .. })
        ));
    }
}

#[test]
fn task_frozen_limits_are_checked_independently_of_current_host_configuration() {
    let mut task = TaskSnapshot {
        schema_version: 1,
        scope: TaskScope {
            tenant: TenantId("tenant".into()),
            principal: PrincipalId("owner".into()),
        },
        sid: Sid("s".into()),
        tid: Tid("t".into()),
        content: TaskContent {
            status: TaskStatus::Submitted,
            interactions: vec![],
            snapshot: Payload::Inline(json!(empty())),
            result_ref: None,
        },
        output_limits: PHASE_ONE_OUTPUT_LIMITS,
        output_usage: OutputUsage::default(),
        snapshot_event_id: None,
        source_commit: CommitSeq(0),
        created_at: TimestampMillis(0),
        updated_at: TimestampMillis(0),
    };
    assert_eq!(
        OutputProjectionService.validate_task_limits(&task, &PHASE_ONE_OUTPUT_LIMITS),
        Ok(())
    );
    task.output_limits.inline_threshold_bytes /= 2;
    assert_eq!(
        OutputProjectionService.validate_task_limits(&task, &PHASE_ONE_OUTPUT_LIMITS),
        Err(ProjectionError::FrozenLimitsChanged)
    );
    task.output_limits = PHASE_ONE_OUTPUT_LIMITS;
    task.output_usage.cumulative_bytes = u64::MAX;
    assert_eq!(
        OutputProjectionService.validate_task_limits(&task, &PHASE_ONE_OUTPUT_LIMITS),
        Ok(())
    );
}
