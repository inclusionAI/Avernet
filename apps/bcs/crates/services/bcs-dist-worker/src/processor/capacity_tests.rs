#![allow(clippy::unwrap_used, reason = "assert encoded capacity proof")]
use super::{capacity::*, commit::seal_candidate, effects::CompletedEffects, lifecycle::finalize_independently};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::session::*;
#[test]
fn independent_field_maxima_fit_ordinary_and_reserved_finalization_frames() {
    // This intentionally combines independent extrema, including 34 tasks and
    // mutually impossible full dispatch sets, to upper-bound legal candidates.
    assert_eq!(serde_json::to_vec(&identity(1)).unwrap().len(), 256);
    assert_eq!(serde_json::to_vec(&payload()["value"]).unwrap().len(), 1024);
    let ordinary = seal_candidate(
        owner(),
        CommitSeq(u64::MAX - 1),
        CommitId(identity(999)),
        candidate().unwrap(),
        false,
        64 * 1024,
    )
    .unwrap();
    assert!(
        serde_json::to_vec(&ordinary.candidate.outbox)
            .unwrap()
            .len()
            <= 192 * 1024
    );
    assert!(serde_json::to_vec(&ordinary.candidate).unwrap().len() <= 896 * 1024);
    println!("maximum ordinary checkpoint={} outbox={}",
        serde_json::to_vec(&ordinary.candidate).unwrap().len(),
        serde_json::to_vec(&ordinary.candidate.outbox).unwrap().len());
    let mut before = ordinary.candidate;
    before.commit_seq = CommitSeq(u64::MAX - 1);
    let batch = before.outbox.as_ref().unwrap();
    let done = CompletedEffects {
        commit_id: batch.commit_id.clone(),
        sequence: batch.source_commit,
        digest: batch.content_digest.clone(),
        saved: Default::default(),
    };
    let finalizing = finalize_independently(
        &before,
        &snapshot().unwrap(),
        TerminalStatus::Failed,
        "OutputQuotaExceeded",
        TimestampMillis(i64::MAX),
        Some(&done),
    )
    .unwrap();
    let finalizing = seal_candidate(
        owner(),
        before.commit_seq,
        CommitId(identity(999)),
        finalizing,
        true,
        64 * 1024,
    )
    .unwrap();
    assert!(
        serde_json::to_vec(&finalizing.candidate.outbox)
            .unwrap()
            .len()
            <= 64 * 1024
    );
    assert!(serde_json::to_vec(&finalizing.candidate).unwrap().len() <= 960 * 1024);
    println!("maximum finalizing checkpoint={} outbox={}",
        serde_json::to_vec(&finalizing.candidate).unwrap().len(),
        serde_json::to_vec(&finalizing.candidate.outbox).unwrap().len());
}

#[test]
fn startup_checks_all_effect_shapes_and_both_envelope_endpoints() {
    for bytes in [64 * 1024, 128 * 1024] {
        validate_shapes(bytes).unwrap();
        validate(Some(SessionCheckpointCapacity { max_document_bytes: 1024 * 1024,
            max_envelope_bytes: bytes }), bytes).unwrap();
    }
    assert!(validate(None, 64 * 1024).is_err());
    assert!(validate(Some(SessionCheckpointCapacity { max_document_bytes: 960 * 1024,
        max_envelope_bytes: 64 * 1024 }), 64 * 1024).is_err());
    assert!(validate(Some(SessionCheckpointCapacity { max_document_bytes: 1024 * 1024,
        max_envelope_bytes: 128 * 1024 }), 64 * 1024).is_err());
}
