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
fn old_format_capabilities_fail_closed_on_isolated_memory_default() -> Result<(), serde_json::Error>
{
    // DownstreamCapabilities gained `isolated_memory` in 0.1.2 with a
    // fail-closed serde default: payloads written by 0.1.1 (and earlier) lack
    // the field and must deserialize as "isolation unverified" (false), never
    // as inferred-true.
    let old = json!({
        "idempotent_start": true,
        "idempotent_input": true,
        "query_by_request": true,
        "query_by_task": true,
        "subscribe": true,
        "resume_events": true,
        "cancel": true,
    });
    let caps: DownstreamCapabilities = serde_json::from_value(old)?;
    assert_eq!(
        caps.isolated_memory, false,
        "old payloads must fail closed: missing evidence is not verified isolation"
    );
    let reloaded: DownstreamCapabilities = serde_json::from_value(serde_json::to_value(&caps)?)?;
    assert_eq!(reloaded, caps);
    let mut verified = caps.clone();
    verified.isolated_memory = true;
    let wire = serde_json::to_value(&verified)?;
    assert_eq!(wire["isolated_memory"], json!(true));
    Ok(())
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

#[test]
fn dispatch_execution_decodes_old_payloads_without_verification_or_quarantine()
-> Result<(), serde_json::Error> {
    // Plan T7 slice 4 additive fields: documents written before the
    // verification handoff and the Quarantined record existed decode with
    // both absent — never fabricated, never defaulted into evidence.
    let old = json!({
        "holder": {"instance": {"node": "conn-1", "incarnation": "1794370950000"}, "epoch": 4},
        "phase": "sending",
        "remote": null,
        "confirmed_input_through": null,
        "cancellation": {"kind": "not_sent"},
        "outcome": null
    });
    let execution: DispatchExecution = serde_json::from_value(old)?;
    assert_eq!(execution.verification, None);
    assert_eq!(execution.quarantined, None);
    Ok(())
}

#[test]
fn verification_handoff_and_quarantine_round_trip_as_wire_shapes()
-> Result<(), serde_json::Error> {
    let handoff = json!({
        "claim": {
            "instance": {"node": "conn-2", "incarnation": "1794370960000"},
            "epoch": 3,
        },
        "attempt": 2,
        "key": {"kind": "by_request", "request_id": "s1.task.d1.n1.a1:start"},
        "method": "query",
        "next_due": 1794371205200i64,
        "backoff_ms": 64000
    });
    let decoded: VerificationHandoff = serde_json::from_value(handoff.clone())?;
    assert_eq!(serde_json::to_value(&decoded)?, handoff);
    let quarantined = json!({
        "at": 1794371505200i64,
        "reason": "stop_verification_deadline"
    });
    let record: QuarantineRecord = serde_json::from_value(quarantined.clone())?;
    assert_eq!(serde_json::to_value(&record)?, quarantined);
    Ok(())
}
