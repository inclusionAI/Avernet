#![allow(clippy::unwrap_used, reason = "contract assertions")]
#[path = "group_round_support/mod.rs"]
mod support;
use support::*;
use bcs_service_api::port::repo::distributed::group_round::GroupRoundRepo;
use bcs_dist_model::*;

#[tokio::test]
async fn reservation_before_acceptance_is_unknown_then_safely_abandoned() {
    let p=Prototype::new().await;
    let a=request("a","same",1);
    let b=request("b","same",1);
    let attempt=p.reserve_only(&a).await.unwrap();
    assert!(p.admit(&b).await.is_err());
    assert!(p.get(&a.input.key).await.unwrap().is_none());
    p.abandon(&attempt).await.unwrap();
    assert!(p.commit_reserved(&attempt).await.is_err());
    assert!(matches!(p.admit(&b).await.unwrap(),Admission::New{..}));
}

#[tokio::test]
async fn prepared_request_recovers_original_basis_after_takeover() {
    let p=Prototype::new().await;
    let a=request("a","pending",1);
    p.reserve_only(&a).await.unwrap();
    let p=p.takeover().await;
    assert!(matches!(p.submit(&a,true,false).await.unwrap(),Admission::New{..}));
    assert!(matches!(p.submit(&a,true,false).await.unwrap(),Admission::Replay{..}));
}

#[tokio::test]
async fn two_reserved_requests_compete_and_loser_key_is_reusable() {
    let p=Prototype::new().await;
    let a=request("a","winner",1);
    let b=request("a","loser",1);
    let first=p.reserve_only(&a).await.unwrap();
    let second=p.reserve_only(&b).await.unwrap();
    assert!(matches!(p.commit_reserved(&first).await.unwrap(),Admission::New{..}));
    assert_eq!(p.commit_reserved(&second).await.unwrap(),Admission::Busy);
    p.archive(&a.input.key,"done").await.unwrap();p.finish(&a.input.key).await.unwrap();
    assert!(p.commit_reserved(&second).await.is_err());
    let changed_context=request("b","loser",1);
    assert!(matches!(p.admit(&changed_context).await.unwrap(),Admission::New{..}));
}

#[tokio::test]
async fn unaccepted_registry_entry_is_discovered_after_owner_activation() {
    let p=Prototype::new().await;
    let r=request("new","message",1);
    p.prepare(&r).await.unwrap();
    let p=p.takeover().await;
    assert!(p.get(&r.input.key).await.unwrap().is_none());
    p.admit(&r).await.unwrap();
    assert_eq!(p.discover().await.unwrap(),vec![r.input.key.sid.clone()]);
}
#[tokio::test]
async fn same_message_cannot_execute_in_two_sessions() {
    let p=Prototype::new().await;
    let a=request("a","same",1);
    let b=request("b","same",1);
    let (ra,rb)=tokio::join!(p.admit(&a),p.admit(&b));
    let results=[ra.unwrap(),rb.unwrap()];
    assert_eq!(results.iter().filter(|r|matches!(r,Admission::New{..})).count(),1);
    assert_eq!(results.iter().filter(|r|matches!(r,Admission::Conflict)).count(),1);
}
#[tokio::test]
async fn concurrent_same_request_is_replay_not_busy() {
    let p=Prototype::new().await;
    let a=request("a","same",1);
    let (left,right)=tokio::join!(p.admit(&a),p.admit(&a));
    let results=[left.unwrap(),right.unwrap()];
    assert_eq!(results.iter().filter(|r|matches!(r,Admission::New{..})).count(),1);
    assert_eq!(results.iter().filter(|r|matches!(r,Admission::Replay{..})).count(),1);
}

#[tokio::test]
async fn possible_delivery_blocks_finish_after_crash_before_ack() {
    let p=Prototype::new().await;
    let a=request("a","message",1);
    p.admit(&a).await.unwrap();
    p.dispatch_without_ack(&a.input.key).await.unwrap();
    p.archive(&a.input.key,"result").await.unwrap();
    let p=p.takeover().await;
    assert!(p.finish(&a.input.key).await.is_err());
    p.stop_remote(&a.input.key).await.unwrap();
    p.finish(&a.input.key).await.unwrap();
}

#[tokio::test]
async fn corrupt_result_and_wrong_stop_identity_are_not_proofs() {
    let p=Prototype::new().await;
    let a=request("a","message",1);
    p.admit(&a).await.unwrap(); p.dispatch(&a.input.key).await.unwrap();
    p.archive(&a.input.key,"result").await.unwrap();
    p.corrupt_stop(&a.input.key).await.unwrap();
    assert!(p.finish(&a.input.key).await.is_err());
    p.restore_stop(&a.input.key).await.unwrap();
    p.finish(&a.input.key).await.unwrap();
    p.corrupt_result(&a.input.key).await.unwrap();
    assert!(p.get(&a.input.key).await.is_err());
}

#[tokio::test]
async fn archive_mutation_does_not_change_committed_result() {
    let p=Prototype::new().await;
    let a=request("a","message",1);
    p.admit(&a).await.unwrap(); p.archive(&a.input.key,"original").await.unwrap();
    p.finish(&a.input.key).await.unwrap();
    assert!(p.archive(&a.input.key,"changed").await.is_err());
    assert_eq!(p.get(&a.input.key).await.unwrap().unwrap().result,
        Some(Payload::Inline(serde_json::json!("original"))));
}
