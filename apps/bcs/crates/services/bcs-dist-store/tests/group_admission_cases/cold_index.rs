//! Task 19 C2: the terminal CAS carries the closed rounds' cold index
//! verbatim. Three consecutive rounds of one session (Z → A → B) keep every
//! closed predecessor resolvable through the authoritative cold cut — the
//! install's own contract ("superseded rounds stay publicly resolvable") is
//! the assertion, and the closing writer is the store's real terminal
//! spine (`commit_terminal`), taken once per round with the deterministic
//! window proof both close drivers (the Worker's Done boundary and the
//! admission gate) share.
use super::*;
use bcs_service_api::port::repo::distributed::result::ArchiveBudget;
use bcs_service_api::port::repo::distributed::session::Effect as SessionEffect;

/// One finalized strict round's evidence: the round task reaches its Done
/// boundary with a sealed final record and the archived final product,
/// staged through the same checkpoint CAS the Worker's own terminal
/// transition takes. The sealed facts (Done task + result reference +
/// finalization record + SaveResult effect batch) are exactly what the
/// gate's deterministic window proof consumes.
async fn close_licensed_round(
    rig: &Rig,
    artifacts: &Arc<bcs_dist_store::group_artifact::GroupArtifactStore>,
    request: &GroupStartRequest,
) {
    use bcs_service_api::port::repo::distributed::result::{TaskArchiveRepo as _};
    let key = request.input.key.clone();
    let sid = key.sid.clone();
    let snapshot = match rig
        .coordination
        .acquire(&sid, &rig.claim, empty_checkpoint())
        .await
        .unwrap()
    {
        AcquireOutcome::Acquired(a) => a,
        other => panic!("{other:?}"),
    };
    let head = snapshot.envelope.checkpoint.commit_seq.0;
    let record = FinalTaskRecord {
        schema_version: 1,
        scope: key.scope.clone(),
        sid: sid.clone(),
        tid: key.tid.clone(),
        status: TerminalStatus::Completed,
        snapshot: Payload::Inline(serde_json::json!({"format_version": 1, "units": []})),
        result: Payload::Inline(serde_json::json!({"round": key.tid.0})),
        source_commit: CommitSeq(head + 1),
        finalized_at: TimestampMillis(NOW),
    };
    let result_ref = bcs_dist_store::archive::result_blob_reference(&record).unwrap();
    let effect_kind = EffectKind::SaveResult { record: record.clone() };
    let effect_digest =
        bcs_dist_store::identity::digest_json(&serde_json::to_value(&effect_kind).unwrap());
    let commit_id = CommitId(format!("finalize-{}-{}", key.tid.0, head + 1));
    let source = EffectSource {
        commit: CommitReceipt {
            sid: sid.clone(),
            owner: snapshot.ownership.owner.clone(),
            commit_id: commit_id.clone(),
            content_digest: effect_digest.clone(),
            sequence: CommitSeq(head + 1),
            consumed: None,
            proof: "not-authority".into(),
        },
        effect_id: EffectId {
            sid: sid.clone(),
            commit: CommitSeq(head + 1),
            index: NonZeroU32::new(1).unwrap(),
        },
        content_digest: effect_digest.clone(),
    };
    let mut candidate = snapshot.envelope.checkpoint.clone();
    candidate.commit_seq = CommitSeq(head + 1);
    let task = candidate
        .tasks
        .iter_mut()
        .find(|task| task.tid == key.tid)
        .expect("the licensed round's committed task entry");
    task.phase = TaskPhase::Done;
    task.result_ref = Some(result_ref.clone());
    task.finalization = Some(TaskFinalization {
        operation_id: RequestId(format!("finalize-{}", key.tid.0)),
        record: record.clone(),
        terminal_event_id: None,
        terminal_published: false,
    });
    candidate.outbox = Some(CommittedBatch {
        created_at: None,
        commit_id: commit_id.clone(),
        source_commit: CommitSeq(head + 1),
        content_digest: effect_digest.clone(),
        effects: vec![SessionEffect {
            id: source.effect_id.clone(),
            content_digest: effect_digest.clone(),
            kind: effect_kind.clone(),
        }],
    });
    // The commit CAS verifies the batch digest against the request digest
    // (the store's own sealedCandidates carry their whole-batch identity).
    assert!(matches!(
        rig.coordination
            .commit(CommitRequest {
                purpose: CommitPurpose::Ordinary { control_revision: 0 },
                owner: snapshot.ownership,
                expected_commit_seq: CommitSeq(head),
                commit_id: commit_id.clone(),
                content_digest: effect_digest.clone(),
                candidate,
            })
            .await
            .unwrap(),
        CommitOutcome::Committed(_)
    ));
    // The published task snapshot: like the Worker's terminal publication
    // lane, the final face of the round's task is the stored snapshot the
    // public reader serves while it exists.
    let task_key = rig.layout.task(&key.tid).unwrap();
    let stored = rig
        .store
        .read_document(&task_key)
        .await
        .unwrap()
        .expect("the round's task snapshot exists");
    let mut snapshot: TaskSnapshot = serde_json::from_value(stored.value.clone()).unwrap();
    snapshot.content.status = TaskStatus::Completed;
    snapshot.content.result_ref = Some(result_ref.clone());
    snapshot.updated_at = TimestampMillis(NOW);
    rig.store
        .replace_document(
            &task_key,
            stored.version,
            &serde_json::to_value(&snapshot).unwrap(),
        )
        .await
        .unwrap();

    // The archived final product: the Worker's own commit evidence is the
    // archive authority, and the cold confirm of the terminal window reads
    // exactly this committed row.
    let archive = TaskFinalArchive {
        schema_version: 2,
        round: key.clone(),
        record: record.clone(),
        public_history: Vec::new(),
        result_digest: result_ref.digest.clone(),
        artifacts: Vec::new(),
    };
    artifacts
        .archive_final(source, archive)
        .await
        .expect("the round's final archive commits from its sealed effect");
}

/// Closes one licensed round's owner-fenced window through the
/// PRODUCTION terminal port: the deterministic proof of the sealed Done
/// facts, committed by `commit_terminal` — the same entry the Worker's
/// strict-round close drives (and the gate re-drives on recovery). The
/// terminal CAS this call takes is exactly the writer that must carry the
/// existing cold index verbatim.
async fn seal_licensed_window(rig: &Rig, gateway: &Gateway, request: &GroupStartRequest) {
    use bcs_service_api::port::repo::distributed::group_round::GroupRoundRepo as _;
    let key = request.input.key.clone();
    let sid = key.sid.clone();
    let documents: Arc<dyn DocumentStorePlugin> = rig.store.clone();
    let owned = match rig
        .coordination
        .acquire(&sid, &rig.claim, empty_checkpoint())
        .await
        .unwrap()
    {
        AcquireOutcome::Acquired(a) => a,
        other => panic!("{other:?}"),
    };
    let license = bcs_dist_store::group_admission::probe_strict_license(
        &documents,
        &rig.layout,
        &sid,
    )
    .await
    .unwrap()
    .expect("the round stays licensed while it closes");
    let proof = bcs_dist_store::group_terminal::sealed_window_proof(
        &owned.envelope.checkpoint,
        &license,
    )
    .expect("the staged Done facts seal the window");
    gateway
        .rounds
        .commit_terminal(&key, &proof)
        .await
        .expect("the real terminal spine closes the authority window");
}

/// Z→A→B of one session: every closed round keeps its cold index entry and
/// its public resolvability. Before the fix the terminal CAS of A dropped
/// the already-condensed cold entry of Z, and B's install rebuilt the index
/// from A's hot marker alone — `get(Z)` was permanently lost.
#[tokio::test]
async fn three_rounds_keep_every_closed_predecessor_cold_resolvable() {
    let rig = Rig::new().await;
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let artifacts = Arc::new(
        bcs_dist_store::group_artifact::GroupArtifactStore::new(
            db,
            rig.store.clone(),
            rig.layout.clone(),
            Arc::new(bcs_dist_store::group_artifact::JsonProjectionValidator),
            ArchiveBudget {
                version: 1,
                profile: RoundBudgetProfile::v1(),
            },
        )
        .unwrap(),
    );
    let gateway = rig.gateway_with_archives(Some(artifacts.clone()));

    // Z installs at generation one and closes through its Done facts.
    let z = round("cold-z", 1);
    assert!(matches!(
        gateway.rounds.admit(&z).await.unwrap(),
        Admission::New { .. }
    ));
    close_licensed_round(&rig, &artifacts, &z).await;
    seal_licensed_window(&rig, &gateway, &z).await;

    // A installs with Z condensed into the cold index, runs to its own
    // Done facts, and closes through the same spine.
    let a = round("cold-a", 2);
    assert!(matches!(
        gateway.rounds.admit(&a).await.unwrap(),
        Admission::New { .. }
    ));
    close_licensed_round(&rig, &artifacts, &a).await;
    seal_licensed_window(&rig, &gateway, &a).await;

    // B installs after A: the third generation of the same session.
    let b = round("cold-b", 3);
    assert!(matches!(
        gateway.rounds.admit(&b).await.unwrap(),
        Admission::New { .. }
    ));

    // The session checkpoint's retained cold index holds BOTH closed
    // predecessors: the terminal CAS of A carried Z verbatim.
    let sid = Sid(SID.into());
    let raw = rig
        .store
        .read_document(&rig.layout.ckpt(&sid).unwrap())
        .await
        .unwrap()
        .expect("the session checkpoint");
    let cold = raw
        .value
        .pointer("/checkpoint/runner_state/state/value/group_cold")
        .cloned()
        .unwrap_or(serde_json::Value::Array(Vec::new()));
    for request in [&z, &a] {
        let tid = request.input.key.tid.0.as_str();
        assert!(
            cold.as_array().is_some_and(|entries| {
                entries
                    .iter()
                    .any(|entry| entry["tid"] == serde_json::json!(tid))
            }),
            "the closed round {tid} must keep its cold index entry: {cold}"
        );
    }

    // Both closed rounds resolve through the authoritative cut with their
    // own archived results — the predecessor stays publicly readable after
    // two successor generations took the session.
    for request in [&z, &a] {
        let view = gateway
            .rounds
            .get(&request.input.key)
            .await
            .unwrap()
            .unwrap_or_else(|| {
                panic!(
                    "round {} vanished from the cold cut",
                    request.input.key.tid.0
                )
            });
        assert!(view.terminal.is_some(), "the cold cut serves the terminal");
        assert_eq!(
            view.result,
            Some(Payload::Inline(serde_json::json!({
                "round": request.input.key.tid.0,
            }))),
            "the cold cut serves the round's own archived result"
        );
    }

    // B remains the licensed current round: its own authority cut is live.
    let b_view = gateway.rounds.get(&b.input.key).await.unwrap();
    assert!(
        b_view.is_some_and(|view| view.terminal.is_none()),
        "the successor generation keeps its licensed open round"
    );
}
