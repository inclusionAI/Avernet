use super::*;

#[tokio::test]
async fn raced_running_ancestor_prevents_skipping_its_descendants() {
    let (h, runs) = harness(&["done"]).await;
    let started = h.start(loop_yaml(4, true, false, 1), false).await;
    let run = &started.view.run;
    let plan = h.plan(&run.run_id).await;
    *runs.race_skip_node.lock().unwrap() = Some(iteration_id(&plan, 2));
    terminal(&h, 0, run, "break").await.unwrap();
    assert_eq!(h.store.get_node_run(&run.run_id, &iteration_id(&plan, 2)).await.unwrap().unwrap().status, StateMachineNodeStatus::Running);
    for iteration in 3..=4 {
        assert_eq!(h.store.get_node_run(&run.run_id, &iteration_id(&plan, iteration)).await.unwrap().unwrap().status, StateMachineNodeStatus::Pending);
    }
    assert_eq!(runs.skip_batch_calls.load(Ordering::SeqCst), 1);
    assert_eq!(runs.status_batch_calls.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn cancellation_between_status_read_and_skip_does_not_write() {
    let (h, runs) = harness(&["done"]).await;
    let started = h.start(loop_yaml(4, true, false, 1), false).await;
    let run = &started.view.run;
    runs.cancel_before_skip.store(true, Ordering::SeqCst);
    let _ = terminal(&h, 0, run, "break").await;
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Aborted);
    assert!(h.store.list_node_runs(&run.run_id).await.unwrap().iter().all(|n| n.status != StateMachineNodeStatus::Skipped));
    assert_eq!(runs.skip_batch_calls.load(Ordering::SeqCst), 1);
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn repeated_skip_contention_is_bounded_and_recoverable() {
    let (h, runs) = harness(&["done"]).await;
    let started = h.start(loop_yaml(4, true, false, 1), false).await;
    let run = &started.view.run;
    runs.skip_conflicts.store(3, Ordering::SeqCst);
    assert!(terminal(&h, 0, run, "break").await.is_err());
    assert_eq!(runs.skip_batch_calls.load(Ordering::SeqCst), 3);
    assert_eq!(runs.status_batch_calls.load(Ordering::SeqCst), 3);
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 2);
}

#[tokio::test]
async fn maximum_nested_skip_recovers_after_one_committed_batch() {
    let (h, runs) = harness(&["approved", "done"]).await;
    let mut definition: Value = serde_yaml::from_str(&nested_loop_yaml()).unwrap();
    let outer = &mut definition["runtime"]["state_machine"]["nodes"]["outer"]["loop"];
    outer["max_iterations"] = json!(31);
    outer["nodes"]["inner"]["loop"]["max_iterations"] = json!(32);
    let started = h.start(serde_yaml::to_string(&definition).unwrap(), false).await;
    let run = &started.view.run;
    h.finish(0, run, "prepared").await;
    h.finish(1, run, "drafted").await;
    h.finish(2, run, "approved-inner").await;
    runs.fail_skip_after.store(2, Ordering::SeqCst);
    assert!(terminal(&h, 3, run, "done-outer").await.is_err());
    assert_eq!(h.store.list_node_runs(&run.run_id).await.unwrap().iter().filter(|n| n.status == StateMachineNodeStatus::Skipped).count(), 262);
    runs.skip_batch_calls.store(0, Ordering::SeqCst);
    runs.get_run_calls.store(0, Ordering::SeqCst);
    recover(&h).await;
    assert_eq!(runs.skip_calls.load(Ordering::SeqCst), 0);
    assert_eq!(runs.skip_batch_calls.load(Ordering::SeqCst), 9);
    assert!(runs.get_run_calls.load(Ordering::SeqCst) < 40);
    assert_eq!(h.store.list_node_runs(&run.run_id).await.unwrap().iter().filter(|n| n.status == StateMachineNodeStatus::Skipped).count(), 2042);
    assert_eq!(h.finish(4, run, "published").await.view.unwrap().run.status, StateMachineRunStatus::Completed);
}
