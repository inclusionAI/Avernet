use bcs_dist_model::*;
use bcs_service_api::{
    core::dist_runner::*,
    port::repo::distributed::{control::*, dispatch::DispatchControlPort, result::*, task_feed::*},
};
use crate::lifecycle_support::*;
use crate::processor_support::{FinishingRunner, Fixture};
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

struct QuotaRunner {
    initializations: AtomicUsize,
    calls: AtomicUsize,
}
#[async_trait::async_trait]
impl DistRunnerCoreService for QuotaRunner {
    async fn init(&self, c: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        self.initializations.fetch_add(1, Ordering::SeqCst);
        Runner::new(Mode::Stream).init(c).await
    }
    async fn reduce(
        &self,
        c: &EvaluationContext<'_>,
        state: &RunnerState,
        input: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        if let RunnerInput::Start { tid, .. } = &input[0] {
            if tid.0.ends_with(".healthy") {
                return FinishingRunner {
                    text: 8,
                    ..Default::default()
                }
                .reduce(c, state, input).await;
            }
            let mut decision = Runner::new(Mode::Stream).reduce(c, state, input).await?;
            let ActionIntent::Show { update, .. } = decision.actions.last_mut().unwrap() else {
                panic!()
            };
            *update = ArtifactUpdate::AppendText {
                delta: Payload::Inline(serde_json::json!("confirmed")),
            };
            return Ok(decision);
        }
        let RunnerInput::Agent { tid, .. } = &input[0] else {
            panic!()
        };
        Ok(Decision {
            next_state: state.clone(),
            next_deadlines: vec![],
            actions: (0..33).map(|index| ActionIntent::Show {
                tid: tid.clone(),
                source: CorrelationKey("n".into()),
                artifact_id: ArtifactId(format!("rejected-{index}")),
                class: OutputClass::Output,
                update: ArtifactUpdate::Upsert {
                    content: Payload::Inline(serde_json::json!("discard entire decision")),
                },
            }).collect(),
        })
    }
}
#[tokio::test(start_paused = true)]
async fn action_quota_failure_preserves_confirmed_output_rejects_entire_decision_and_initializes_next_task()
 {
    let runner = Arc::new(QuotaRunner {
        initializations: AtomicUsize::new(0),
        calls: AtomicUsize::new(0),
    });
    let f = Fixture::new(runner.clone()).await;
    let first = f.admit_for(session(), "overflow").await;
    let healthy = f.admit_for(session(), "healthy").await;
    f.warm().await;
    until_status(&f, &f.processor, &first, TaskStatus::Working).await;
    let confirmed = f.snapshot(&first).await;
    assert_eq!(confirmed.output_usage.cumulative_bytes, 11);
    let Payload::Inline(raw) = confirmed.content.snapshot else {
        panic!()
    };
    let display: DisplaySnapshot = serde_json::from_value(raw).unwrap();
    assert_eq!(display.units[0].content, serde_json::json!("confirmed"));
    delta(&f, &first, 1, "overflow-now").await;
    until_status(&f, &f.processor, &first, TaskStatus::Failed).await;
    until_status(&f, &f.processor, &healthy, TaskStatus::Completed).await;
    let Some(RestoredTask::Final(view)) = f.feed.restore(&session().scope, &first).await.unwrap()
    else {
        panic!()
    };
    let Payload::Inline(raw) = view.content.snapshot else {
        panic!()
    };
    let preserved: DisplaySnapshot = serde_json::from_value(raw).unwrap();
    assert_eq!(
        preserved, display,
        "confirmed content survives; neither action of the rejected batch appears"
    );
    assert_eq!(f.snapshot(&first).await.output_usage.cumulative_bytes, 11);
    let record = f
        .archive
        .get_final(&session().scope, &first)
        .await
        .unwrap()
        .unwrap();
    assert!(
        serde_json::to_string(&record)
            .unwrap()
            .contains("OutputQuotaExceeded")
    );
    assert!(
        f.dispatch
            .get(&dispatch_id(&first))
            .await
            .unwrap()
            .unwrap()
            .view
            .control
            .cancel
            .is_some()
    );
    assert_eq!(runner.initializations.load(Ordering::SeqCst), 2);
    assert_eq!(
        runner.calls.load(Ordering::SeqCst),
        3,
        "Start, rejected reduction, then fresh healthy Start"
    );
    assert!(matches!(
        f.coordination
            .load_control(&session().sid)
            .await
            .unwrap()
            .unwrap()
            .control
            .mode,
        ControlMode::Normal
    ));
    let Some(RestoredTask::Final(view)) = f.feed.restore(&session().scope, &healthy).await.unwrap()
    else {
        panic!()
    };
    let Payload::Inline(raw) = view.content.snapshot else {
        panic!()
    };
    let healthy_display: DisplaySnapshot = serde_json::from_value(raw).unwrap();
    assert_eq!(healthy_display.units.len(), 1);
    assert_eq!(
        healthy_display.units[0].content,
        serde_json::json!("xxxxxxxx")
    );
}
