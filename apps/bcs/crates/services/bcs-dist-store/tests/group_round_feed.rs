#![allow(clippy::unwrap_used, reason = "protocol assertions")]
#[path = "group_round_support/mod.rs"]
mod support;
use support::*;
use bcs_service_api::port::repo::distributed::group_round::GroupRoundRepo;

#[tokio::test]
async fn independent_publisher_recovers_append_and_snapshot_after_new_round() {
    let p=Prototype::new().await;
    let a=request("a","first",1);
    p.admit(&a).await.unwrap();p.archive(&a.input.key,"result-a").await.unwrap();p.finish(&a.input.key).await.unwrap();
    let permit=p.feed_permission(&a.input.key).await.unwrap();
    let b=request("a","second",2);
    p.admit(&b).await.unwrap();p.dispatch(&b.input.key).await.unwrap();
    let current=p.takeover().await;
    // Old instance has lost execution ownership, but committed publication
    // permission is independent and remains valid for exactly these effects.
    p.append_permitted(&permit).await.unwrap();
    let current=current.takeover().await;
    current.publish_permitted(&permit).await.unwrap();
    current.publish_permitted(&permit).await.unwrap();
    assert_eq!(current.feed_events(&a.input.key.sid).await.unwrap().len(),2);
    assert_eq!(current.feed_snapshot(&a.input.key).await.unwrap()["result"],"result-a");
    assert!(current.dispatch(&a.input.key).await.is_err());
}

#[tokio::test]
async fn publisher_rejects_tampering_and_later_round_before_predecessor() {
    let p=Prototype::new().await;
    let a=request("a","first",1);
    p.admit(&a).await.unwrap();p.archive(&a.input.key,"result-a").await.unwrap();p.finish(&a.input.key).await.unwrap();
    let permit=p.feed_permission(&a.input.key).await.unwrap();
    let mut forged=permit.clone();forged["events"][0]["result"]=serde_json::json!("forged");
    assert!(p.append_permitted(&forged).await.is_err());
    assert!(p.publish_permitted(&permit).await.is_err());
    let b=request("a","second",2);
    p.admit(&b).await.unwrap();p.archive(&b.input.key,"result-b").await.unwrap();p.finish(&b.input.key).await.unwrap();
    let next=p.feed_permission(&b.input.key).await.unwrap();
    assert!(p.append_permitted(&next).await.is_err());
    p.append_permitted(&permit).await.unwrap();p.publish_permitted(&permit).await.unwrap();
    p.append_permitted(&next).await.unwrap();p.publish_permitted(&next).await.unwrap();
    let events=p.feed_events(&a.input.key.sid).await.unwrap();
    assert_eq!(events.len(),4);
    assert_eq!(events[0]["key"]["tid"],a.input.key.tid.0);
    assert_eq!(events[2]["key"]["tid"],b.input.key.tid.0);
}
