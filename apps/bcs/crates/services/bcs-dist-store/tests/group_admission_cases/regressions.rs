use super::*;

#[tokio::test]
async fn message_cannot_install_in_two_contexts() {
    let rig = Rig::new().await;
    let gateway = rig.gateway();
    let first = round("shared-message", 1);
    assert!(matches!(gateway.rounds.admit(&first).await.unwrap(), Admission::New { .. }));
    let mut second = first.clone();
    second.input.key.sid = Sid("s0042.second.1".into());
    second.input.key.tid = Tid("s0042.second.1.shared-message.g1".into());
    let mut second_spec = spec(GROUP);
    second_spec.sid = second.input.key.sid.clone();
    rig.store.create_document(&rig.layout.spec(&second_spec.sid).unwrap(),
        &serde_json::to_value(second_spec).unwrap()).await.unwrap();
    assert_eq!(gateway.rounds.admit(&second).await.unwrap(), Admission::Conflict);
    assert!(gateway.rounds.get(&second.input.key).await.unwrap().is_none());
}

#[tokio::test]
async fn competing_contexts_cannot_share_one_prepared_message() {
    let rig = Rig::new().await;
    let gateway = rig.gateway();
    let first = round("concurrent-message", 1);
    let mut second = first.clone();
    second.input.key.sid = Sid("s0042.second.1".into());
    second.input.key.tid = Tid("s0042.second.1.concurrent-message.g1".into());
    let mut second_spec = spec(GROUP);
    second_spec.sid = second.input.key.sid.clone();
    rig.store.create_document(&rig.layout.spec(&second_spec.sid).unwrap(),
        &serde_json::to_value(second_spec).unwrap()).await.unwrap();
    let pause = rig.store.pause_after("before.document.replace", &rig.ckpt_key(), 1);
    let pending = tokio::spawn({
        let rounds = gateway.rounds.clone();
        let first = first.clone();
        async move { rounds.admit(&first).await }
    });
    pause.reached().await;
    assert_eq!(gateway.rounds.admit(&second).await.unwrap(), Admission::Conflict);
    pause.resume();
    assert!(matches!(pending.await.unwrap().unwrap(), Admission::New { .. }));
    assert!(gateway.rounds.get(&second.input.key).await.unwrap().is_none());
}

#[tokio::test]
async fn strict_round_accepts_authenticated_connector_terminal() {
    let rig = Rig::new().await;
    let gateway = rig.gateway();
    let request = round("connector", 1);
    gateway.rounds.admit(&request).await.unwrap();
    let did = Did(format!("{}.node.1", request.input.key.tid.0));
    let event_key = bcs_dist_encoding::connector::KeyEvent::Final;
    let input = InputEnvelope {
        schema_version: 1,
        sid: request.input.key.sid.clone(),
        tid: request.input.key.tid.clone(),
        input_id: bcs_dist_encoding::connector::key_input_id(&did, event_key),
        business_digest: Digest("recomputed".into()),
        body: InputBody::Agent { event: AgentEvent {
            did, holder_epoch: HolderEpoch(1), seq: InputSeq(1),
            kind: AgentEventKind::Final, remote_event_id: None,
            payload: Payload::Inline(serde_json::json!({"state":"completed"})),
        } },
    };
    let admitted = gateway.producer.admit_connector(ConnectorAdmission {
        input,
        identity: ConnectorEventIdentity { state: "completed".into(), round: None },
    }).await.unwrap();
    assert!(matches!(admitted, AdmitOutcome::Accepted(_) | AdmitOutcome::Duplicate(_)));
}

/// Plan T7 slice 3 ruling: a Sid whose sealed checkpoint runner state carries
/// `directory_retired=true` (the CAS marker the directory reclaim registers
/// before removing discovery) must never admit NEW rounds again — the retry
/// of an already-installed round stays a replay.
#[tokio::test]
async fn retired_session_directory_rejects_new_admissions() {
    let rig = Rig::new().await;
    let gateway = rig.gateway();
    let first = round("retired-marker", 1);
    assert!(matches!(
        gateway.rounds.admit(&first).await.unwrap(),
        Admission::New { .. }
    ));
    // The reclaim-side CAS: the sealed owner registers the retirement marker
    // in the runner state and clears the scheduling duty, exactly like the
    // retention prototype's `directory-retire` commit.
    let snapshot = match rig
        .coordination
        .acquire(&Sid(SID.into()), &rig.claim, empty_checkpoint())
        .await
        .unwrap()
    {
        AcquireOutcome::Acquired(a) => a,
        other => panic!("{other:?}"),
    };
    let mut candidate = snapshot.envelope.checkpoint.clone();
    candidate.tasks.clear();
    candidate.commit_seq = CommitSeq(snapshot.envelope.checkpoint.commit_seq.0 + 1);
    if let Some(runner) = candidate.runner_state.as_mut() {
        runner.state.value["directory_retired"] = serde_json::json!(true);
    }
    let commit_id = CommitId(format!("retire-{}", candidate.commit_seq.0));
    let content_digest = Digest(serde_json::to_string(&candidate.runner_state).unwrap());
    candidate.outbox = Some(CommittedBatch {
        created_at: None,
        commit_id: commit_id.clone(),
        source_commit: candidate.commit_seq,
        content_digest: content_digest.clone(),
        effects: vec![],
    });
    assert!(matches!(
        rig.coordination
            .commit(CommitRequest {
                purpose: CommitPurpose::Ordinary { control_revision: 0 },
                owner: snapshot.ownership,
                expected_commit_seq: CommitSeq(candidate.commit_seq.0 - 1),
                commit_id,
                content_digest,
                candidate,
            })
            .await
            .unwrap(),
        CommitOutcome::Committed(_)
    ));
    // New message keys (fresh generations) on the retired Sid never install.
    let second = round("retired-next", 2);
    assert_eq!(
        gateway.rounds.admit(&second).await.unwrap(),
        Admission::Conflict,
        "a retired Sid must not admit new rounds"
    );
    // The same-request retry of the already-installed round stays a replay.
    assert!(matches!(
        gateway.rounds.admit(&first).await.unwrap(),
        Admission::Replay { .. }
    ));
}
