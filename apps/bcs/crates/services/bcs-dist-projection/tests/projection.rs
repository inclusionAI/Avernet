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
        node: NodeId("node".into()),
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
    let mut by_node = upsert("a", json!("other node"));
    by_node.node = NodeId("other".into());
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
fn cumulative_snapshot_and_unit_budgets_are_independent() {
    let mut limits = PHASE_ONE_OUTPUT_LIMITS;
    limits.max_unit_bytes = 8;
    limits.inline_threshold_bytes = 8;
    limits.max_total_output_bytes = 10;
    let first = OutputProjectionService
        .project_candidate(
            &empty(),
            OutputUsage::default(),
            &[upsert("a", json!("1234"))],
            &limits,
        )
        .unwrap();
    assert_eq!(
        OutputProjectionService.project_candidate(
            &first.snapshot,
            first.usage,
            &[upsert("a", json!("1234"))],
            &limits
        ),
        Err(ProjectionError::OutputQuotaExceeded {
            budget: OutputBudget::CumulativeBytes
        })
    );
    assert_eq!(
        OutputProjectionService.project_candidate(
            &empty(),
            OutputUsage::default(),
            &[upsert("a", json!("1234567"))],
            &limits
        ),
        Err(ProjectionError::OutputQuotaExceeded {
            budget: OutputBudget::UnitBytes
        })
    );
    let mut tiny_snapshot = PHASE_ONE_OUTPUT_LIMITS;
    tiny_snapshot.max_snapshot_bytes = serde_json::to_vec(&empty()).unwrap().len() as u32 + 5;
    assert_eq!(
        OutputProjectionService.project_candidate(
            &empty(),
            OutputUsage::default(),
            &[upsert("a", json!(0))],
            &tiny_snapshot
        ),
        Err(ProjectionError::OutputQuotaExceeded {
            budget: OutputBudget::SnapshotBytes
        })
    );
    // Restore still enforces the unit limit rather than using an unlimited mode.
    assert_eq!(
        OutputProjectionService.fold_committed(&empty(), &[upsert("a", json!("1234567"))], &limits),
        Err(ProjectionError::OutputQuotaExceeded {
            budget: OutputBudget::UnitBytes
        })
    );
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
fn encoded_snapshot_limit_includes_escaped_identity_and_order_metadata() {
    let mut change = upsert("artifact/\"/中", json!("\"\\\n"));
    change.node = NodeId("node/尾".into());
    let expected = project(&[change.clone()]);
    let bytes = serde_json::to_vec(&expected.snapshot).unwrap().len() as u32;
    let mut limits = PHASE_ONE_OUTPUT_LIMITS;
    limits.max_snapshot_bytes = bytes;
    assert_eq!(
        OutputProjectionService
            .project_candidate(&empty(), OutputUsage::default(), &[change.clone()], &limits)
            .unwrap(),
        expected
    );
    limits.max_snapshot_bytes -= 1;
    assert_eq!(
        OutputProjectionService.project_candidate(
            &empty(),
            OutputUsage::default(),
            &[change],
            &limits
        ),
        Err(ProjectionError::OutputQuotaExceeded {
            budget: OutputBudget::SnapshotBytes
        })
    );
}

#[test]
fn append_and_replace_size_deltas_match_independent_full_json_encoding() {
    let mut state = project(&[
        upsert("a", json!("原始")),
        upsert("b", json!({"removed":[1,2,3]})),
    ]);
    for change in [
        append("a", "\"\\\n尾"),
        upsert("b", json!([])),
        append("a", "🙂"),
    ] {
        let expected = OutputProjectionService
            .project_candidate(
                &state.snapshot,
                state.usage,
                std::slice::from_ref(&change),
                &PHASE_ONE_OUTPUT_LIMITS,
            )
            .unwrap();
        let mut limits = PHASE_ONE_OUTPUT_LIMITS;
        limits.max_snapshot_bytes = serde_json::to_vec(&expected.snapshot)
            .unwrap()
            .len()
            .max(serde_json::to_vec(&state.snapshot).unwrap().len())
            as u32;
        assert_eq!(
            OutputProjectionService
                .project_candidate(&state.snapshot, state.usage, &[change], &limits)
                .unwrap(),
            expected
        );
        state = expected;
    }
}

#[test]
fn count_identity_and_current_unit_bounds_cannot_be_bypassed_by_small_payloads() {
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
    limits.max_unit_bytes = 8;
    limits.inline_threshold_bytes = 8;
    assert_eq!(
        OutputProjectionService.fold_committed(
            &empty(),
            &[append("a", "1234"), append("a", "567")],
            &limits
        ),
        Err(ProjectionError::OutputQuotaExceeded {
            budget: OutputBudget::UnitBytes
        })
    );
}

#[test]
fn malformed_existing_state_and_invalid_profiles_are_not_quota_failures() {
    let current = project(&[upsert("a", json!("content"))]).snapshot;
    assert_eq!(
        OutputProjectionService.project_candidate(
            &current,
            OutputUsage::default(),
            &[],
            &PHASE_ONE_OUTPUT_LIMITS
        ),
        Err(ProjectionError::InvalidSnapshot {
            code: "usage_below_display"
        })
    );
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
            2 => limits.1.max_total_output_bytes = u64::MAX,
            _ => limits.1.max_snapshot_bytes = 1,
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
    task.output_limits.max_total_output_bytes /= 2;
    assert_eq!(
        OutputProjectionService.validate_task_limits(&task, &PHASE_ONE_OUTPUT_LIMITS),
        Err(ProjectionError::FrozenLimitsChanged)
    );
    task.output_limits = PHASE_ONE_OUTPUT_LIMITS;
    task.output_usage.cumulative_bytes = PHASE_ONE_OUTPUT_LIMITS.max_total_output_bytes + 1;
    assert_eq!(
        OutputProjectionService.validate_task_limits(&task, &PHASE_ONE_OUTPUT_LIMITS),
        Err(ProjectionError::InvalidSnapshot {
            code: "usage_exceeds_frozen_limit"
        })
    );
}

#[test]
fn maximum_legal_units_remain_bounded_and_snapshot_overhead_can_exhaust_first() {
    let text = "x".repeat(PHASE_ONE_OUTPUT_LIMITS.max_unit_bytes as usize - 2);
    let changes = (0..7)
        .map(|i| upsert(&format!("artifact-{i}"), json!(text)))
        .collect::<Vec<_>>();
    let result = project(&changes);
    assert_eq!(
        result.usage.cumulative_bytes,
        u64::from(PHASE_ONE_OUTPUT_LIMITS.max_unit_bytes) * 7
    );
    assert_eq!(result.snapshot.units.len(), 7);
    assert_eq!(
        OutputProjectionService.project_candidate(
            &result.snapshot,
            result.usage,
            &[upsert("eighth", json!(text))],
            &PHASE_ONE_OUTPUT_LIMITS
        ),
        Err(ProjectionError::OutputQuotaExceeded {
            budget: OutputBudget::SnapshotBytes
        })
    );
    let oversized = "x".repeat(PHASE_ONE_OUTPUT_LIMITS.max_unit_bytes as usize - 1);
    assert_eq!(
        OutputProjectionService.project_candidate(
            &empty(),
            OutputUsage::default(),
            &[upsert("too-large", json!(oversized))],
            &PHASE_ONE_OUTPUT_LIMITS
        ),
        Err(ProjectionError::OutputQuotaExceeded {
            budget: OutputBudget::UnitBytes
        })
    );
}
