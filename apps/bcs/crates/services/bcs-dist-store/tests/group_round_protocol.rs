//! Positive T1 protocol tests. The prototype uses actual CoordinationStore CAS
//! and a test plugin; it is not the #4245 processor or a deployment provider.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "protocol assertions")]
#[path = "group_round_support/mod.rs"]
mod support;
use support::*;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::group_round::*;
use bcs_test_support::contract::distributed::group_round::group_round_repo_contract_tests;

#[tokio::test]
async fn conformance_group_round_repo() {
    let p = Prototype::new().await;
    group_round_repo_contract_tests(&p, request("a", "message-a", 1)).await.unwrap();
}

#[tokio::test]
async fn accepted_before_hint_is_discovered_and_dispatched_after_takeover() {
    let p = Prototype::new().await;
    let a = request("a", "message-a", 1);
    p.prepare(&a).await.unwrap();
    assert!(p.get(&a.input.key).await.unwrap().is_none());
    p.admit(&a).await.unwrap();
    let successor = p.takeover().await;
    assert_eq!(successor.discover().await.unwrap(), vec![a.input.key.sid.clone()]);
    successor.dispatch(&a.input.key).await.unwrap();
    assert_eq!(successor.deliveries().await.unwrap(), vec![a.input.key.clone()]);
    assert!(p.dispatch(&a.input.key).await.is_err());
}

#[tokio::test]
async fn archive_commit_feed_windows_allow_actual_next_dispatch() {
    let p = Prototype::new().await;
    let a = request("a", "message-a", 1);
    p.admit(&a).await.unwrap();
    p.dispatch(&a.input.key).await.unwrap();
    p.archive(&a.input.key, "answer-a").await.unwrap();
    let b = request("a", "message-b", 2);
    assert_eq!(p.admit(&b).await.unwrap(), Admission::Busy);
    assert!(p.get(&a.input.key).await.unwrap().unwrap().terminal.is_none());
    let p = p.takeover().await;
    assert!(p.finish(&a.input.key).await.is_err());
    p.stop_remote(&a.input.key).await.unwrap();
    p.finish(&a.input.key).await.unwrap();
    assert_eq!(p.get(&a.input.key).await.unwrap().unwrap().result, Some(Payload::Inline(serde_json::json!("answer-a"))));
    p.admit(&b).await.unwrap();
    p.dispatch(&b.input.key).await.unwrap();
    assert_eq!(p.deliveries().await.unwrap(), vec![a.input.key.clone(), b.input.key.clone()]);
    // Append is done, snapshot publication is not: recover the second obligation.
    p.append_feed(&a.input.key).await.unwrap();
    let p = p.takeover().await;
    p.publish_snapshot(&a.input.key).await.unwrap();
    p.publish_snapshot(&a.input.key).await.unwrap();
    assert_eq!(p.published().await.unwrap(), vec![a.input.key.clone()]);
    assert!(matches!(p.admit(&a).await.unwrap(), Admission::Replay { .. }));
}

#[tokio::test]
async fn unknown_commit_response_replays_same_task_without_duplicate_delivery() {
    let p = Prototype::new().await;
    let a = request("a", "message-a", 1);
    p.prepare(&a).await.unwrap();
    // Otherwise the fault hits checkpoint creation, not the acceptance CAS.
    p.initialize_checkpoint(&a.input.key.sid).await.unwrap();
    p.lose_next_commit_reply();
    assert!(p.admit(&a).await.is_err());
    let p = p.takeover().await;
    assert!(matches!(p.admit(&a).await.unwrap(), Admission::Replay { .. }));
    p.dispatch(&a.input.key).await.unwrap();
    p.dispatch(&a.input.key).await.unwrap();
    assert_eq!(p.deliveries().await.unwrap(), vec![a.input.key]);
}

#[tokio::test]
async fn replay_before_current_capabilities() {
    let p = Prototype::new().await;
    let a = request("a", "message-a", 1);
    p.submit(&a, true, true).await.unwrap();
    assert!(matches!(p.submit(&a, true, false).await.unwrap(), Admission::Replay { .. }));
    assert!(p.submit(&a, false, true).await.is_err());
    let mut changed = a.clone();
    changed.request_digest = Digest("changed-input".into());
    assert_eq!(p.submit(&changed, true, false).await.unwrap(), Admission::Conflict);
    assert!(p.submit(&request("b", "message-b", 1), true, false).await.is_err());
}

#[tokio::test]
async fn list_cut_waits_for_both_sids_and_keeps_an_immutable_page() {
    let p = Prototype::new().await;
    let a = request("a", "message-a", 1);
    let b = request("b", "message-b", 1);
    p.admit(&a).await.unwrap(); p.admit(&b).await.unwrap();
    p.project(&b.input.key.sid).await.unwrap();
    assert!(p.list_cut().await.is_err());
    p.project(&a.input.key.sid).await.unwrap();
    let cut = p.list_cut().await.unwrap();
    assert_eq!(cut.rows.len(), 2);
    p.archive(&a.input.key, "a").await.unwrap();
    p.finish(&a.input.key).await.unwrap();
    assert!(p.list_cut().await.is_err());
    assert!(cut.rows.iter().all(|r| r.view.terminal.is_none()));
    p.project(&a.input.key.sid).await.unwrap();
    let next = p.list_cut().await.unwrap();
    assert_eq!(next.rows.iter().filter(|r| r.view.terminal.is_some()).count(), 1);
}

#[tokio::test]
async fn concurrent_commit_during_cut_is_rejected_not_mixed() {
    let p=Prototype::new().await;
    let a=request("a","first",1);let b=request("b","other",1);
    p.admit(&a).await.unwrap();p.admit(&b).await.unwrap();
    p.project(&a.input.key.sid).await.unwrap();p.project(&b.input.key.sid).await.unwrap();
    let ready=tokio::sync::Notify::new();let resume=tokio::sync::Notify::new();
    let query=p.list_cut_paused(&ready,&resume);
    let writer=async {
        ready.notified().await;
        p.archive(&a.input.key,"answer").await.unwrap();p.finish(&a.input.key).await.unwrap();
        p.project(&a.input.key.sid).await.unwrap();resume.notify_one();
    };
    let (cut,())=tokio::join!(query,writer);
    assert!(cut.is_err());
    assert_eq!(p.list_cut().await.unwrap().rows.iter().filter(|r|r.view.terminal.is_some()).count(),1);
}

#[tokio::test]
async fn prepared_loser_cannot_be_automatically_admitted_in_next_generation() {
    let p = Prototype::new().await;
    let a = request("a", "message-a", 1);
    let loser = request("a", "loser", 1);
    p.prepare(&loser).await.unwrap();
    p.admit(&a).await.unwrap();
    assert_eq!(p.admit(&loser).await.unwrap(), Admission::Busy);
    p.archive(&a.input.key, "a").await.unwrap(); p.finish(&a.input.key).await.unwrap();
    assert!(p.admit(&loser).await.is_err());
    assert!(p.discover().await.unwrap().is_empty());
    let retry = request("a", "loser", 2);
    assert!(matches!(p.admit(&retry).await.unwrap(), Admission::New { .. }));
}
