use bcs_service_api::port::repo::distributed::session::{Checkpoint, EffectKind, TaskPhase};
use serde_json::json;

#[test]
fn awaiting_result_checkpoint_retains_replayable_result_and_input_coverage(
) -> Result<(), serde_json::Error> {
    let wire = json!({
        "format_version":1,
        "commit_seq":8,
        "consumed":{"sid":"s","token":{"backend":"store-a","generation":"v1","value":"opaque-8"}},
        "tasks":[{"tid":"t","phase":{"kind":"awaiting_result","status":"completed"},
            "input":{"kind":"inline","value":{}},"result_ref":null}],
        "runner_state":{"format_version":1,"value":{"finished":true}},
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
    let checkpoint: Checkpoint = serde_json::from_value(wire.clone())?;
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
    Ok(())
}

#[test]
fn historical_schedule_decisions_are_not_replayable_business_effects() {
    // Reject the old effect variant rather than deleting a new activation on replay.
    let effect = json!({"kind":"set_due","at":null});
    assert!(serde_json::from_value::<EffectKind>(effect).is_err());
}
