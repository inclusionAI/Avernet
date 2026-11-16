use super::*;
use harness::task_feed::*;

#[tokio::test]
async fn publisher_checks_event_identity_content_and_snapshot_replay() -> ContractResult {
    let first = AppendCommittedEvents {
        source: source(),
        previous: None,
        events: vec![OutputEvent {
            id: event_id(),
            tid: input().tid,
            body: OutputBody::Status {
                status: TaskStatus::Working,
                interactions: vec![],
            },
        }],
    };
    let mut changed = first.clone();
    changed.source.content_digest = Digest("different-event-digest".into());
    changed.events[0].body = OutputBody::Status {
        status: TaskStatus::Failed,
        interactions: vec![],
    };
    let snapshot = PublishCommittedSnapshot {
        source: source(),
        snapshot: snapshot(),
    };
    for conflict in [true, false] {
        let script = Script::default()
            .expect(
                "TaskPublisherPort::append",
                (&first,),
                ok(AppendOutcome::Appended),
            )
            .expect(
                "TaskPublisherPort::append",
                (&first,),
                ok(AppendOutcome::AlreadyApplied),
            )
            .expect(
                "TaskPublisherPort::append",
                (&changed,),
                ok(if conflict {
                    AppendOutcome::ContentConflict
                } else {
                    AppendOutcome::AlreadyApplied
                }),
            )
            .expect(
                "TaskPublisherPort::publish",
                (&snapshot,),
                ok(PublishOutcome::Published),
            )
            .expect(
                "TaskPublisherPort::publish",
                (&snapshot,),
                ok(PublishOutcome::AlreadyApplied),
            );
        let test = task_publisher_port_contract_tests(
            &script,
            first.clone(),
            changed.clone(),
            snapshot.clone(),
        );
        if conflict {
            test.await?;
            script.done();
        } else {
            rejects(test, "assertion").await;
            script.remaining(2);
        }
    }
    Ok(())
}

#[tokio::test]
async fn reader_checks_consistent_cut_and_gap_at_delivery() -> ContractResult {
    let expected = task_view();
    let expired = TaskCursor {
        token: ReplayToken {
            value: "expired".into(),
            ..position().token
        },
        ..cursor()
    };
    for gap in [true, false] {
        let items = if gap {
            vec![ok(FollowItem::Gap)]
        } else {
            vec![]
        };
        let follow: BoxStream<'static, Result<FollowItem, StoreError>> =
            Box::pin(futures::stream::iter(items));
        let script = Script::default()
            .expect(
                "TaskReaderPort::restore",
                (scope(), &expected.tid),
                ok(Some(RestoredTask::Live {
                    view: expected.clone(),
                    cursor: cursor(),
                })),
            )
            .expect(
                "TaskReaderPort::validate_resume",
                (scope(), &expired),
                ok(ResumeCheck::Gap),
            )
            .expect("TaskReaderPort::follow", (scope(), &expired), follow);
        let scope = scope();
        let test = task_reader_port_contract_tests(&script, &scope, &expected, expired.clone());
        if gap {
            test.await?;
        } else {
            rejects(test, "assertion").await;
        }
        script.done();
    }
    Ok(())
}
