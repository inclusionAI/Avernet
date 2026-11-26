#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "terminal evidence provider tests"
)]
mod common;
mod task_feed_support;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{admission::*, result::*, session::*, task_feed::*};
use bcs_store_api::document::DocumentStorePlugin;
use common::*;
use task_feed_support::*;

async fn terminal(f: &Fixture) -> (AppendCommittedEvents, PublishCommittedSnapshot) {
    let record = FinalTaskRecord {
        schema_version: 1,
        scope: scope(),
        sid: Sid(SID.into()),
        tid: Tid(TID.into()),
        status: TerminalStatus::Completed,
        snapshot: task().content.snapshot,
        result: Payload::Inline(serde_json::json!({"done":true})),
        source_commit: CommitSeq(1),
        finalized_at: TimestampMillis(42),
    };
    let sources = f
        .commit(
            1,
            vec![EffectKind::SaveResult {
                record: record.clone(),
            }],
        )
        .await;
    let SavedResult::Saved(reference) = f
        .archive
        .save_final(sources[0].clone(), record)
        .await
        .unwrap()
    else {
        panic!()
    };
    let event = OutputEvent {
        id: event_id(2, 1),
        tid: Tid(TID.into()),
        body: OutputBody::Terminal {
            status: TerminalStatus::Completed,
            result_ref: reference.clone(),
        },
    };
    let mut snapshot = task();
    snapshot.source_commit = CommitSeq(2);
    snapshot.snapshot_event_id = Some(event.id);
    snapshot.content.status = TaskStatus::Completed;
    snapshot.content.result_ref = Some(reference);
    let sources = f
        .commit(
            2,
            vec![
                EffectKind::AppendEvents {
                    previous: None,
                    events: vec![event.clone()],
                },
                EffectKind::PublishSnapshot {
                    snapshot: snapshot.clone(),
                },
            ],
        )
        .await;
    (
        AppendCommittedEvents {
            source: sources[0].clone(),
            previous: None,
            events: vec![event],
        },
        PublishCommittedSnapshot {
            source: sources[1].clone(),
            snapshot,
        },
    )
}
#[tokio::test]
async fn closure_requires_both_terminal_publication_and_real_retained_completion() {
    let f = Fixture::new().await;
    let tid = Tid(TID.into());
    assert!(
        f.feed
            .terminal_evidence(&scope(), &tid)
            .await
            .unwrap()
            .is_none()
    );
    let (append, publish) = terminal(&f).await;
    f.feed.append(append).await.unwrap();
    assert!(
        f.feed
            .terminal_evidence(&scope(), &tid)
            .await
            .unwrap()
            .is_none()
    );
    f.feed.publish(publish).await.unwrap();
    let proof = f
        .feed
        .terminal_evidence(&scope(), &tid)
        .await
        .unwrap()
        .unwrap();
    bcs_test_support::contract::distributed::runtime_safety::terminal_task_evidence_port_contract_tests(&f.feed,&scope(),&tid,Some(proof.clone())).await.unwrap();
    assert_eq!(proof.terminal_event, event_id(2, 1));
    assert_eq!(proof.source_commit, CommitSeq(2));
    let key = f.layout.task_terminal_publish(&tid).unwrap();
    let mut marker = f.storage.read_document(&key).await.unwrap().unwrap();
    marker.value["completion"]["digest"] = serde_json::json!("forged");
    f.storage
        .replace_document(&key, marker.version, &marker.value)
        .await
        .unwrap();
    assert!(f.feed.terminal_evidence(&scope(), &tid).await.is_err());
}
#[tokio::test]
async fn lost_terminal_marker_reply_is_repaired_and_proof_survives_trim() {
    let f = Fixture::new().await;
    let tid = Tid(TID.into());
    let (append, publish) = terminal(&f).await;
    f.feed.append(append.clone()).await.unwrap();
    // Publish writes task, completion, then marker. Lost marker reply is resolved
    // by the provider's exact immutable content read, or the identical replay.
    f.storage.lose_write(3);
    let _ = f.feed.publish(publish.clone()).await;
    f.feed.publish(publish).await.unwrap();
    let before = f
        .feed
        .terminal_evidence(&scope(), &tid)
        .await
        .unwrap()
        .unwrap();
    f.feed
        .trim(RetentionRequest {
            sid: Sid(SID.into()),
            keep_from: event_id(3, 1),
        })
        .await
        .unwrap();
    assert_eq!(
        f.reopen()
            .terminal_evidence(&scope(), &tid)
            .await
            .unwrap()
            .unwrap(),
        before
    );
    f.feed.append(append).await.unwrap();
    assert_eq!(
        f.feed
            .terminal_evidence(&scope(), &tid)
            .await
            .unwrap()
            .unwrap(),
        before
    );
}
