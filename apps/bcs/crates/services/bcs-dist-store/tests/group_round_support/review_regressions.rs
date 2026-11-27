use super::*;

#[tokio::test]
async fn recycle_cannot_erase_concurrently_accepted_work() {
    let p = Prototype::new().await;
    let a = request("a", "recycle-race", 1);
    p.prepare(&a).await.unwrap();
    p.acquired(&a.input.key.sid).await.unwrap();
    let reached = tokio::sync::Notify::new();
    let resume = tokio::sync::Notify::new();
    let recycling = p.recycle_directory_paused(&a.input.key.sid, Some((&reached, &resume)));
    let admission = async {
        reached.notified().await;
        let outcome = p.admit(&a).await;
        resume.notify_one();
        outcome
    };
    let (_, accepted) = tokio::join!(recycling, admission);
    assert!(matches!(accepted.unwrap(), Admission::New { .. }));
    assert_eq!(p.pending_work().await.unwrap(), vec![a.input.key]);
}

#[tokio::test]
async fn failed_attempt_delivery_retains_all_stop_obligations() {
    let p = Prototype::new().await;
    let a = request("a", "dispatch-window", 1);
    p.admit(&a).await.unwrap();
    p.replace(&format!("sink:{}#second", a.input.key.tid.0), None,
        json!({"conflicting":true})).await.unwrap();
    assert!(p.dispatch_attempts(&a.input.key, &["first", "second"]).await.is_err());
    assert!(p.fail_with_reason(&a.input.key, "delivery interrupted").await.is_err());
    assert!(p.get(&a.input.key).await.unwrap().unwrap().terminal.is_none());
}
