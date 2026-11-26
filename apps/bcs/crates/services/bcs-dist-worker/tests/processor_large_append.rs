#![allow(
    clippy::unwrap_used,
    reason = "actual-provider large AppendText regression"
)]
mod processor_support;
mod support;
use bcs_dist_model::*;
use bcs_service_api::{core::dist_runner::*, port::repo::distributed::task_feed::*};
use futures::StreamExt;
use processor_support::*;
use std::sync::Arc;

struct AppendingRunner;
impl DistRunnerCoreService for AppendingRunner {
    fn init(&self, context: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        FinishingRunner::default().init(context)
    }
    fn reduce(
        &self,
        context: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        let mut decision = FinishingRunner {
            text: 1_048_574,
            ..Default::default()
        }
        .reduce(context, state, inputs)?;
        let ActionIntent::Show { update, .. } = &mut decision.actions[0] else {
            panic!()
        };
        let ArtifactUpdate::Upsert { content } = update else {
            panic!()
        };
        *update = ArtifactUpdate::AppendText {
            delta: content.clone(),
        };
        Ok(decision)
    }
}

#[tokio::test]
async fn one_mib_append_externalizes_outbox_and_restores_the_same_complete_text() {
    let f = Fixture::new(Arc::new(AppendingRunner)).await;
    let tid = f.admit("large-append").await;
    let Some(RestoredTask::Live { cursor, .. }) =
        f.feed.restore(&spec().scope, &tid).await.unwrap()
    else {
        panic!()
    };
    f.warm().await;
    for _ in 0..100 {
        f.step().await.unwrap();
        if f.snapshot(&tid).await.content.status == TaskStatus::Completed {
            break;
        }
    }
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Completed);
    let requests = f.publisher.append_requests.lock().unwrap().clone();
    assert!(
        requests
            .iter()
            .flat_map(|r| &r.events)
            .any(|event| matches!(
                &event.body,
                OutputBody::Artifact {
                    update: ArtifactUpdate::AppendText {
                        delta: Payload::Ref(_)
                    },
                    ..
                }
            ))
    );
    let Some(RestoredTask::Final(view)) = f.feed.restore(&spec().scope, &tid).await.unwrap() else {
        panic!()
    };
    let Payload::Inline(snapshot) = view.content.snapshot else {
        panic!()
    };
    let display: DisplaySnapshot = serde_json::from_value(snapshot).unwrap();
    assert_eq!(display.units.len(), 1);
    assert_eq!(
        display.units[0].content,
        serde_json::json!("x".repeat(1_048_574))
    );
    let mut follow = f.feed.follow(spec().scope, cursor);
    let mut deltas = Vec::new();
    while let Some(item) = tokio::time::timeout(std::time::Duration::from_secs(2), follow.next())
        .await
        .unwrap()
    {
        let FollowItem::Events { items, .. } = item.unwrap() else {
            panic!("unexpected Gap")
        };
        for event in items {
            if let OutputBody::Artifact {
                update: ArtifactUpdate::AppendText { delta },
                ..
            } = event.body
            {
                deltas.push(delta);
            }
        }
    }
    assert_eq!(
        deltas,
        vec![Payload::Inline(serde_json::json!("x".repeat(1_048_574)))]
    );
}
