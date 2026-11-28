#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "provider contract tests"
)]
mod common;
mod task_feed_support;
use bcs_dist_model::*;
use bcs_dist_store::task_feed::TaskFeedConfig;
use bcs_service_api::port::repo::distributed::common::PageRequest;
use bcs_service_api::port::repo::distributed::{session::*, task_feed::*};
use bcs_store_api::{document::DocumentStorePlugin, stream::StreamStorePlugin, zset::*};
use common::*;
use futures::StreamExt;
use std::{num::NonZeroU32, ops::Bound};
use task_feed_support::*;

#[tokio::test]
async fn initial_empty_snapshot_and_append_restore_share_pure_projection() {
    let f = Fixture::new().await;
    let RestoredTask::Live { view, .. } = f
        .feed
        .restore(&scope(), &Tid(TID.into()))
        .await
        .unwrap()
        .unwrap()
    else {
        panic!("live")
    };
    assert_eq!(display(&view).units.len(), 0);
    assert_eq!(view.covered_through, None);
    let stored = f
        .feed
        .load_snapshot(&scope(), &Tid(TID.into()))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(stored.output_limits, PHASE_ONE_OUTPUT_LIMITS);
    assert_eq!(stored.snapshot_event_id, None);
    let mut foreign = scope();
    foreign.principal = PrincipalId("foreign".into());
    assert!(
        f.feed
            .load_snapshot(&foreign, &Tid(TID.into()))
            .await
            .is_err()
    );
    let request = f.append_request(1, None, text_events(1)).await;
    assert_eq!(
        f.feed.append(request.clone()).await.unwrap(),
        AppendOutcome::Appended
    );
    assert_eq!(
        f.feed.append(request).await.unwrap(),
        AppendOutcome::AlreadyApplied
    );
    let RestoredTask::Live { view, .. } = f
        .reopen()
        .restore(&scope(), &Tid(TID.into()))
        .await
        .unwrap()
        .unwrap()
    else {
        panic!("live")
    };
    assert_eq!(display(&view).units[0].content, serde_json::json!("hello"));
    assert_eq!(view.covered_through, Some(event_id(1, 2)));
}

#[tokio::test]
async fn partial_physical_append_is_not_a_visible_cut() {
    let f = Fixture::new().await;
    let request = f.append_request(1, None, text_events(1)).await;
    let pause = f
        .storage
        .pause_after("stream.append", &f.layout.ev(&Sid(SID.into())).unwrap(), 1);
    let writer = f.reopen();
    let pending = tokio::spawn(async move { writer.append(request).await });
    pause.reached().await;
    let RestoredTask::Live { view, .. } = f
        .feed
        .restore(&scope(), &Tid(TID.into()))
        .await
        .unwrap()
        .unwrap()
    else {
        panic!("live")
    };
    assert!(display(&view).units.is_empty());
    pause.resume();
    assert_eq!(pending.await.unwrap().unwrap(), AppendOutcome::Appended);
}

#[tokio::test]
async fn append_unknown_at_each_write_resumes_same_ids_without_double_delta() {
    for lost in 1..=8 {
        let f = Fixture::new().await;
        let request = f.append_request(1, None, text_events(1)).await;
        f.storage.lose_write(lost);
        let _ = f.feed.append(request.clone()).await;
        let outcome = f.reopen().append(request).await.unwrap();
        assert!(matches!(
            outcome,
            AppendOutcome::Appended | AppendOutcome::AlreadyApplied
        ));
        let RestoredTask::Live { view, .. } = f
            .feed
            .restore(&scope(), &Tid(TID.into()))
            .await
            .unwrap()
            .unwrap()
        else {
            panic!("live")
        };
        assert_eq!(
            display(&view).units[0].content,
            serde_json::json!("hello"),
            "lost write {lost}"
        );
    }
}

#[tokio::test]
async fn completed_effect_replay_survives_advanced_outbox_and_rejects_changed_body() {
    let f = Fixture::new().await;
    let request = f.append_request(1, None, text_events(1)).await;
    f.feed.append(request.clone()).await.unwrap();
    f.commit(2, vec![]).await;
    assert_eq!(
        f.reopen().append(request.clone()).await.unwrap(),
        AppendOutcome::AlreadyApplied
    );
    let mut changed = request;
    changed.events[0].body = OutputBody::Status {
        status: TaskStatus::Failed,
        interactions: vec![],
    };
    changed.source.content_digest = kind_digest(&EffectKind::AppendEvents {
        previous: changed.previous,
        events: changed.events.clone(),
    });
    assert_eq!(
        f.feed.append(changed).await.unwrap(),
        AppendOutcome::ContentConflict
    );
}

async fn append_and_publish(f: &Fixture) -> (AppendCommittedEvents, PublishCommittedSnapshot) {
    let events = text_events(1);
    let snapshot = hello_snapshot(1, event_id(1, 2));
    let sources = f
        .commit(
            1,
            vec![
                EffectKind::AppendEvents {
                    previous: None,
                    events: events.clone(),
                },
                EffectKind::PublishSnapshot {
                    snapshot: snapshot.clone(),
                },
            ],
        )
        .await;
    let append = AppendCommittedEvents {
        source: sources[0].clone(),
        previous: None,
        events,
    };
    let publish = PublishCommittedSnapshot {
        source: sources[1].clone(),
        snapshot,
    };
    f.feed.append(append.clone()).await.unwrap();
    f.feed.publish(publish.clone()).await.unwrap();
    (append, publish)
}

#[tokio::test]
async fn snapshot_waits_for_complete_append_and_cannot_change_frozen_limits() {
    let f = Fixture::new().await;
    let events = text_events(1);
    let snapshot = hello_snapshot(1, event_id(1, 2));
    let sources = f
        .commit(
            1,
            vec![
                EffectKind::AppendEvents {
                    previous: None,
                    events: events.clone(),
                },
                EffectKind::PublishSnapshot {
                    snapshot: snapshot.clone(),
                },
            ],
        )
        .await;
    let publish = PublishCommittedSnapshot {
        source: sources[1].clone(),
        snapshot,
    };
    assert!(f.feed.publish(publish.clone()).await.is_err());
    f.feed
        .append(AppendCommittedEvents {
            source: sources[0].clone(),
            previous: None,
            events,
        })
        .await
        .unwrap();
    assert_eq!(
        f.feed.publish(publish.clone()).await.unwrap(),
        PublishOutcome::Published
    );
    assert_eq!(
        f.feed.publish(publish.clone()).await.unwrap(),
        PublishOutcome::AlreadyApplied
    );
    let mut changed = publish.snapshot;
    changed.source_commit = CommitSeq(2);
    changed.output_limits.inline_threshold_bytes /= 2;
    let sources = f
        .commit(
            2,
            vec![EffectKind::PublishSnapshot {
                snapshot: changed.clone(),
            }],
        )
        .await;
    assert!(
        f.feed
            .publish(PublishCommittedSnapshot {
                source: sources[0].clone(),
                snapshot: changed
            })
            .await
            .is_err()
    );
}

#[tokio::test]
async fn logical_gap_precedes_physical_trim_and_sparse_boundary_preserves_after_last_removed() {
    let f = Fixture::new().await;
    let RestoredTask::Live { cursor: start, .. } = f
        .feed
        .restore(&scope(), &Tid(TID.into()))
        .await
        .unwrap()
        .unwrap()
    else {
        panic!("live")
    };
    let (append, _) = append_and_publish(&f).await;
    let one = f.with_config(TaskFeedConfig {
        page_entries: 1,
        ..TaskFeedConfig::default()
    });
    let mut follow = one.follow(scope(), start.clone());
    let FollowItem::Events {
        next: after_first, ..
    } = follow.next().await.unwrap().unwrap()
    else {
        panic!("events")
    };
    let pause = f.storage.pause_after(
        "before.stream.trim",
        &f.layout.ev(&Sid(SID.into())).unwrap(),
        1,
    );
    let trimmer = f.reopen();
    let pending = tokio::spawn(async move {
        trimmer
            .trim(RetentionRequest {
                sid: Sid(SID.into()),
                keep_from: event_id(1, 2),
            })
            .await
    });
    pause.reached().await;
    let native = StreamStorePlugin::range(
        &f.storage.model,
        &f.layout.ev(&Sid(SID.into())).unwrap(),
        Bound::Unbounded,
        Bound::Unbounded,
        NonZeroU32::new(8).unwrap(),
        false,
    )
    .await
    .unwrap();
    assert_eq!(native.entries.len(), 2);
    assert_eq!(
        f.feed.validate_resume(&scope(), &start).await.unwrap(),
        ResumeCheck::Gap
    );
    assert_eq!(
        f.feed
            .validate_resume(&scope(), &after_first)
            .await
            .unwrap(),
        ResumeCheck::Valid
    );
    pause.resume();
    assert_eq!(pending.await.unwrap().unwrap(), TrimOutcome::Trimmed);
    assert_eq!(
        f.feed.append(append).await.unwrap(),
        AppendOutcome::AlreadyApplied
    );
    let RestoredTask::Live { view, .. } = f
        .feed
        .restore(&scope(), &Tid(TID.into()))
        .await
        .unwrap()
        .unwrap()
    else {
        panic!("live")
    };
    assert_eq!(display(&view).units[0].content, serde_json::json!("hello"));
}

#[tokio::test]
async fn follow_rechecks_logical_boundary_after_native_read() {
    let f = Fixture::new().await;
    let RestoredTask::Live { cursor, .. } = f
        .feed
        .restore(&scope(), &Tid(TID.into()))
        .await
        .unwrap()
        .unwrap()
    else {
        panic!("live")
    };
    append_and_publish(&f).await;
    let pause = f
        .storage
        .pause_after("stream.range", &f.layout.ev(&Sid(SID.into())).unwrap(), 1);
    let mut follow = f.feed.follow(scope(), cursor);
    let delivery = tokio::spawn(async move { follow.next().await.unwrap().unwrap() });
    pause.reached().await;
    f.feed
        .trim(RetentionRequest {
            sid: Sid(SID.into()),
            keep_from: event_id(1, 2),
        })
        .await
        .unwrap();
    pause.resume();
    assert_eq!(delivery.await.unwrap(), FollowItem::Gap);
}

#[tokio::test]
async fn new_empty_task_is_restorable_after_other_task_history_is_trimmed() {
    let f = Fixture::new().await;
    append_and_publish(&f).await;
    f.feed
        .trim(RetentionRequest {
            sid: Sid(SID.into()),
            keep_from: event_id(1, 2),
        })
        .await
        .unwrap();
    let mut next = task();
    next.tid = Tid(format!("{SID}.next"));
    f.storage
        .create_document(
            &f.layout.task(&next.tid).unwrap(),
            &serde_json::to_value(&next).unwrap(),
        )
        .await
        .unwrap();
    let RestoredTask::Live { view, .. } =
        f.feed.restore(&scope(), &next.tid).await.unwrap().unwrap()
    else {
        panic!("live")
    };
    assert!(display(&view).units.is_empty());
}

#[tokio::test]
async fn archive_success_alone_does_not_expose_a_terminal_task() {
    use bcs_service_api::port::repo::distributed::result::ResultStorePort;
    let f = Fixture::new().await;
    let record = FinalTaskRecord {
        schema_version: 1,
        scope: scope(),
        sid: Sid(SID.into()),
        tid: Tid(TID.into()),
        status: TerminalStatus::Completed,
        snapshot: task().content.snapshot,
        result: Payload::Inline(serde_json::json!({"done":true})),
        source_commit: CommitSeq(1),
        finalized_at: TimestampMillis(42),
    };
    let sources = f
        .commit(
            1,
            vec![EffectKind::SaveResult {
                record: record.clone(),
            }],
        )
        .await;
    f.archive
        .save_final(sources[0].clone(), record)
        .await
        .unwrap();
    assert!(matches!(
        f.feed.restore(&scope(), &Tid(TID.into())).await.unwrap(),
        Some(RestoredTask::Live { .. })
    ));
}

#[tokio::test]
async fn sparse_task_list_pages_advance_scanned_members_and_bind_scope() {
    let f = Fixture::new().await;
    let key = f.layout.tasks(&scope()).unwrap();
    f.storage
        .add(
            &key,
            &[ZSetEntry {
                member: format!("{SID}.000-missing"),
                score: 0.0,
            }],
            ZAddMode::InsertOnly,
        )
        .await
        .unwrap();
    let filter = TaskFilter {
        sid: None,
        statuses: vec![],
    };
    let first = f
        .feed
        .list(
            &scope(),
            filter.clone(),
            PageRequest {
                after: None,
                limit: NonZeroU32::MIN,
            },
        )
        .await
        .unwrap();
    assert!(first.items.is_empty());
    assert!(first.next.is_some());
    let second = f
        .feed
        .list(
            &scope(),
            filter.clone(),
            PageRequest {
                after: first.next.clone(),
                limit: NonZeroU32::MIN,
            },
        )
        .await
        .unwrap();
    assert_eq!(second.items.len(), 1);
    let mut other = scope();
    other.tenant = TenantId("other".into());
    assert!(
        f.feed
            .list(
                &other,
                filter,
                PageRequest {
                    after: first.next,
                    limit: NonZeroU32::MIN
                }
            )
            .await
            .is_err()
    );
}

async fn append_referenced(f: &Fixture, count: usize, length: usize) -> TaskCursor {
    use bcs_service_api::port::repo::distributed::result::BlobStorePort;
    let RestoredTask::Live { cursor, .. } = f
        .feed
        .restore(&scope(), &Tid(TID.into()))
        .await
        .unwrap()
        .unwrap()
    else {
        panic!("live")
    };
    let mut events = Vec::new();
    for index in 0..count {
        let bytes = serde_json::to_vec(&"x".repeat(length)).unwrap();
        let reference = bcs_dist_store::archive::blob_reference(&format!("large-{index}"), &bytes);
        f.archive
            .put_immutable(reference.clone(), bytes)
            .await
            .unwrap();
        events.push(OutputEvent {
            id: event_id(1, index as u32 + 1),
            tid: Tid(TID.into()),
            body: OutputBody::Artifact {
                source: CorrelationKey("source".into()),
                artifact_id: ArtifactId(format!("artifact-{index}")),
                class: OutputClass::Output,
                update: ArtifactUpdate::Upsert {
                    content: Payload::Ref(reference),
                },
            },
        });
    }
    let request = f.append_request(1, None, events).await;
    f.feed.append(request).await.unwrap();
    cursor
}

#[tokio::test]
async fn follow_resolves_references_and_does_not_skip_events_at_resolved_byte_limit() {
    let f = Fixture::new().await;
    let cursor = append_referenced(&f, 2, 900_000).await;
    let mut follow = f.feed.follow(scope(), cursor);
    for expected in 1..=2 {
        let FollowItem::Events { items, .. } = follow.next().await.unwrap().unwrap() else {
            panic!("events")
        };
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].id, event_id(1, expected));
        let OutputBody::Artifact {
            update:
                ArtifactUpdate::Upsert {
                    content: Payload::Inline(value),
                },
            ..
        } = &items[0].body
        else {
            panic!("resolved output")
        };
        assert_eq!(value.as_str().unwrap().len(), 900_000);
    }
}

#[tokio::test]
async fn follow_rejects_missing_and_corrupt_blob_before_delivery() {
    use bcs_db_api::{DbPlugin, DbStatement};
    for corrupt in [false, true] {
        let f = Fixture::new().await;
        let cursor = append_referenced(&f, 1, 5).await;
        let sql = if corrupt {
            "UPDATE bcs_dist_immutable_objects SET content_bytes = X'626164' WHERE object_key = 'large-0'"
        } else {
            "DELETE FROM bcs_dist_immutable_objects WHERE object_key = 'large-0'"
        };
        f.db.execute(DbStatement::new(sql)).await.unwrap();
        let mut follow = f.feed.follow(scope(), cursor);
        assert!(follow.next().await.unwrap().is_err());
        assert!(follow.next().await.is_none());
    }
}

#[tokio::test]
async fn publish_and_trim_unknown_writes_recover_from_durable_progress() {
    for lost in 1..=6 {
        let f = Fixture::new().await;
        let events = text_events(1);
        let snapshot = hello_snapshot(1, event_id(1, 2));
        let sources = f
            .commit(
                1,
                vec![
                    EffectKind::AppendEvents {
                        previous: None,
                        events: events.clone(),
                    },
                    EffectKind::PublishSnapshot {
                        snapshot: snapshot.clone(),
                    },
                ],
            )
            .await;
        f.feed
            .append(AppendCommittedEvents {
                source: sources[0].clone(),
                previous: None,
                events,
            })
            .await
            .unwrap();
        let publish = PublishCommittedSnapshot {
            source: sources[1].clone(),
            snapshot,
        };
        f.storage.lose_write(lost.min(2));
        let _ = f.feed.publish(publish.clone()).await;
        assert!(matches!(
            f.reopen().publish(publish).await.unwrap(),
            PublishOutcome::Published | PublishOutcome::AlreadyApplied
        ));
        f.storage.lose_write(lost);
        let request = RetentionRequest {
            sid: Sid(SID.into()),
            keep_from: event_id(1, 2),
        };
        let _ = f.feed.trim(request.clone()).await;
        assert!(matches!(
            f.reopen().trim(request).await.unwrap(),
            TrimOutcome::Trimmed | TrimOutcome::AlreadyTrimmed
        ));
        let RestoredTask::Live { view, .. } = f
            .feed
            .restore(&scope(), &Tid(TID.into()))
            .await
            .unwrap()
            .unwrap()
        else {
            panic!("live")
        };
        assert_eq!(display(&view).units[0].content, serde_json::json!("hello"));
    }
}

#[tokio::test]
async fn terminal_visibility_follows_archival_and_second_commit_output() {
    use bcs_service_api::port::repo::distributed::result::{ResultStorePort, SavedResult};
    let f = Fixture::new().await;
    let record = FinalTaskRecord {
        schema_version: 1,
        scope: scope(),
        sid: Sid(SID.into()),
        tid: Tid(TID.into()),
        status: TerminalStatus::Completed,
        snapshot: task().content.snapshot,
        result: Payload::Inline(serde_json::json!({"done":true})),
        source_commit: CommitSeq(1),
        finalized_at: TimestampMillis(42),
    };
    let sources = f
        .commit(
            1,
            vec![EffectKind::SaveResult {
                record: record.clone(),
            }],
        )
        .await;
    let SavedResult::Saved(reference) = f
        .archive
        .save_final(sources[0].clone(), record)
        .await
        .unwrap()
    else {
        panic!("saved")
    };
    let event = OutputEvent {
        id: event_id(2, 1),
        tid: Tid(TID.into()),
        body: OutputBody::Terminal {
            status: TerminalStatus::Completed,
            result_ref: reference.clone(),
        },
    };
    let mut snapshot = task();
    snapshot.source_commit = CommitSeq(2);
    snapshot.snapshot_event_id = Some(event.id);
    snapshot.content.status = TaskStatus::Completed;
    snapshot.content.result_ref = Some(reference);
    let sources = f
        .commit(
            2,
            vec![
                EffectKind::AppendEvents {
                    previous: None,
                    events: vec![event.clone()],
                },
                EffectKind::PublishSnapshot {
                    snapshot: snapshot.clone(),
                },
            ],
        )
        .await;
    f.feed
        .append(AppendCommittedEvents {
            source: sources[0].clone(),
            previous: None,
            events: vec![event],
        })
        .await
        .unwrap();
    assert!(matches!(
        f.feed.restore(&scope(), &Tid(TID.into())).await.unwrap(),
        Some(RestoredTask::Final(_))
    ));
    f.feed
        .publish(PublishCommittedSnapshot {
            source: sources[1].clone(),
            snapshot,
        })
        .await
        .unwrap();
    assert!(matches!(
        f.reopen()
            .restore(&scope(), &Tid(TID.into()))
            .await
            .unwrap(),
        Some(RestoredTask::Final(_))
    ));
}

#[tokio::test]
async fn follow_delivers_a_unit_at_the_exact_one_mib_json_boundary() {
    let f = Fixture::new().await;
    let cursor = append_referenced(&f, 1, 1024 * 1024 - 2).await;
    let mut follow = f.feed.follow(scope(), cursor);
    let FollowItem::Events { items, .. } = follow.next().await.unwrap().unwrap() else {
        panic!("events")
    };
    let OutputBody::Artifact {
        update: ArtifactUpdate::Upsert {
            content: Payload::Inline(value),
        },
        ..
    } = &items[0].body
    else {
        panic!("resolved")
    };
    assert_eq!(serde_json::to_vec(value).unwrap().len(), 1024 * 1024);
}

#[tokio::test]
async fn checkpoint_evidence_read_obeys_the_configured_io_timeout() {
    use std::time::{Duration, Instant};
    let f = Fixture::new().await;
    let request = f.append_request(1, None, text_events(1)).await;
    let _pause = f.storage.pause_after(
        "document.read",
        &f.layout.ckpt(&Sid(SID.into())).unwrap(),
        1,
    );
    let reader = f.with_config(TaskFeedConfig {
        io_timeout: Duration::from_millis(30),
        ..TaskFeedConfig::default()
    });
    let started = Instant::now();
    assert!(matches!(
        reader.append(request.clone()).await,
        Err(bcs_service_api::port::repo::distributed::common::StoreError::NotApplied { .. })
    ));
    assert!(started.elapsed() < Duration::from_millis(300));
    assert_eq!(
        f.reopen().append(request).await.unwrap(),
        AppendOutcome::Appended
    );
}

#[tokio::test]
async fn cas_episode_bounds_slow_io_before_the_first_write() {
    use std::time::{Duration, Instant};
    let f = Fixture::new().await;
    let request = f.append_request(1, None, text_events(1)).await;
    let _pause = f.storage.pause_after(
        "document.read",
        &f.layout.output_head(&Sid(SID.into())).unwrap(),
        1,
    );
    let started = Instant::now();
    assert!(matches!(
        f.feed.append(request.clone()).await,
        Err(bcs_service_api::port::repo::distributed::common::StoreError::OutcomeUnknown { .. })
    ));
    assert!(started.elapsed() < Duration::from_millis(800));
    assert_eq!(
        f.reopen().append(request).await.unwrap(),
        AppendOutcome::Appended
    );
}

#[tokio::test]
async fn restore_and_list_enforce_the_whole_read_deadline() {
    use std::time::{Duration, Instant};
    for list in [false, true] {
        let f = Fixture::new().await;
        let reader = f.with_config(TaskFeedConfig {
            scan_duration: Duration::from_millis(30),
            ..TaskFeedConfig::default()
        });
        let _pause = f.storage.pause_after(
            "document.read",
            &f.layout.task(&Tid(TID.into())).unwrap(),
            1,
        );
        let started = Instant::now();
        let result = if list {
            reader
                .list(
                    &scope(),
                    TaskFilter {
                        sid: None,
                        statuses: vec![],
                    },
                    PageRequest {
                        limit: NonZeroU32::new(10).unwrap(),
                        after: None,
                    },
                )
                .await
                .map(|_| ())
        } else {
            reader.restore(&scope(), &Tid(TID.into())).await.map(|_| ())
        };
        assert!(matches!(
            result,
            Err(bcs_service_api::port::repo::distributed::common::StoreError::NotApplied { .. })
        ));
        assert!(started.elapsed() < Duration::from_millis(300));
    }
}

#[tokio::test]
async fn definite_later_write_failures_preserve_the_partial_effect_as_unknown() {
    use bcs_service_api::port::repo::distributed::common::StoreError;
    for failed in 2..=7 {
        let f = Fixture::new().await;
        let request = f.append_request(1, None, text_events(1)).await;
        f.storage.fail_write(failed);
        assert!(
            matches!(
                f.feed.append(request.clone()).await,
                Err(StoreError::OutcomeUnknown { .. })
            ),
            "append write {failed}"
        );
        assert!(matches!(
            f.reopen().append(request).await.unwrap(),
            AppendOutcome::Appended | AppendOutcome::AlreadyApplied
        ));
    }
    let f = Fixture::new().await;
    let request = f.append_request(1, None, text_events(1)).await;
    f.feed.append(request).await.unwrap();
    let mut snapshot = hello_snapshot(2, event_id(1, 2));
    snapshot.updated_at = TimestampMillis(2);
    let sources = f
        .commit(
            2,
            vec![EffectKind::PublishSnapshot {
                snapshot: snapshot.clone(),
            }],
        )
        .await;
    let request = PublishCommittedSnapshot {
        source: sources[0].clone(),
        snapshot,
    };
    f.storage.fail_write(2); // Task publication succeeds; its completion row fails.
    assert!(matches!(
        f.feed.publish(request.clone()).await,
        Err(StoreError::OutcomeUnknown { .. })
    ));
    assert_eq!(
        f.reopen().publish(request).await.unwrap(),
        PublishOutcome::AlreadyApplied
    );
}

#[tokio::test]
async fn trim_definite_later_failures_are_unknown_after_durable_progress() {
    use bcs_service_api::port::repo::distributed::common::StoreError;
    for failed in 2..=6 {
        let f = Fixture::new().await;
        append_and_publish(&f).await;
        f.storage.fail_write(failed);
        let request = RetentionRequest {
            sid: Sid(SID.into()),
            keep_from: event_id(1, 2),
        };
        assert!(
            matches!(
                f.feed.trim(request.clone()).await,
                Err(StoreError::OutcomeUnknown { .. })
            ),
            "trim write {failed}"
        );
        assert!(matches!(
            f.reopen().trim(request).await.unwrap(),
            TrimOutcome::Trimmed | TrimOutcome::AlreadyTrimmed
        ));
    }
}

#[tokio::test]
async fn referenced_json_can_expand_beyond_the_former_unit_byte_quota() {
    use bcs_service_api::port::repo::distributed::result::BlobStorePort;
    let f = Fixture::new().await;
    // The parser expands these short numbers in the public compact encoding.
    let bytes = format!("[{}]", vec!["1e9"; 81_000].join(",")).into_bytes();
    let value: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert!(bytes.len() < 1024 * 1024);
    assert!(serde_json::to_vec(&value).unwrap().len() > 1024 * 1024);
    let reference = bcs_dist_store::archive::blob_reference("numbers", &bytes);
    f.archive
        .put_immutable(reference.clone(), bytes)
        .await
        .unwrap();
    let mut event = text_events(1).remove(0);
    if let OutputBody::Artifact { update, .. } = &mut event.body {
        *update = ArtifactUpdate::Upsert {
            content: Payload::Ref(reference),
        };
    }
    let request = f.append_request(1, None, vec![event]).await;
    assert_eq!(f.feed.append(request).await.unwrap(), AppendOutcome::Appended);
    let Some(RestoredTask::Live { view, .. }) = f.feed.restore(&scope(), &Tid(TID.into())).await.unwrap() else {
        panic!("live")
    };
    assert_eq!(display(&view).units[0].content, value);
}

#[tokio::test]
async fn follow_and_restore_deliver_a_unit_larger_than_the_former_output_quotas() {
    let f = Fixture::new().await;
    let length = 9 * 1024 * 1024;
    let cursor = append_referenced(&f, 1, length).await;
    let mut follow = f.feed.follow(scope(), cursor);
    let FollowItem::Events { items, next } = follow.next().await.unwrap().unwrap() else {
        panic!("events")
    };
    assert_eq!(items.len(), 1);
    assert_eq!(items[0].id, event_id(1, 1));
    let OutputBody::Artifact { update: ArtifactUpdate::Upsert { content: Payload::Inline(value) }, .. } = &items[0].body else {
        panic!("resolved output")
    };
    assert_eq!(value.as_str().unwrap().len(), length);
    assert_eq!(f.feed.validate_resume(&scope(), &next).await.unwrap(), ResumeCheck::Valid);
    let Some(RestoredTask::Live { view, .. }) = f.feed.restore(&scope(), &Tid(TID.into())).await.unwrap() else {
        panic!("live")
    };
    assert_eq!(display(&view).units[0].content.as_str().unwrap().len(), length);
}
