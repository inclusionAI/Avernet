//! Regression: archive authorization is not terminal authorization.
#[path = "group_artifact_support/mod.rs"]
mod support;
use support::*;
use bcs_service_api::port::repo::distributed::result::TaskArchiveRepo;
use std::num::NonZeroU64;

#[tokio::test]
async fn archived_result_without_terminal_commit_cannot_confirm_proof() {
    let f = GroupFixture::new(small_budget(64 * 1024)).await;
    let archive = task_archive(1, NonZeroU64::new(1).unwrap(), vec![text_part("p", 1, "answer")]);
    let source = f.commit(&archive).await;
    f.group.archive_final(source.clone(), archive.clone()).await.unwrap();
    assert!(f.group.confirm_terminal_proof(&scope(), &group(), &archive.round.tid,
        &proof_for(&archive, &source)).await.is_err());
    let proof = proof_for(&archive, &source);
    f.commit_terminal(&archive, &proof).await;
    // A correctly bound authority must still reject missing/corrupt closure bytes.
    use bcs_store_api::document::DocumentStorePlugin;
    let stored = f.inner.documents.read_document(&proof.closure_ref.key).await.unwrap().unwrap();
    f.inner.documents.replace_document(&proof.closure_ref.key, stored.version,
        &serde_json::json!({"key":archive.round,"stopped":false})).await.unwrap();
    assert!(f.group.confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &proof).await.is_err());
    let stored = f.inner.documents.read_document(&proof.closure_ref.key).await.unwrap().unwrap();
    f.inner.documents.replace_document(&proof.closure_ref.key, stored.version,
        &serde_json::json!({"key":archive.round,"stopped":true})).await.unwrap();
    let mut forged = proof.clone();
    forged.closure_ref.key = "closure:absent".into();
    assert!(f.group.confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &forged).await.is_err());
    f.group.confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &proof).await.unwrap();
}

/// Plan T7 关闭轮次后有界压缩: repeated closings keep a bounded checkpoint that
/// does not grow linearly with the archived round history. The metric: with
/// every round carrying an 8 KiB input payload and view draft, N consecutive
/// closings grow the checkpoint by at most a fixed skeleton per round — an
/// order of magnitude under the retained-history counterfactual — while the
/// event watermark, the retained terminal authority, all unfinished
/// obligations and all foreign tasks survive verbatim.
#[tokio::test]
async fn consecutive_round_closes_compact_the_checkpoint_to_a_bounded_skeleton() {
    use bcs_dist_store::group_terminal::compact_closed_round;
    use bcs_service_api::port::repo::distributed::group_round::StrictLicense;
    use bcs_service_api::port::repo::distributed::session::*;
    use bcs_dist_model::*;

    let license = |tid: &str| StrictLicense {
        schema_version: 1,
        scope: scope(),
        group: group(),
        sid: Sid("s0001.session".into()),
        tid: Tid(tid.into()),
        generation: NonZeroU64::new(1).unwrap(),
        request_id: "round-request".into(),
        request_digest: "digest".into(),
    };
    let input = || Payload::Inline(serde_json::Value::String("x".repeat(8 * 1024)));
    let open_task = |tid: &str| {
        TaskProgress {
            steps: 1,
            tid: Tid(tid.into()),
            phase: TaskPhase::Running,
            input: input(),
            result_ref: None,
            output_limits: PHASE_ONE_OUTPUT_LIMITS,
            output_usage: OutputUsage::default(),
            publication: Default::default(),
            finalization: None,
        }
    };
    let size = |checkpoint: &Checkpoint| serde_json::to_vec(checkpoint).unwrap().len();
    let watermark = EventId { commit: CommitSeq(7), index: std::num::NonZeroU32::new(9).unwrap() };
    let base = Checkpoint {
        format_version: 1,
        commit_seq: CommitSeq(1),
        consumed: None,
        tasks: [open_task("s0001.session.task-a"), open_task("s0001.session.task-z")]
            .into_iter()
            .collect(),
        runner_state: None,
        dispatch_seq: [(
            Did("s0001.session.task-b.d1.n1.a1".into()),
            DispatchSequence { tid: Tid("s0001.session.task-b".into()), holder_epoch: HolderEpoch(1), sequence: InputSeq(2) },
        )]
        .into_iter()
        .collect(),
        deadlines: vec![],
        deadline_history: Default::default(),
        view_drafts: Default::default(),
        last_event_id: Some(watermark.clone()),
        outbox: None,
        feed_publications: vec![FeedPublication {
            schema_version: 1,
            sid: Sid("s0001.session".into()),
            tid: Tid("s0001.session.task-a".into()),
            scope: scope(),
            group: group(),
            generation: NonZeroU64::new(1).unwrap(),
            commit_id: CommitId("c".into()),
            commit_digest: Digest("sha256:d".into()),
            consumed: None,
            effect: EffectId { sid: Sid("s0001.session".into()), commit: CommitSeq(1), index: std::num::NonZeroU32::MIN },
            content_digest: Digest("sha256:e".into()),
            kind: EffectKind::AppendEvents { previous: Some(watermark.clone()), events: vec![] },
        }],
    };
    let closed = size(&base) as i64;
    let round_input = serde_json::to_vec(&input()).unwrap().len() as i64;

    let mut checkpoint = base.clone();
    let mut growth = 0i64;
    for round in 0..8u32 {
        let tid = format!("s0001.session.task-closed-{round}");
        checkpoint.tasks.push_back(open_task(&tid));
        checkpoint.view_drafts.insert(
            Tid(tid.clone()),
            TaskContent {
                status: TaskStatus::Working,
                interactions: vec![],
                snapshot: input(),
                result_ref: None,
            },
        );
        checkpoint.deadlines.push(Deadline {
            id: DeadlineId(format!("d-{round}")),
            at: TimestampMillis(42 + round as i64),
            kind: DeadlineKind::Task { tid: Tid(tid.clone()) },
        });
        checkpoint.deadline_history.insert(
            DeadlineId(format!("d-prev-{round}")),
            Deadline {
                id: DeadlineId(format!("d-prev-{round}")),
                at: TimestampMillis(42 + round as i64),
                kind: DeadlineKind::Task { tid: Tid(tid.clone()) },
            },
        );
        // The closing round keeps its terminal authority in the runner state;
        // the compacting CAS then drops only this round's hot history.
        checkpoint.tasks.back_mut().unwrap().phase = TaskPhase::Done;
        compact_closed_round(&mut checkpoint, &license(&tid));
        // 连续多轮的 checkpoint 不随已归档历史线性增长: bounded skeleton only.
        growth = size(&checkpoint) as i64 - closed;
        assert!(
            growth < round_input / 4,
            "round {round}: checkpoint growth {growth} must stay far below one round's \
             retained input payload ({round_input})"
        );
    }
    // 拒绝旧事件的 watermark survives verbatim.
    assert_eq!(checkpoint.last_event_id, Some(watermark));
    // All unfinished obligations survive verbatim.
    assert_eq!(checkpoint.feed_publications.len(), 1);
    assert_eq!(
        checkpoint
            .dispatch_seq
            .get(&Did("s0001.session.task-b.d1.n1.a1".into()))
            .map(|seq| seq.sequence),
        Some(InputSeq(2))
    );
    // Foreign (unclosed) work survives verbatim.
    assert_eq!(checkpoint.tasks.len(), 2, "only the two open tasks remain");
    // The closed rounds' facts are gone: no hot history remains at all.
    assert!(checkpoint
        .tasks
        .iter()
        .all(|task| !task.tid.0.starts_with("s0001.session.task-closed")));
    assert!(checkpoint
        .deadlines
        .iter()
        .chain(checkpoint.deadline_history.values())
        .all(|deadline| !matches!(&deadline.kind, DeadlineKind::Task { tid } if tid.0.starts_with("s0001.session.task-closed"))));
    assert!(checkpoint.view_drafts.keys().all(|tid| !tid.0.starts_with("s0001.session.task-closed")));
    // The metric vector: total growth over 8 rounds of 8 KiB history stays
    // under one round's payload, orders below the 64 KiB history total.
    assert!(growth < round_input, "the growth is bounded by a skeleton, not the history");
}
