use bcs_dist_model::*;
use serde_json::json;

#[test]
fn artifact_events_keep_stable_identity_and_explicit_reference_operation()
-> Result<(), serde_json::Error> {
    let wire = json!({"kind":"artifact", "node":"node-a", "artifact_id":"answer", "class":"output",
        "update":{"op":"append_text","delta":{"kind":"ref","value":{"key":"immutable-delta","digest":"sha256:content"}}}});
    let event: OutputBody = serde_json::from_value(wire.clone())?;
    assert_eq!(serde_json::to_value(event)?, wire);
    let legacy = json!({"kind":"artifact", "node":"node-a", "class":"output", "content":{"kind":"inline","value":"implicit merge"}});
    assert!(serde_json::from_value::<OutputBody>(legacy).is_err());
    let mut missing_identity = wire;
    missing_identity
        .as_object_mut()
        .ok_or_else(|| serde_json::Error::io(std::io::Error::other("fixture")))?
        .remove("artifact_id");
    assert!(serde_json::from_value::<OutputBody>(missing_identity).is_err());
    Ok(())
}

#[test]
fn frozen_limits_do_not_default_missing_fields_from_current_configuration()
-> Result<(), serde_json::Error> {
    let mut wire = serde_json::to_value(PHASE_ONE_OUTPUT_LIMITS)?;
    assert_eq!(
        serde_json::from_value::<OutputLimits>(wire.clone())?,
        PHASE_ONE_OUTPUT_LIMITS
    );
    wire.as_object_mut()
        .ok_or_else(|| serde_json::Error::io(std::io::Error::other("fixture")))?
        .remove("max_total_output_bytes");
    assert!(serde_json::from_value::<OutputLimits>(wire).is_err());
    Ok(())
}

#[test]
fn deadline_occurrence_identity_is_persisted_with_snapshot_flush() -> Result<(), serde_json::Error>
{
    let wire = json!({"published_through":{"commit":3,"index":1},"pending_events":2,"pending_bytes":123,
        "flush_deadline":{"id":"snapshot-task-commit-4","at":5000,"kind":{"kind":"snapshot","tid":"task"}}});
    let progress: SnapshotProgress = serde_json::from_value(wire.clone())?;
    assert_eq!(serde_json::to_value(progress)?, wire);
    let old_timer = json!({"at":5000,"kind":{"kind":"snapshot","tid":"task"}});
    assert!(serde_json::from_value::<Deadline>(old_timer).is_err());
    Ok(())
}
