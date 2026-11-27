//! Strict cancel recovery and source fences over the real Inbox provider.
use super::*;

fn cancel(tid: &Tid, input_id: &str) -> AdmitRequest {
    AdmitRequest {
        scope: scope(),
        input: InputEnvelope {
            schema_version: 1,
            input_id: InputId(input_id.into()),
            business_digest: Digest(String::new()),
            sid: Sid(SID.into()),
            tid: tid.clone(),
            body: InputBody::Cancel { reason: None },
        },
        create_session: None,
        create_task: None,
    }
}

#[tokio::test]
async fn strict_cancel_retry_recovers_once_and_generic_admit_cannot_adopt_it() {
    let rig = Rig::new().await;
    let one = rig.gateway();
    let winner = round("msg-a", 1);
    assert!(matches!(one.rounds.admit(&winner).await, Ok(Admission::New { .. })));
    let request = cancel(&winner.input.key.tid, "cancel-a");
    let pause = rig.store.pause_after("document.create", "admission", 1);
    let lost_reply = tokio::spawn({
        let producer = one.producer.clone();
        let request = request.clone();
        async move { producer.admit_strict_cancel(request).await }
    });
    tokio::time::timeout(Duration::from_secs(3), pause.reached()).await.unwrap();
    lost_reply.abort();
    drop(pause);

    let two = rig.gateway();
    let before = physical_writes(&rig.store);
    assert!(matches!(
        two.producer.admit(request.clone()).await,
        Err(StoreError::NotApplied { code }) if code == "strict_admission_entry"
    ));
    assert_eq!(physical_writes(&rig.store), before);
    assert!(matches!(
        two.producer.admit_strict_cancel(request.clone()).await.unwrap(),
        AdmitOutcome::Duplicate(_)
    ));
    assert!(matches!(
        two.producer.admit_strict_cancel(request).await.unwrap(),
        AdmitOutcome::Duplicate(_)
    ));
    assert_eq!(rig.inbox_entries(&two).await, 2, "one Start and one Cancel");
}

#[tokio::test]
async fn a_cancel_whose_append_reply_is_lost_does_not_block_the_next_round() {
    let rig = Rig::new().await;
    let one = rig.gateway();
    let old = round("msg-a", 1);
    assert!(matches!(one.rounds.admit(&old).await, Ok(Admission::New { .. })));
    let request = cancel(&old.input.key.tid, "cancel-stale");
    let pause = rig.store.pause_after("stream.append", ":inbox:", 1);
    let lost_reply = tokio::spawn({
        let producer = one.producer.clone();
        let request = request.clone();
        async move { producer.admit_strict_cancel(request).await }
    });
    tokio::time::timeout(Duration::from_secs(3), pause.reached()).await.unwrap();
    lost_reply.abort();
    drop(pause);
    rig.retire_round(&one).await;
    assert!(matches!(one.rounds.admit(&round("msg-b", 2)).await, Ok(Admission::New { .. })));

    // Repair observes the physical append and then seals it Rejected against
    // the current checkpoint, without requiring the original caller to retry.
    for _ in 0..3 {
        let count = rig.inbox_entries(&one).await;
        if count == 2 {
            assert!(matches!(
                one.producer.admit_strict_cancel(request).await,
                Err(StoreError::NotApplied { code }) if code == STRICT_CANCEL_NOT_ACTIVE
            ));
            return;
        }
        assert_eq!(count, 1, "an unfinished repair exposes only the first Start");
    }
    panic!("the stale Cancel blocked the successor Start");
}

#[tokio::test]
async fn a_queued_cancel_is_skipped_when_the_target_round_retires_before_delivery() {
    let rig = Rig::new().await;
    let one = rig.gateway();
    let old = round("msg-a", 1);
    assert!(matches!(one.rounds.admit(&old).await, Ok(Admission::New { .. })));
    assert!(matches!(
        one.producer.admit_strict_cancel(cancel(&old.input.key.tid, "cancel-delayed")).await,
        Ok(AdmitOutcome::Accepted(_))
    ));
    rig.retire_round(&one).await;
    assert!(matches!(one.rounds.admit(&round("msg-b", 2)).await, Ok(Admission::New { .. })));
    assert_eq!(rig.inbox_entries(&one).await, 2, "only the two Starts are delivered");
}
