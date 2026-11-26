//! The inbox-side strict fences: legacy generic records stranded by strict
//! adoption, duel losers forfeiting staged claims, and acceptance-reply
//! loss recovery.
use super::*;

/// Durable strict slot census of the session schedule (physical state).
async fn strict_slots(rig: &Rig) -> (usize, usize) {
    let key = rig.layout.schedule(&Sid(SID.into())).unwrap();
    let Some(raw) = rig.store.read_document(&key).await.unwrap() else {
        return (0, 0);
    };
    let slots = raw.value["slots"].as_object().cloned().unwrap_or_default();
    let strict = slots
        .values()
        .filter(|slot| (*slot)["strict"] == serde_json::json!(true))
        .count();
    (slots.len(), strict)
}

/// A legacy generic Start envelope (old JSON-RPC wire shape) with a stable
/// input id: repeated retries locate the same admission record.
fn legacy_start(input_id: &str) -> AdmitRequest {
    let mut input = user_input(input_id, "old json-rpc start");
    input.tid = Tid(TID.into());
    generic_start(input)
}

#[tokio::test]
async fn stranded_generic_record_is_fenced_by_strict_adoption() {
    let rig = Rig::new().await;
    let one = rig.gateway();
    // (i) A legacy generic Start strands: its admission record is durable,
    // undecided (no decision), and has not yet registered a schedule slot.
    let pause = rig.store.pause_after("document.create", "admission", 1);
    let stranded = tokio::spawn({
        let producer = one.producer.clone();
        let request = legacy_start("rpc-stranded");
        async move { producer.admit(request).await }
    });
    pause.reached().await;
    stranded.abort();
    drop(pause);
    assert!(
        one.producer
            .lookup(&scope(), &InputId("rpc-stranded".into()))
            .await
            .unwrap()
            .is_some(),
        "the legacy record is durable and undecided"
    );

    // (ii) Strict adoption reaches the session: the busy predicate consults
    // the checkpoint queue, which a stranded generic record never touched
    // (inbox admission never writes checkpoint tasks).
    let winner = round("msg-a", 1);
    assert_eq!(
        one.rounds.admit(&winner).await.unwrap(),
        Admission::New { accepted: winner.clone() }
    );

    // (iii) The old-RPC client retries its InputId: the stranded record is
    // fenced with ZERO physical writes and no executable Start is produced.
    let before = physical_writes(&rig.store);
    assert!(matches!(
        one.producer.admit(legacy_start("rpc-stranded")).await,
        Err(StoreError::NotApplied { ref code }) if code == "strict_claim_source"
    ));
    assert_eq!(
        physical_writes(&rig.store),
        before,
        "a fenced old-RPC retry leaves no physical write"
    );
    // Physical absence: exactly the strict round's single Start is in the
    // session stream, and no undecided generic slot sits in the schedule.
    assert_eq!(rig.inbox_entries(&one).await, 1);
    let (total, strict) = strict_slots(&rig).await;
    assert_eq!((total, strict), (1, 1));
    // The strict round keeps working after the fence.
    assert!(matches!(
        one.rounds.admit(&winner).await.unwrap(),
        Admission::Replay { .. }
    ));
}

#[tokio::test]
async fn duel_loser_forfeits_its_staged_claim() {
    let rig = Rig::new().await;
    let one = rig.gateway();
    let two = rig.gateway();
    let winner = round("msg-a", 1);
    let loser = round("msg-b", 1);

    // Duel 1: hold the loser between its staged writes and its acceptance
    // CAS; the winner commits first; the loser resumes into the conflict.
    let pause = rig
        .store
        .pause_after("before.document.replace", &rig.ckpt_key(), 1);
    let loser_task = tokio::spawn({
        let rounds = two.rounds.clone();
        let loser = loser.clone();
        async move { rounds.admit(&loser).await }
    });
    pause.reached().await;
    assert_eq!(
        one.rounds.admit(&winner).await.unwrap(),
        Admission::New { accepted: winner.clone() }
    );
    pause.resume();
    assert_eq!(loser_task.await.unwrap(), Ok(Admission::Busy));

    // The loser's staged record is durably fenced (rejections never resume
    // into a Start entry), and its schedule slot is freed: only the
    // winner's sticky claim remains.
    let input_id =
        bcs_dist_store::group_admission::strict_round_input_id(&loser).unwrap();
    assert_eq!(
        two.producer
            .admission_decision(&scope(), &input_id)
            .await
            .unwrap(),
        Some(AdmitOutcome::Quarantined)
    );
    let (total, strict) = strict_slots(&rig).await;
    assert_eq!((total, strict), (1, 1), "no resident loser slot");

    // The forfeited round never resurrects at its OLD generation: after the
    // winner's round retires, re-sending the loser's original gen-1 request
    // is an error, writes nothing and executes nothing (nuance (b)).
    rig.retire_round(&one).await;
    let before = physical_writes(&rig.store);
    assert!(matches!(
        two.rounds.admit(&loser).await,
        Err(StoreError::Invalid { ref code }) if code == "stale_generation"
    ));
    assert_eq!(physical_writes(&rig.store), before);
    assert_eq!(rig.inbox_entries(&one).await, 1, "no new executable Start");

    // Same messageId at the NEW generation re-admits instead of wedging at
    // the forfeited record: supersede → stage → license → one new Start.
    let resubmit = round("msg-b", 2);
    assert_eq!(
        two.rounds.admit(&resubmit).await.unwrap(),
        Admission::New { accepted: resubmit.clone() }
    );
    let input_id =
        bcs_dist_store::group_admission::strict_round_input_id(&resubmit).unwrap();
    assert!(matches!(
        two.producer.admission_decision(&scope(), &input_id).await.unwrap(),
        Some(AdmitOutcome::Duplicate(_))
    ));
    assert_eq!(rig.inbox_entries(&one).await, 2, "exactly one new Start");
    let (total, strict) = strict_slots(&rig).await;
    assert_eq!((total, strict), (2, 2));

    // Duel 2 at the next generation: retire so the session is free, duel
    // again, and prove the slot budget only carries winners — a later
    // install still succeeds, never OutcomeUnknown.
    rig.retire_round(&one).await;
    let winner2 = round("msg-d", 3);
    let loser2 = round("msg-c", 3);
    let pause = rig
        .store
        .pause_after("before.document.replace", &rig.ckpt_key(), 1);
    let loser_task = tokio::spawn({
        let rounds = two.rounds.clone();
        let loser2 = loser2.clone();
        async move { rounds.admit(&loser2).await }
    });
    pause.reached().await;
    assert_eq!(
        one.rounds.admit(&winner2).await.unwrap(),
        Admission::New { accepted: winner2.clone() }
    );
    pause.resume();
    assert_eq!(loser_task.await.unwrap(), Ok(Admission::Busy));
    let input_id2 =
        bcs_dist_store::group_admission::strict_round_input_id(&loser2).unwrap();
    assert_eq!(
        two.producer
            .admission_decision(&scope(), &input_id2)
            .await
            .unwrap(),
        Some(AdmitOutcome::Quarantined)
    );
    let (total, strict) = strict_slots(&rig).await;
    assert_eq!(
        (total, strict),
        (3, 3),
        "duels accumulate winners' claims only"
    );
    // The session keeps working: the winner replays by original identity.
    assert!(matches!(
        one.rounds.admit(&winner2).await.unwrap(),
        Admission::Replay { .. }
    ));
}

#[tokio::test]
async fn lost_acceptance_reply_recovers_the_original_round() {
    let rig = Rig::new().await;
    let one = rig.gateway();
    let two = rig.gateway();
    let winner = round("msg-a", 1);
    // The license CAS is durable but its caller dies before the reply: the
    // admission record itself is unknown to the crashed Gateway. A successor
    // recovers the original Tid through the same message key and delivers the
    // single Start; the loser stays busy. One business round survives.
    let pause = rig
        .store
        .pause_after("document.replace", &rig.ckpt_key(), 1);
    let crashed = tokio::spawn({
        let rounds = one.rounds.clone();
        let winner = winner.clone();
        async move { rounds.admit(&winner).await }
    });
    pause.reached().await;
    crashed.abort();
    drop(pause);
    assert!(matches!(
        two.rounds.admit(&winner).await.unwrap(),
        Admission::Replay { ref accepted } if accepted.input.key.tid == winner.input.key.tid
    ));
    assert_eq!(rig.inbox_entries(&one).await, 1);
    let loser = round("msg-b", 1);
    assert_eq!(two.rounds.admit(&loser).await.unwrap(), Admission::Busy);
    // Duplicate Start delivery (a successor re-issuing the same round's
    // aftermath) applies once.
    assert!(matches!(
        two.rounds.admit(&winner).await.unwrap(),
        Admission::Replay { .. }
    ));
    assert_eq!(rig.inbox_entries(&one).await, 1);
}
