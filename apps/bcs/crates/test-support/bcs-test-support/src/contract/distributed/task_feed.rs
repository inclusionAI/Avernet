use super::ContractResult;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::task_feed::*;
use futures::StreamExt;
use std::time::Duration;

/// First append is committed and fresh; changed is a same-ID content collision.
pub async fn task_publisher_port_contract_tests(
    port: &dyn TaskPublisherPort,
    first: AppendCommittedEvents,
    changed: AppendCommittedEvents,
    snapshot: PublishCommittedSnapshot,
) -> ContractResult {
    assert_eq!(port.append(first.clone()).await?, AppendOutcome::Appended);
    assert_eq!(port.append(first).await?, AppendOutcome::AlreadyApplied);
    assert_eq!(port.append(changed).await?, AppendOutcome::ContentConflict);
    assert_eq!(
        port.publish(snapshot.clone()).await?,
        PublishOutcome::Published
    );
    assert_eq!(
        port.publish(snapshot).await?,
        PublishOutcome::AlreadyApplied
    );
    Ok(())
}

/// Fixture has a live consistent cut and an expired cursor from the SAME task.
pub async fn task_reader_port_contract_tests(
    port: &dyn TaskReaderPort,
    scope: &TaskScope,
    expected: &TaskView,
    expired: TaskCursor,
) -> ContractResult {
    match port.restore(scope, &expected.tid).await? {
        Some(RestoredTask::Live { view, .. }) => assert_eq!(&view, expected),
        other => panic!("expected a live restored cut: {other:?}"),
    }
    assert_eq!(
        port.validate_resume(scope, &expired).await?,
        ResumeCheck::Gap
    );
    let mut follow = port.follow(scope.clone(), expired);
    let item = tokio::time::timeout(Duration::from_secs(2), follow.next()).await?;
    assert!(matches!(item, Some(Ok(FollowItem::Gap))));
    Ok(())
}
