use bcs_service_api::port::repo::distributed::session::{Checkpoint, EffectKind, TaskPhase, TaskRunnerState};
use bcs_dist_model::{PHASE_ONE_OUTPUT_LIMITS, OutputUsage, SnapshotProgress};
use serde_json::json;

#[test]
fn awaiting_result_checkpoint_retains_replayable_result_and_input_coverage(
) -> Result<(), serde_json::Error> {
    let mut wire = json!({
        "format_version":1,
        "downstream_generation":3, "pending_cancel_did":null,
        "commit_seq":8,
        "consumed":{"sid":"s","token":{"backend":"store-a","generation":"v1","value":"opaque-8"}},
        "tasks":[{"tid":"t","phase":{"kind":"awaiting_result","status":"completed"},
            "input":{"kind":"inline","value":{}},"result_ref":null,"steps":0,
            "output_limits":PHASE_ONE_OUTPUT_LIMITS,"output_usage":OutputUsage::default(),
            "publication":SnapshotProgress::default(),"finalization":null}],
        "runner_state":null,
        "dispatch_seq":{},"deadlines":[],"view_drafts":{},
        "last_event_id":{"commit":7,"index":1},
        "outbox":{"commit_id":"commit-8","source_commit":8,"content_digest":"batch-digest",
            "effects":[{"id":{"sid":"s","commit":8,"index":1},"content_digest":"result-digest",
                "kind":{"kind":"save_result","record":{"schema_version":1,
                    "scope":{"tenant":"tenant","principal":"caller"},"sid":"s","tid":"t",
                    "status":"completed","snapshot":{"kind":"inline","value":{"answer":"ready"}},
                    "result":{"kind":"inline","value":{"answer":"ready"}},
                    "source_commit":8,"finalized_at":1234}}}]}
    });
    wire["tasks"][0]["finalization"] = json!({
        "operation_id":"finish-t", "record":wire["outbox"]["effects"][0]["kind"]["record"],
        "terminal_event_id":null, "terminal_published":false
    });
    let checkpoint: Checkpoint = serde_json::from_value(wire.clone())?;
    assert_eq!(checkpoint.format_version, Checkpoint::FORMAT_VERSION);
    assert!(matches!(
        checkpoint.tasks.front().map(|task| &task.phase),
        Some(TaskPhase::AwaitingResult { .. })
    ));
    let batch = checkpoint
        .outbox
        .as_ref()
        .ok_or_else(|| std::io::Error::other("missing outbox"))
        .map_err(serde_json::Error::io)?;
    assert!(matches!(
        batch.effects[0].kind,
        EffectKind::SaveResult { .. }
    ));
    assert_eq!(serde_json::to_value(checkpoint)?, wire);
    wire["tasks"][0].as_object_mut().unwrap().remove("steps");
    let legacy: Checkpoint = serde_json::from_value(wire)?;
    assert_eq!(legacy.tasks.front().unwrap().steps, 0);
    Ok(())
}

#[test]
fn historical_schedule_decisions_are_not_replayable_business_effects() {
    // Reject the old effect variant rather than deleting a new activation on replay.
    let effect = json!({"kind":"set_due","at":null});
    assert!(serde_json::from_value::<EffectKind>(effect).is_err());
}

#[test]
fn runner_state_is_task_bound_and_can_be_discarded_before_next_task_init() -> Result<(), serde_json::Error> {
    let wire = json!({"tid":"task-a","state":{"format_version":1,"value":{"step":3}}});
    let state: Option<TaskRunnerState> = serde_json::from_value(wire.clone())?;
    assert_eq!(serde_json::to_value(&state)?, wire);
    assert!(serde_json::from_value::<Option<TaskRunnerState>>(json!({"format_version":1,"value":{}})).is_err());
    assert_eq!(serde_json::from_value::<Option<TaskRunnerState>>(json!(null))?, None);
    Ok(())
}

#[test]
fn output_projection_contract_is_object_safe() {
    fn accept(_: &dyn bcs_service_api::core::dist_projection::OutputProjectionCoreService) {}
    let _ = accept;
}
