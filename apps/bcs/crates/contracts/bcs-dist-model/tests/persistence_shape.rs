use bcs_dist_model::*;
use serde_json::json;

#[test]
fn admission_identity_has_no_physical_queue_offset() -> Result<(), serde_json::Error> {
    let wire = json!({"sid":"session-a","tid":"task-a","input_id":"input-a","business_digest":"content-a"});
    let receipt: AdmissionReceipt = serde_json::from_value(wire.clone())?;
    assert_eq!(serde_json::to_value(receipt)?, wire);
    Ok(())
}

#[test]
fn replay_position_preserves_backend_generation_and_scope() -> Result<(), serde_json::Error> {
    let wire = json!({"sid":"session-a","token":{"backend":"backend-a","generation":"log-v3","value":"example"}});
    let position: InboxPos = serde_json::from_value(wire.clone())?;
    assert_eq!(serde_json::to_value(&position)?, wire);
    let different_generation: InboxPos = serde_json::from_value(json!({
        "sid":"session-a","token":{"backend":"backend-a","generation":"log-v4","value":"example"}
    }))?;
    assert_ne!(position, different_generation);
    Ok(())
}

#[test]
fn event_indices_are_one_based_and_not_transport_timestamps() -> Result<(), serde_json::Error> {
    assert!(serde_json::from_value::<EventId>(json!({"commit":7,"index":0})).is_err());
    let event: EventId = serde_json::from_value(json!({"commit":7,"index":1}))?;
    assert_eq!(event.commit, CommitSeq(7));
    assert_eq!(event.index.get(), 1);
    Ok(())
}

#[test]
fn unknown_input_variants_do_not_silently_become_user_messages() {
    let input = json!({"schema_version":1,"sid":"s","tid":"t","input_id":"i",
        "business_digest":"d","body":{"kind":"future_variant","payload":null}});
    assert!(serde_json::from_value::<InputEnvelope>(input).is_err());
}

#[test]
fn accepted_cancellation_is_distinct_from_confirmed_cancellation() -> Result<(), serde_json::Error>
{
    let accepted: CancelObservation = serde_json::from_value(json!({"kind":"request_accepted"}))?;
    let confirmed: CancelObservation =
        serde_json::from_value(json!({"kind":"confirmed_canceled"}))?;
    let unknown: CancelObservation = serde_json::from_value(json!({"kind":"outcome_unknown"}))?;
    assert_ne!(accepted, confirmed);
    assert_ne!(unknown, confirmed);
    assert!(serde_json::from_value::<CancelObservation>(
        json!({"kind":"already_finished","outcome":"unknown"})
    )
    .is_err());
    Ok(())
}
