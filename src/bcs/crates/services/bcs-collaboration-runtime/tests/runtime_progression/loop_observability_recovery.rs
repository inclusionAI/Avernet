use super::*;
use super::super::observability_tests::RecordingLoopMetrics;
use bcs_domain::StateMachineLoopRouteKind as Route;
use bcs_service_api::{StateMachineLoopMetric as Metric, StateMachineLoopOutcome as Outcome};

#[tokio::test]
async fn loop_observations_do_not_recount_a_committed_result_after_runtime_replacement() {
    for (outcome, route, iterations) in [("done", Route::Break, 1), ("again", Route::Exhausted, 1), ("again", Route::Continue, 2)] {
        let (mut h, runs) = harness(&[outcome]).await;
        let metrics = Arc::new(RecordingLoopMetrics::default());
        h.runtime = h.runtime.with_loop_instrumentation(Some(metrics.clone()));
        let run = h.start(loop_yaml(iterations, true, false, 1), false).await.view.run;
        runs.fail_dispatch.store(true, Ordering::SeqCst);
        assert!(terminal(&h, 0, &run, "original result").await.is_err());
        let mut expected = vec![Metric::IterationStarted,
            Metric::IterationCompleted { route, outcome: Outcome::from_outcome(outcome) }];
        assert_eq!(*metrics.0.lock().unwrap(), expected);
        h.definitions.hide_definitions.store(true, Ordering::SeqCst);
        install_runtime(&mut h, runs, &[]).await;
        h.runtime = h.runtime.with_loop_instrumentation(Some(metrics.clone()));
        recover(&h).await;
        recover(&h).await;
        if route == Route::Continue { expected.push(Metric::IterationStarted); }
        assert_eq!(*metrics.0.lock().unwrap(), expected);
        assert_eq!(h.delivery.commands.lock().await.len(), 2);
    }
}

#[tokio::test]
async fn loop_observations_count_recovered_judge_only_after_successful_commit() {
    let (mut h, runs) = harness(&["done", "again"]).await;
    let metrics = Arc::new(RecordingLoopMetrics::default());
    h.runtime = h.runtime.with_loop_instrumentation(Some(metrics.clone()));
    let run = h.start(loop_yaml(2, true, false, 2), false).await.view.run;
    runs.fail_judge_finish.store(true, Ordering::SeqCst);
    assert!(terminal(&h, 0, &run, "saved judge input").await.is_err());
    assert_eq!(*metrics.0.lock().unwrap(), [Metric::IterationStarted]);
    recover(&h).await;
    recover(&h).await;
    assert_eq!(*metrics.0.lock().unwrap(), [Metric::IterationStarted,
        Metric::IterationCompleted { route: Route::Continue, outcome: Outcome::Other },
        Metric::IterationStarted]);
}
