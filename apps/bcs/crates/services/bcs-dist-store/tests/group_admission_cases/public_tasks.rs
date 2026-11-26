//! Cross-connection public-task vectors of the strict-group admission:
//! duel classification, pre-write invisibility and recovery.
use super::*;

#[tokio::test]
async fn two_gateways_one_round() {
    let rig = Rig::new().await;
    let one = rig.gateway();
    let two = rig.gateway();
    let winner = round("msg-a", 1);
    let loser = round("msg-b", 1);

    // Hold the winner exactly at its acceptance CAS: the license write is
    // durable, the reply is not yet delivered. A concurrent Gateway must
    // classify by the durable authority, never by pending intents.
    let pause = rig
        .store
        .pause_after("document.replace", &rig.ckpt_key(), 1);
    let first = tokio::spawn({
        let rounds = one.rounds.clone();
        let winner = winner.clone();
        async move { rounds.admit(&winner).await.unwrap() }
    });
    pause.reached().await;
    // While the winner is unacknowledged, the loser sees the license: Busy,
    // no task, no queue — and the same message retry finds the original.
    assert_eq!(two.rounds.admit(&loser).await.unwrap(), Admission::Busy);
    let identity = GroupRequestIdentity::strict(
        &winner.input.key.scope,
        &winner.input.key.group,
        &winner.request_id,
    );
    assert!(matches!(
        two.rounds.lookup(&identity).await.unwrap(),
        GroupRequestRecord::Accepted(_)
    ));
    pause.resume();
    let admitted = first.await.unwrap();
    assert_eq!(admitted, Admission::New { accepted: winner.clone() });

    // Winner retry through the other connection: the original Tid.
    assert!(matches!(
        two.rounds.admit(&winner).await.unwrap(),
        Admission::Replay { ref accepted } if accepted.input.key.tid == winner.input.key.tid
    ));

    // Exactly one accepted round; the loser never runs. The count covers the
    // whole admission log above: one New winner, one Busy loser, one Start.
    let mut new_count = 0;
    let mut busy_count = 0;
    for outcome in [
        &admitted,
        &two.rounds.admit(&loser).await.unwrap(),
        &two.rounds.admit(&winner).await.unwrap(),
    ] {
        if matches!(outcome, Admission::New { .. }) {
            new_count += 1;
        }
        if matches!(outcome, Admission::Busy) {
            busy_count += 1;
        }
    }
    // One executable Start reaches the single round's inbox.
    let executable_starts = rig.inbox_entries(&one).await;
    assert_eq!((new_count, busy_count, executable_starts), (1, 1, 1));

    // The busy Session takes no old-RPC write: zero physical writes.
    let writes_before = physical_writes(&rig.store);
    assert!(matches!(
        one.producer.admit(rpc_start()).await,
        Err(StoreError::NotApplied { ref code }) if code == "strict_claim_source"
    ));
    assert_eq!(physical_writes(&rig.store), writes_before);

    // After the previous round retires, the loser admits by its own message
    // id at the next generation; a wrong generation never installs.
    rig.retire_round(&one).await;
    // The free session answers a wrong generation as an error, never installs
    // it silently; the next request admits at the exact expected generation.
    assert!(matches!(
        two.rounds.admit(&round("msg-c", 99)).await,
        Err(StoreError::Invalid { ref code }) if code == "stale_generation"
    ));
    let loser_next = round("msg-b", 2);
    assert_eq!(
        two.rounds.admit(&loser_next).await.unwrap(),
        Admission::New { accepted: loser_next.clone() }
    );
    // The retired round's request is still the first identity of its message.
    assert!(matches!(
        one.rounds.admit(&round("msg-a", 1)).await.unwrap(),
        Admission::Replay { ref accepted } if accepted.input.key.tid == winner.input.key.tid
    ));

    // Cross-group, cross-principal and unknown Sid never create sessions.
    let before = physical_writes(&rig.store);
    let mut other_group = round("cross-a", 1);
    other_group.input.key.group = GroupId("grp-other".into());
    assert!(matches!(
        one.rounds.admit(&other_group).await,
        Err(StoreError::Invalid { ref code }) if code == "group_identity_mismatch"
    ));
    let mut other_principal = round("cross-b", 1);
    other_principal.input.key.scope.principal = PrincipalId("user-002".into());
    assert!(matches!(
        one.rounds.admit(&other_principal).await,
        Err(StoreError::Invalid { ref code }) if code == "group_scope_mismatch"
    ));
    let mut unknown = round("cross-c", 1);
    unknown.input.key.sid = Sid("s0099.unknown.1".into());
    assert!(matches!(
        one.rounds.admit(&unknown).await,
        Err(StoreError::NotApplied { ref code }) if code == "session_not_found"
    ));
    assert_eq!(physical_writes(&rig.store), before);
}

#[tokio::test]
async fn prepared_snapshot_is_not_a_public_task() {
    let rig = Rig::new().await;
    let one = rig.gateway();
    let winner = round("msg-a", 1);
    let guessed = winner.input.key.tid.clone();
    let nonexistent = Tid(format!("{SID}.never-admitted"));
    let cursor = |tid: &Tid| TaskCursor {
        sid: Sid(SID.into()),
        tid: tid.clone(),
        token: ReplayToken {
            backend: BackendId("tbase".into()),
            generation: LogGeneration("1".into()),
            value: "0-0".into(),
        },
    };

    // Stop the admitting Gateway between its staged snapshot/index writes
    // and the permission grant: no license yet.
    let pause = rig
        .store
        .pause_after("before.document.replace", &rig.ckpt_key(), 1);
    let pending = tokio::spawn({
        let rounds = one.rounds.clone();
        let winner = winner.clone();
        async move { rounds.admit(&winner).await }
    });
    pause.reached().await;

    // The snapshot and list index physically exist (pre-writes are durable
    // repairs); Get/subscribe/cancel of the guessed Tid behave exactly like a
    // resource that never existed, and List/total exclude it.
    assert!(rig
        .store
        .read_document(&rig.layout.task(&guessed).unwrap())
        .await
        .unwrap()
        .is_some());
    let listed: Vec<String> = ZSetStorePlugin::range(
        rig.store.as_ref(),
        &rig.layout.tasks(&scope()).unwrap(),
        &ZSetRange {
            min: std::ops::Bound::Included(0.0),
            max: std::ops::Bound::Included(0.0),
            after: None,
            limit: NonZeroU32::new(128).unwrap(),
        },
    )
    .await
    .unwrap()
    .into_iter()
    .map(|entry| entry.member)
    .collect();
    assert!(listed.contains(&guessed.0), "the staged pre-write is real");
    assert_eq!(
        rig.feed.restore(&scope(), &guessed).await.unwrap(),
        rig.feed.restore(&scope(), &nonexistent).await.unwrap()
    );
    assert_eq!(
        rig.feed
            .load_snapshot(&scope(), &guessed)
            .await
            .unwrap(),
        rig.feed
            .load_snapshot(&scope(), &nonexistent)
            .await
            .unwrap()
    );
    assert_eq!(
        rig.feed.validate_resume(&scope(), &cursor(&guessed)).await,
        rig.feed
            .validate_resume(&scope(), &cursor(&nonexistent))
            .await
    );
    assert_eq!(
        rig.feed
            .list(
                &scope(),
                TaskFilter {
                    sid: None,
                    statuses: vec![],
                },
                PageRequest {
                    after: None,
                    limit: NonZeroU32::new(16).unwrap(),
                },
            )
            .await
            .unwrap()
            .items
            .len(),
        0
    );
    assert_eq!(one.rounds.get(&winner.input.key).await.unwrap(), None);

    // Old-RPC cancel against the staged session: rejected at the store seam,
    // zero physical writes.
    let writes_before = physical_writes(&rig.store);
    let cancel = AdmitRequest {
        scope: scope(),
        input: InputEnvelope {
            schema_version: 1,
            sid: Sid(SID.into()),
            tid: guessed.clone(),
            input_id: InputId("rpc-cancel".into()),
            business_digest: Digest("rpc-cancel".into()),
            body: InputBody::Cancel { reason: None },
        },
        create_session: None,
        create_task: None,
    };
    assert!(matches!(
        one.producer.admit(cancel).await,
        Err(StoreError::NotApplied { ref code }) if code == "strict_claim_source"
    ));
    assert_eq!(physical_writes(&rig.store), writes_before);

    // The abandoned attempt never gains permission: fail the pending
    // acceptance CAS definitely, restart with a fresh connection, and the
    // staged round stays invisible.
    rig.store.fail_write(1);
    pause.resume();
    assert!(pending.await.unwrap().is_err());
    let two = rig.gateway();
    assert_eq!(
        two.rounds.get(&winner.input.key).await.unwrap(),
        None,
        "an abandoned attempt stays invisible after restart"
    );
    assert_eq!(rig.feed.restore(&scope(), &guessed).await.unwrap(), None);
    assert_eq!(rig.rounds_list_is_empty().await, true);

    // Successful recovery shows exactly one round: one license, one Start.
    assert_eq!(
        two.rounds.admit(&winner).await.unwrap(),
        Admission::New { accepted: winner.clone() }
    );
    assert!(two
        .rounds
        .get(&winner.input.key)
        .await
        .unwrap()
        .is_some());
    let starts = rig.inbox_entries(&two).await;
    assert_eq!(starts, 1, "one durable Start entry for the round");
    assert!(matches!(
        two.rounds.admit(&winner).await.unwrap(),
        Admission::Replay { .. }
    ));
    assert_eq!(rig.inbox_entries(&two).await, 1);
}

impl Rig {
    async fn rounds_list_is_empty(&self) -> bool {
        self.feed
            .list(
                &scope(),
                TaskFilter {
                    sid: None,
                    statuses: vec![],
                },
                PageRequest {
                    after: None,
                    limit: NonZeroU32::new(16).unwrap(),
                },
            )
            .await
            .unwrap()
            .items
            .is_empty()
    }
}
