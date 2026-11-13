use super::*;

#[path = "../../../../test-support/legacy_log_capture.rs"]
mod legacy;

#[test]
fn coverage_legacy_runtime_retry_and_completion_keep_attempts_and_logs_consistent() {
    legacy::isolated(|| {
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            let h = Harness::new(&[]).await;
            let started = h.start(loop_yaml(1, false, false, 2), false).await;
            let run = started.view.run;
            assert_eq!(h.delivery.commands.lock().await.len(), 1);
            let retried = h.fail(0, &run).await.view.unwrap();
            assert_eq!(retried.run.status, StateMachineRunStatus::Running);
            assert_eq!(h.delivery.commands.lock().await.len(), 2);
            let delivery_ids: Vec<_> = h.delivery.commands.lock().await.iter().map(|c| c.run_id.clone()).collect();
            assert_ne!(delivery_ids[0], delivery_ids[1], "retry gets a fresh delivery identity");
            assert_eq!(h.finish(1, &run, "recovered output").await.view.unwrap().run.status, StateMachineRunStatus::Running);
            assert!(h.prompt(2).await.contains("recovered output"));
            let completed = h.finish(2, &run, "published output").await.view.unwrap();
            assert_eq!(completed.run.status, StateMachineRunStatus::Completed);
            assert_eq!(completed.run.output.as_deref(), Some("published output"));
            let count = h.delivery.commands.lock().await.len();
            // A stale response to the rejected attempt cannot reopen the run or
            // dispatch another node after its successful retry completed.
            let duplicate = h.fail(0, &run).await;
            assert_eq!(duplicate.view.unwrap().run.status, StateMachineRunStatus::Completed);
            assert_eq!(h.delivery.commands.lock().await.len(), count);
            let rows = legacy::rows("bcs_message");
            let failed: Vec<_> = rows.iter().filter(|row| row.message.contains("node_result")
                && row.message.contains("status=\"failed\"")).collect();
            assert_eq!(failed.len(), 1, "failed attempt is logged once: {rows:?}");
            failed[0].assert_field("attempt", "0");
            failed[0].assert_field("log_class", "business_key");
            let finished: Vec<_> = rows.iter().filter(|row| row.message.contains("run_complete")).collect();
            assert_eq!(finished.len(), 1);
            finished[0].assert_field("state_machine_run_id", &run.run_id);
        });
    });
}

#[test]
fn coverage_legacy_runtime_exhausted_error_does_not_publish_a_success() {
    legacy::isolated(|| {
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            let h = Harness::new(&[]).await;
            let started = h.start(loop_yaml(1, false, false, 1), false).await;
            let run = started.view.run;
            let failed = h.fail(0, &run).await.view.unwrap();
            assert_eq!(failed.run.status, StateMachineRunStatus::Failed);
            assert_eq!(h.delivery.commands.lock().await.len(), 1, "failure cannot dispatch publish node");
            let rows = legacy::rows("bcs_message");
            let failures: Vec<_> = rows.iter().filter(|row| row.message.contains("run_failed")).collect();
            assert_eq!(failures.len(), 1);
            failures[0].assert_field("state_machine_run_id", &run.run_id);
            failures[0].assert_field("log_class", "business_key");
            assert_eq!(failures[0].level, tracing::log::Level::Warn);
            assert!(rows.iter().all(|row| !row.message.contains("run_complete")));
        });
    });
}
