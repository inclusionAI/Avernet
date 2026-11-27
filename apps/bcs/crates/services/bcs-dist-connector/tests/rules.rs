//! The pure rules: identities, resume steps, SENT policy, end of call.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

mod common;

use bcs_dist_connector::identity::{self, KeyEvent};
use bcs_dist_connector::rules::DistConnectorRules;
use bcs_dist_model::*;
use bcs_service_api::core::dist_connector::*;
use common::*;

/// Any moment before the fixture spec's deadline.
const NOW: i64 = 1_794_371_205_200;

fn cursor(epoch: u64) -> ReportCursor {
    ReportCursor {
        holder_epoch: HolderEpoch(epoch),
        last_seq: InputSeq(0),
    }
}

fn key(report: Report) -> (InputEnvelope, AgentEvent) {
    match report {
        Report::Key(request) => {
            // Connector reports reference an existing task and cannot create one.
            let InputBody::Agent { event } = request.input.body.clone() else {
                panic!("not an agent input");
            };
            (request.input, event)
        }
        Report::Stream(_) => panic!("expected a key event"),
    }
}

#[test]
fn local_hash_matches_the_shared_store_hash() {
    // Two copies of one function (the engine may not depend on a store crate):
    // these vectors keep them identical.
    let cases: [&[&[u8]]; 4] = [
        &[],
        &[b""],
        &[b"ab", b"c"],
        &[b"s0042.7f3a9c.b21e.n2.a1", b"final", &7u64.to_be_bytes()],
    ];
    for fields in cases {
        assert_eq!(
            identity::digest_fields(fields),
            bcs_dist_store::identity::digest_fields(fields)
        );
    }
    assert_ne!(
        identity::digest_fields(&[b"ab", b"c"]),
        identity::digest_fields(&[b"a", b"bc"])
    );
    assert_eq!(
        identity::digest_fields(&[b"abc"]).0,
        format!("sha256:{}", known_abc())
    );
}

/// sha256(len("abc") as 8 BE bytes ++ "abc"), computed independently.
fn known_abc() -> String {
    use sha2::{Digest, Sha256};
    let mut bytes = 3u64.to_be_bytes().to_vec();
    bytes.extend_from_slice(b"abc");
    hex::encode(Sha256::digest(bytes))
}

#[test]
fn request_ids_are_stable_and_distinct() {
    let rules = DistConnectorRules;
    let did = Did(DID.into());
    assert_eq!(rules.start_request_id(&did).0, format!("{DID}:start"));
    assert_eq!(rules.input_request_id(&did, InputSeq(2)).0, format!("{DID}:in:2"));
    assert_ne!(
        rules.input_request_id(&did, InputSeq(1)),
        rules.input_request_id(&did, InputSeq(2))
    );
}

#[test]
fn key_identity_ignores_holder_epoch_event_id_and_payload() {
    let rules = DistConnectorRules;
    let view = in_phase(DispatchPhase::Accepted);
    let (a, event_a) = key(rules.report(
        &view,
        &mut cursor(1),
        AgentEventKind::Final,
        Some("evt-1".into()),
        Payload::Inline(serde_json::json!({"state": "completed"})),
    ));
    let (b, event_b) = key(rules.report(
        &view,
        &mut cursor(4),
        AgentEventKind::Final,
        None,
        Payload::Inline(serde_json::json!({"state": "completed", "artifacts": [1]})),
    ));
    assert_eq!(a.input_id, b.input_id);
    assert_eq!(a.business_digest, b.business_digest);
    assert!(a.input_id.0.starts_with("ae:"));
    assert_eq!((a.sid.clone(), a.tid.clone()), (view.spec.sid.clone(), view.spec.tid.clone()));
    assert_eq!(a.schema_version, 1);
    // The event itself still carries who reported it.
    assert_eq!(event_a.holder_epoch, HolderEpoch(1));
    assert_eq!(event_b.holder_epoch, HolderEpoch(4));
    assert_eq!(event_a.remote_event_id.as_deref(), Some("evt-1"));
}

#[test]
fn final_and_error_have_different_identities() {
    let rules = DistConnectorRules;
    let view = in_phase(DispatchPhase::Accepted);
    let (fin, _) = key(rules.report(&view, &mut cursor(1), AgentEventKind::Final, None, state("completed")));
    let (err, _) = key(rules.report(&view, &mut cursor(1), AgentEventKind::Error, None, state("failed")));
    let (canceled, _) = key(rules.report(&view, &mut cursor(1), AgentEventKind::Error, None, state("canceled")));
    assert_ne!(fin.input_id, err.input_id);
    assert_eq!(err.input_id, canceled.input_id);
    // The terminal state is part of the digest, the payload is not.
    assert_ne!(err.business_digest, canceled.business_digest);
}

#[test]
fn question_round_follows_the_confirmed_watermark() {
    let rules = DistConnectorRules;
    let mut view = in_phase(DispatchPhase::Accepted);
    let placeholder = || AgentEventKind::InputRequired {
        interaction: InteractionId("from-adapter".into()),
    };
    let (first, event) = key(rules.report(&view, &mut cursor(1), placeholder(), None, state("input_required")));
    let (again, _) = key(rules.report(&view, &mut cursor(2), placeholder(), None, state("input_required")));
    assert_eq!(first.input_id, again.input_id, "same round, any holder");
    let AgentEventKind::InputRequired { interaction } = event.kind else {
        panic!("kind changed");
    };
    assert!(interaction.0.starts_with("ia:"));
    assert_eq!(interaction.0[3..], first.input_id.0[3..]);
    assert_eq!(first.input_id, identity::key_input_id(&view.spec.did, KeyEvent::InputRequired(1)));

    view.execution.confirmed_input_through = Some(InputSeq(1));
    let (second, _) = key(rules.report(&view, &mut cursor(2), placeholder(), None, state("input_required")));
    assert_ne!(first.input_id, second.input_id, "a confirmed reply opens round 2");
    assert_eq!(second.input_id, identity::key_input_id(&view.spec.did, KeyEvent::InputRequired(2)));

    let auth = AgentEventKind::AuthRequired {
        interaction: InteractionId("x".into()),
    };
    let (auth, _) = key(rules.report(&view, &mut cursor(2), auth, None, state("auth_required")));
    assert_ne!(auth.input_id, second.input_id);
}

#[test]
fn seq_counts_every_reported_event_from_one() {
    let rules = DistConnectorRules;
    let view = in_phase(DispatchPhase::Accepted);
    let mut cursor = cursor(3);
    for (n, kind) in [
        AgentEventKind::Delta,
        AgentEventKind::Thinking,
        AgentEventKind::Tool,
        AgentEventKind::Snapshot,
    ]
    .into_iter()
    .enumerate()
    {
        match rules.report(&view, &mut cursor, kind.clone(), None, state("working")) {
            Report::Stream(append) => {
                assert_eq!(append.event.seq, InputSeq(n as u64 + 1));
                assert_eq!(append.event.holder_epoch, HolderEpoch(3));
                assert_eq!(append.event.kind, kind);
                assert_eq!(append.event.did, view.spec.did);
                assert_eq!((append.sid, append.tid), (view.spec.sid.clone(), view.spec.tid.clone()));
            }
            Report::Key(_) => panic!("intermediate event admitted"),
        }
    }
    let (_, event) = key(rules.report(&view, &mut cursor, AgentEventKind::Final, None, state("completed")));
    assert_eq!(event.seq, InputSeq(5), "key and intermediate events share the counter");
    assert_eq!(cursor.last_seq, InputSeq(5));
}

#[test]
fn resume_step_per_phase() {
    let rules = DistConnectorRules;
    assert_eq!(rules.resume_step(&in_phase(DispatchPhase::Created)), ResumeStep::Send);
    assert_eq!(rules.resume_step(&in_phase(DispatchPhase::Claimed)), ResumeStep::Send);
    assert_eq!(rules.resume_step(&in_phase(DispatchPhase::Sending)), ResumeStep::ResolveStart);
    assert_eq!(rules.resume_step(&in_phase(DispatchPhase::Accepted)), ResumeStep::Reattach);
    assert_eq!(rules.resume_step(&in_phase(DispatchPhase::Ended)), ResumeStep::Nothing);
    let mut canceled = in_phase(DispatchPhase::Claimed);
    canceled.control.cancel = Some(cancel_intent());
    assert_eq!(rules.resume_step(&canceled), ResumeStep::EndCanceledUnsent);
    // A cancel does not make a possibly sent call "unsent".
    let mut sending = in_phase(DispatchPhase::Sending);
    sending.control.cancel = Some(cancel_intent());
    assert_eq!(rules.resume_step(&sending), ResumeStep::ResolveStart);
}

#[test]
fn sent_policy_follows_declared_capabilities() {
    let rules = DistConnectorRules;
    let view = in_phase(DispatchPhase::Sending);
    let request_id = RequestId(format!("{}:start", view.spec.did.0));
    let body = match &view.spec.request {
        Payload::Inline(value) => serde_json::to_vec(value).unwrap(),
        Payload::Ref(reference) => reference.key.as_bytes().to_vec(),
    };
    let mut current = rules.send_proof(
        &view.spec,
        ProofOperation::Start { request_id: request_id.clone() },
        &body,
        Digest("sha256:fixture-config".into()),
    );
    assert!(current.dedupe_domain.is_empty(), "intent alone is not a dedupe guarantee");
    assert!(current.guarantee_deadline <= TimestampMillis(NOW));
    // Inject trusted evidence to exercise the pure predicate; the current
    // actor has no such evidence source and never uses this resend path.
    current.dedupe_domain = "verified-test-domain".into();
    current.guarantee_deadline = TimestampMillis(NOW + 1_000);
    let recorded = current.clone();
    // A recheck that upholds the proof: in window, same domain, same
    // semantics (plan T6).
    let upheld = DedupeRecheck {
        recorded: Some(&recorded),
        current: &current,
        now: Some(TimestampMillis(NOW)),
        resolved_domain: Some(recorded.dedupe_domain.as_str()),
    };

    let none = DownstreamCapabilities {
        idempotent_start: false,
        idempotent_input: false,
        query_by_request: false,
        query_by_task: false,
        subscribe: false,
        resume_events: false,
        cancel: false,
        isolated_memory: false,
        persistent_dedupe: false,
    };
    // No query and no dedupe: the call stays unresolved, whatever the proof.
    assert_eq!(rules.uncertain_start(&none, &upheld), UncertainSend::Unresolved);
    assert_eq!(rules.uncertain_input(&none, &upheld), UncertainSend::Unresolved);
    assert_eq!(rules.reattach(&none), Reattach::Unresolved);

    let ask = DownstreamCapabilities { query_by_request: true, ..none.clone() };
    assert_eq!(rules.uncertain_start(&ask, &upheld), UncertainSend::AskAgent);
    assert_eq!(rules.uncertain_input(&ask, &upheld), UncertainSend::Unresolved);

    // Declared idempotency alone never resends (plan T6).
    let idem = DownstreamCapabilities {
        idempotent_start: true,
        idempotent_input: true,
        query_by_request: true,
        ..none.clone()
    };
    assert_eq!(rules.uncertain_start(&idem, &upheld), UncertainSend::AskAgent, "no durable dedupe: the request can only be asked for");

    // The negotiated persistent dedupe, inside the recorded proof, resends.
    let durable = DownstreamCapabilities {
        persistent_dedupe: true,
        ..idem.clone()
    };
    assert_eq!(rules.uncertain_start(&durable, &upheld), UncertainSend::Resend, "a durable dedupe inside its window wins");
    assert_eq!(rules.uncertain_input(&durable, &upheld), UncertainSend::Resend);
    // An input idempotent only in name — no negotiated durable dedupe and
    // no query to ask — stays unresolved.
    let quiet = DownstreamCapabilities {
        idempotent_input: true,
        persistent_dedupe: false,
        ..durable.clone()
    };
    assert_eq!(rules.uncertain_input(&quiet, &upheld), UncertainSend::Unresolved);

    let poll = DownstreamCapabilities { query_by_task: true, ..none.clone() };
    assert_eq!(rules.reattach(&poll), Reattach::Poll);
    let sub = DownstreamCapabilities { subscribe: true, ..none.clone() };
    assert_eq!(rules.reattach(&sub), Reattach::Subscribe { query_first: false });
    let both = DownstreamCapabilities { subscribe: true, query_by_task: true, ..none };
    assert_eq!(rules.reattach(&both), Reattach::Subscribe { query_first: true });
}

#[test]
fn end_of_maps_terminal_events_only() {
    let rules = DistConnectorRules;
    assert_eq!(rules.end_of(&AgentEventKind::Final, &state("completed")), Some(FinalDispatchOutcome::Completed));
    assert_eq!(rules.end_of(&AgentEventKind::Error, &state("failed")), Some(FinalDispatchOutcome::Failed));
    assert_eq!(rules.end_of(&AgentEventKind::Error, &state("rejected")), Some(FinalDispatchOutcome::Failed));
    assert_eq!(rules.end_of(&AgentEventKind::Error, &state("canceled")), Some(FinalDispatchOutcome::Canceled));
    let by_ref = Payload::Ref(BlobRef { key: "k".into(), digest: Digest("d".into()) });
    assert_eq!(rules.end_of(&AgentEventKind::Error, &by_ref), Some(FinalDispatchOutcome::Failed));
    assert_eq!(rules.end_of(&AgentEventKind::Delta, &state("completed")), None);
    let waiting = AgentEventKind::InputRequired { interaction: InteractionId("i".into()) };
    assert_eq!(rules.end_of(&waiting, &state("input_required")), None);
}

#[test]
fn next_input_is_the_one_right_above_the_watermark() {
    let rules = DistConnectorRules;
    let mut view = in_phase(DispatchPhase::Accepted);
    assert!(rules.next_input(&view).is_none());
    view.control.pending_input = vec![pending(2), pending(1)];
    assert_eq!(rules.next_input(&view).map(|p| p.seq), Some(InputSeq(1)));
    view.execution.confirmed_input_through = Some(InputSeq(1));
    assert_eq!(rules.next_input(&view).map(|p| p.seq), Some(InputSeq(2)), "delivered entries are skipped");
    view.execution.confirmed_input_through = Some(InputSeq(2));
    assert!(rules.next_input(&view).is_none());
    // A gap is never skipped.
    view.execution.confirmed_input_through = None;
    view.control.pending_input = vec![pending(2)];
    assert!(rules.next_input(&view).is_none());
}
